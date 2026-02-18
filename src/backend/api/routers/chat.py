"""
Chat API routes with SSE streaming support.

This module provides the chat streaming endpoint using the
DataAssistant + process_query() pattern:
1. Classifies intent (data query, refinement, or conversation)
2. For data queries: calls process_query() directly
3. Supports conversational refinements like "show me for 90 days"
4. Handles clarification requests for missing parameters
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator

from api.dependencies import get_optional_user_id
from api.step_events import (
    clear_step_queue,
    set_request_user_id,
    set_step_queue,
)
from api.workflow_cache import (
    get_clarification_context,
    store_clarification_context,
)
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from models import ClarificationRequest, NL2SQLRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _sanitized_error_event(error: Exception) -> str:
    """Build a sanitized SSE error payload with a correlation ID.

    Logs the full exception server-side and returns a generic message
    to the client so internal details are never leaked.
    """
    correlation_id = uuid.uuid4().hex[:12]
    logger.error("SSE error [%s]: %s", correlation_id, error, exc_info=True)
    payload = {
        "error": "An internal error occurred. Please try again.",
        "correlation_id": correlation_id,
        "done": True,
    }
    return f"data: {json.dumps(payload)}\n\n"


def _format_step_event(step_event: dict) -> dict:
    """Format a step event dict for SSE emission."""
    result: dict = {"step": step_event.get("step"), "done": False}
    if "status" in step_event:
        result["status"] = step_event["status"]
    if "duration_ms" in step_event:
        result["duration_ms"] = step_event["duration_ms"]
    return result


async def generate_clarification_response_stream(
    clarification_ctx: ClarificationRequest,
    message: str,
    request_id: str,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response for a clarification continuation."""
    del request_id  # Used by caller for lookup only
    from api.session_manager import get_assistant, store_assistant
    from config.settings import get_settings
    from entities.nl2sql_controller.pipeline import process_query
    from entities.shared.protocols import QueueReporter
    from entities.workflow.clients import create_pipeline_clients

    step_queue: asyncio.Queue[dict] = asyncio.Queue()
    set_step_queue(step_queue)
    set_request_user_id(user_id)

    try:
        settings = get_settings()
        reporter = QueueReporter(step_queue)
        clients = create_pipeline_clients(settings, reporter=reporter)

        # Build request incorporating the user's clarification answer
        request = NL2SQLRequest(
            user_query=message,
            is_refinement=True,
            previous_template_json=clarification_ctx.template_json,
            base_params=clarification_ctx.extracted_parameters,
            param_overrides={
                clarification_ctx.parameter_name: message,
            },
        )

        result = await process_query(request, clients)

        # Drain step events
        while not step_queue.empty():
            try:
                evt = step_queue.get_nowait()
                yield (f"data: {json.dumps(_format_step_event(evt))}\n\n")
            except asyncio.QueueEmpty:
                break

        foundry_thread_id = thread_id

        if isinstance(result, ClarificationRequest):
            # Another clarification needed
            new_id = f"clarify_{uuid.uuid4().hex[:12]}"
            store_clarification_context(new_id, result)

            clarification_data = {
                "needs_clarification": True,
                "clarification": {
                    "request_id": new_id,
                    "parameter_name": result.parameter_name,
                    "prompt": result.prompt,
                    "allowed_values": result.allowed_values,
                },
                "done": False,
            }
            yield f"data: {json.dumps(clarification_data)}\n\n"
            yield (f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n")
        else:
            # NL2SQLResponse
            response = result
            tool_call = {
                "tool_name": "nl2sql_query",
                "tool_call_id": (f"nl2sql_clarification_{id(response)}"),
                "args": {},
                "result": {
                    "sql_query": response.sql_query,
                    "sql_response": response.sql_response,
                    "columns": response.columns,
                    "row_count": response.row_count,
                    "confidence_score": (response.confidence_score),
                    "query_source": response.query_source,
                    "error": response.error,
                    "observations": None,
                    "needs_clarification": (response.needs_clarification),
                    "clarification": (
                        response.clarification.model_dump() if response.clarification else None
                    ),
                    "defaults_used": response.defaults_used,
                    "suggestions": [s.model_dump() for s in response.suggestions],
                },
            }

            yield (
                "data: "
                f"{json.dumps({'tool_call': tool_call, 'done': False, 'thread_id': foundry_thread_id})}"
                "\n\n"
            )
            yield (f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n")

            # Update assistant context if cached
            assistant = get_assistant(foundry_thread_id)
            if assistant:
                assistant.update_context(
                    response,
                    response.template_json,
                    response.extracted_params,
                )
                assistant.enrich_response(response)
                if foundry_thread_id:
                    store_assistant(foundry_thread_id, assistant)

        yield (f"data: {json.dumps({'done': True, 'thread_id': foundry_thread_id})}\n\n")

    except (ValueError, RuntimeError, OSError, TypeError) as e:
        logger.error("Clarification error: %s", e, exc_info=True)
        yield _sanitized_error_event(e)
    finally:
        clear_step_queue()


async def generate_orchestrator_streaming_response(
    message: str,
    thread_id: str | None = None,
    user_id: str | None = None,
    title: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response using the DataAssistant + process_query()."""
    del title  # TODO: Use for thread metadata
    import time

    from agent_framework import ChatAgent
    from agent_framework_azure_ai import AzureAIClient
    from api.session_manager import get_assistant, store_assistant
    from azure.identity.aio import DefaultAzureCredential
    from config.settings import get_settings
    from entities.assistant import DataAssistant, load_assistant_prompt
    from entities.nl2sql_controller.pipeline import process_query
    from entities.shared.protocols import QueueReporter
    from entities.workflow.clients import create_pipeline_clients

    step_queue: asyncio.Queue[dict] = asyncio.Queue()
    set_step_queue(step_queue)
    set_request_user_id(user_id)

    try:
        settings = get_settings()

        # Get or create DataAssistant for this session
        assistant = get_assistant(thread_id)

        if assistant is None:
            endpoint = settings.azure_ai_project_endpoint
            if not endpoint:
                raise ValueError("AZURE_AI_PROJECT_ENDPOINT not set")

            client_id = settings.azure_client_id
            if client_id:
                credential = DefaultAzureCredential(
                    managed_identity_client_id=client_id,
                )
            else:
                credential = DefaultAzureCredential()

            orchestrator_model = (
                settings.azure_ai_orchestrator_model or settings.azure_ai_model_deployment_name
            )

            ai_client = AzureAIClient(
                project_endpoint=endpoint,
                credential=credential,
                model_deployment_name=orchestrator_model,
                use_latest_version=True,
            )

            agent = ChatAgent(
                name="DataAssistant",
                instructions=load_assistant_prompt(),
                chat_client=ai_client,
            )

            assistant = DataAssistant(agent, thread_id)
            logger.info(
                "Created new DataAssistant for thread_id=%s",
                thread_id,
            )

        # Step 1: Classify intent
        yield (f"data: {json.dumps({'step': 'Analyzing request...', 'status': 'started'})}\n\n")

        classify_start = time.time()
        classification = await assistant.classify_intent(message)
        classify_ms = int((time.time() - classify_start) * 1000)

        yield (
            "data: "
            f"{json.dumps({'step': 'Analyzing request...', 'status': 'completed', 'duration_ms': classify_ms})}"
            "\n\n"
        )

        if classification.intent == "conversation":
            yield (
                f"data: {json.dumps({'step': 'Generating response...', 'status': 'started'})}\n\n"
            )
            convo_start = time.time()
            response_text = await assistant.handle_conversation(message)
            convo_ms = int((time.time() - convo_start) * 1000)
            yield (
                "data: "
                f"{json.dumps({'step': 'Generating response...', 'status': 'completed', 'duration_ms': convo_ms})}"
                "\n\n"
            )

            output = {
                "text": response_text,
                "thread_id": assistant.thread_id,
            }
            yield f"data: {json.dumps(output)}\n\n"
        else:
            # Data query or refinement
            nl2sql_request = assistant.build_nl2sql_request(classification)

            reporter = QueueReporter(step_queue)
            clients = create_pipeline_clients(settings, reporter=reporter)

            result = await process_query(nl2sql_request, clients)

            # Drain step events
            while not step_queue.empty():
                try:
                    evt = step_queue.get_nowait()
                    yield (f"data: {json.dumps(_format_step_event(evt))}\n\n")
                except asyncio.QueueEmpty:
                    break

            if isinstance(result, ClarificationRequest):
                req_id = f"clarify_{uuid.uuid4().hex[:12]}"
                store_clarification_context(req_id, result)

                clarification_data = {
                    "needs_clarification": True,
                    "clarification": {
                        "request_id": req_id,
                        "parameter_name": result.parameter_name,
                        "prompt": result.prompt,
                        "allowed_values": (result.allowed_values),
                    },
                    "thread_id": assistant.thread_id,
                    "done": False,
                }
                yield (f"data: {json.dumps(clarification_data)}\n\n")
                yield (f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n")

                if assistant.thread_id:
                    store_assistant(assistant.thread_id, assistant)

                yield (f"data: {json.dumps({'done': True, 'thread_id': assistant.thread_id})}\n\n")
                return

            # NL2SQLResponse
            response = result
            assistant.update_context(
                response,
                response.template_json,
                response.extracted_params,
            )
            assistant.enrich_response(response)

            if assistant.thread_id:
                store_assistant(assistant.thread_id, assistant)

            output = assistant.render_response(response)
            yield f"data: {json.dumps(output)}\n\n"

        yield (f"data: {json.dumps({'done': True, 'thread_id': assistant.thread_id})}\n\n")

    except Exception as e:
        logger.error("DataAssistant error: %s", e, exc_info=True)
        yield _sanitized_error_event(e)

    finally:
        clear_step_queue()


@router.get("/stream")
async def chat_stream(
    message: str = Query(..., description="User message"),
    thread_id: str | None = Query(None, description="Thread ID"),
    title: str | None = Query(None, description="Thread title"),
    request_id: str | None = Query(None, description="Request ID for clarification"),
    user_id: str | None = Depends(get_optional_user_id),
) -> StreamingResponse:
    """SSE streaming chat with DataAssistant architecture."""
    if request_id:
        clarification_ctx = get_clarification_context(request_id)
        if clarification_ctx:
            return StreamingResponse(
                generate_clarification_response_stream(
                    clarification_ctx,
                    message,
                    request_id,
                    thread_id,
                    user_id,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
        logger.warning(
            "No clarification context for request_id=%s",
            request_id,
        )

    return StreamingResponse(
        generate_orchestrator_streaming_response(message, thread_id, user_id, title),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
