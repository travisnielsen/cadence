"""
Chat API routes with SSE streaming support.

This module provides the chat streaming endpoint using the ConversationOrchestrator pattern:
1. Classifies intent (data query, refinement, or conversation)
2. For data queries: invokes the NL2SQL workflow
3. Supports conversational refinements like "show me for 90 days"
4. Handles clarification requests for missing parameters
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from agent_framework import (
    ExecutorCompletedEvent,
    ExecutorInvokedEvent,
    RequestInfoEvent,
    WorkflowOutputEvent,
    WorkflowRunState,
    WorkflowStatusEvent,
)
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from opentelemetry import context as otel_context
from opentelemetry import trace

if TYPE_CHECKING:
    from agent_framework import Workflow

from api.dependencies import get_optional_user_id
from api.step_events import clear_step_queue, set_request_user_id, set_step_queue
from api.workflow_cache import get_paused_workflow, store_paused_workflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Get tracer for workflow-level spans
tracer = trace.get_tracer(__name__)


async def generate_clarification_response_stream(
    workflow: "Workflow",
    message: str,
    request_id: str,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream response for a clarification continuation using send_responses_streaming.

    This is called when the user responds to a clarification request.
    The workflow resumes from where it left off, using the saved template context.

    Args:
        workflow: The Workflow instance
        message: User's clarification response
        request_id: The request_id from the original RequestInfoEvent
        thread_id: Foundry thread ID
        user_id: User ID for context
    """
    # Create a parent span for the clarification response
    workflow_span = tracer.start_span(
        "workflow.clarification_response",
        attributes={
            "workflow.message": message[:100],
            "workflow.request_id": request_id,
            "workflow.thread_id": thread_id or "unknown",
            "workflow.user_id": user_id or "anonymous",
        },
    )
    context_token = otel_context.attach(trace.set_span_in_context(workflow_span))

    # Set up step event queue for tool-level progress
    step_queue: asyncio.Queue = asyncio.Queue()
    set_step_queue(step_queue)
    logger.info("Step queue created for clarification response")

    # Set user_id in context
    set_request_user_id(user_id)

    try:
        logger.info(
            "Sending clarification response for request_id=%s: %s", request_id, message[:100]
        )

        # Send the user's response back to the workflow
        # The key is the request_id, the value is the user's response
        responses = {request_id: message}

        output_received = False
        foundry_thread_id = thread_id

        async for event in workflow.send_responses_streaming(responses):
            logger.info("Received workflow event from clarification: %s", type(event).__name__)

            if isinstance(event, WorkflowOutputEvent):
                # This is the NL2SQL response from the workflow
                output_data = event.data
                try:
                    # Parse the response
                    if isinstance(output_data, str):
                        response_data = json.loads(output_data)
                    else:
                        response_data = output_data

                    # Import NL2SQLResponse
                    from models import NL2SQLResponse

                    # Check if this looks like an NL2SQLResponse (has sql_query field)
                    if "sql_query" in response_data or "sql_response" in response_data:
                        response = NL2SQLResponse.model_validate(response_data)
                        logger.info("Parsed NL2SQLResponse: rows=%d", response.row_count)

                        # Build tool_call format for frontend
                        tool_call = {
                            "tool_name": "nl2sql_query",
                            "tool_call_id": f"nl2sql_clarification_{id(response)}",
                            "args": {},
                            "result": {
                                "sql_query": response.sql_query,
                                "sql_response": response.sql_response,
                                "columns": response.columns,
                                "row_count": response.row_count,
                                "confidence_score": response.confidence_score,
                                "used_cached_query": response.used_cached_query,
                                "query_source": response.query_source,
                                "error": response.error,
                                "observations": None,
                                "needs_clarification": response.needs_clarification,
                                "clarification": response.clarification.model_dump()
                                if response.clarification
                                else None,
                                "defaults_used": response.defaults_used,
                            },
                        }

                        yield f"data: {json.dumps({'tool_call': tool_call, 'done': False, 'thread_id': foundry_thread_id})}\n\n"
                        logger.info("Emitted NL2SQLResponse tool_call from clarification")
                        output_received = True

                        yield f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n"
                    else:
                        # Legacy format: look for text/tool_call
                        output_text = response_data.get("text", "")
                        foundry_thread_id = response_data.get("thread_id") or foundry_thread_id

                        tool_call = response_data.get("tool_call")
                        if tool_call:
                            yield f"data: {json.dumps({'tool_call': tool_call, 'done': False})}\n\n"
                            logger.info(
                                "Emitted tool_call from clarification: %s",
                                tool_call.get("tool_name"),
                            )
                            output_received = True
                        elif output_text:
                            chunk_size = 50
                            for i in range(0, len(output_text), chunk_size):
                                chunk = output_text[i : i + chunk_size]
                                yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                                await asyncio.sleep(0.01)
                            output_received = True

                        yield f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n"

                except json.JSONDecodeError:
                    output_text = str(output_data)
                    chunk_size = 50
                    for i in range(0, len(output_text), chunk_size):
                        chunk = output_text[i : i + chunk_size]
                        yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                        await asyncio.sleep(0.01)
                    output_received = True

            elif isinstance(event, WorkflowStatusEvent):
                if event.state == WorkflowRunState.IDLE:
                    logger.info("Workflow completed after clarification")

            elif isinstance(event, RequestInfoEvent):
                # Another clarification request (e.g., multiple missing parameters)
                logger.info(
                    "Received another RequestInfoEvent with request_id: %s", event.request_id
                )

                from models import ClarificationRequest

                if event.data is not None and isinstance(event.data, ClarificationRequest):
                    # Store the paused workflow for the follow-up clarification
                    store_paused_workflow(event.request_id, workflow)
                    logger.info(
                        "Stored paused workflow for follow-up request_id=%s", event.request_id
                    )

                    clarification_data = {
                        "needs_clarification": True,
                        "clarification": {
                            "request_id": event.request_id,
                            "parameter_name": event.data.parameter_name,
                            "prompt": event.data.prompt,
                            "allowed_values": event.data.allowed_values,
                        },
                        "done": False,
                    }
                    yield f"data: {json.dumps(clarification_data)}\n\n"
                    logger.info("Sent follow-up clarification: %s", event.data.prompt)
                    output_received = True
                    yield f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n"

        if not output_received:
            yield f"data: {json.dumps({'content': 'No response generated', 'done': False})}\n\n"

        yield f"data: {json.dumps({'done': True, 'thread_id': foundry_thread_id})}\n\n"

        workflow_span.set_status(trace.StatusCode.OK)

    except (ValueError, RuntimeError, OSError, TypeError) as e:
        logger.error("Clarification workflow error: %s", e, exc_info=True)
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"

        workflow_span.set_status(trace.StatusCode.ERROR, str(e))
        workflow_span.record_exception(e)
    finally:
        clear_step_queue()
        workflow_span.end()
        otel_context.detach(context_token)


async def generate_orchestrator_streaming_response(
    message: str,
    thread_id: str | None = None,
    user_id: str | None = None,
    title: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream response using the ConversationOrchestrator pattern.

    This new architecture:
    1. ConversationOrchestrator owns the Foundry thread and classifies intent
    2. For data queries/refinements: invokes the NL2SQL workflow
    3. Renders results back to the user

    Supports conversational refinements like "show me for 90 days".
    """
    del title  # TODO: Use for thread metadata in orchestrator
    import os

    from agent_framework_azure_ai import AzureAIClient
    from api.session_manager import get_orchestrator, store_orchestrator
    from azure.identity.aio import DefaultAzureCredential
    from entities.orchestrator import ConversationOrchestrator
    from entities.workflow import create_nl2sql_workflow

    # Set up step event queue for tool-level progress
    step_queue: asyncio.Queue = asyncio.Queue()
    set_step_queue(step_queue)
    set_request_user_id(user_id)

    try:
        # Get or create orchestrator for this session
        orchestrator = get_orchestrator(thread_id)

        if orchestrator is None:
            # Create new orchestrator
            endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
            if not endpoint:
                raise ValueError("AZURE_AI_PROJECT_ENDPOINT not set")

            client_id = os.getenv("AZURE_CLIENT_ID")
            if client_id:
                credential = DefaultAzureCredential(managed_identity_client_id=client_id)
            else:
                credential = DefaultAzureCredential()

            # Use orchestrator-specific model, falling back to default
            orchestrator_model = os.getenv(
                "AZURE_AI_ORCHESTRATOR_MODEL", os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
            )

            client = AzureAIClient(
                project_endpoint=endpoint,
                credential=credential,
                model_deployment_name=orchestrator_model,
                use_latest_version=True,
            )
            logger.info("Orchestrator using model: %s", orchestrator_model)

            orchestrator = ConversationOrchestrator(client, thread_id)
            logger.info("Created new ConversationOrchestrator for thread_id=%s", thread_id)

        # Step 1: Classify intent
        import time

        yield f"data: {json.dumps({'step': 'Analyzing request...', 'status': 'started'})}\n\n"

        classify_start = time.time()
        classification = await orchestrator.classify_intent(message)
        classify_duration_ms = int((time.time() - classify_start) * 1000)

        yield f"data: {json.dumps({'step': 'Analyzing request...', 'status': 'completed', 'duration_ms': classify_duration_ms})}\n\n"

        logger.info("Intent classification: %s", classification.intent)

        if classification.intent == "conversation":
            # Handle as conversation
            yield f"data: {json.dumps({'step': 'Generating response...', 'status': 'started'})}\n\n"

            convo_start = time.time()
            response_text = await orchestrator.handle_conversation(message)
            convo_duration_ms = int((time.time() - convo_start) * 1000)

            yield f"data: {json.dumps({'step': 'Generating response...', 'status': 'completed', 'duration_ms': convo_duration_ms})}\n\n"

            # Emit the response
            output = {
                "text": response_text,
                "thread_id": orchestrator.thread_id,
            }
            yield f"data: {json.dumps(output)}\n\n"

        else:
            # Data query or refinement - build request and invoke workflow
            nl2sql_request = orchestrator.build_nl2sql_request(classification)

            # Create fresh NL2SQL workflow
            workflow, _, _ = create_nl2sql_workflow()

            logger.info(
                "Invoking NL2SQL workflow: is_refinement=%s, query=%s",
                nl2sql_request.is_refinement,
                nl2sql_request.user_query[:50],
            )

            # Helper to format step events with duration
            def format_step_event(step_event: dict) -> dict:
                result = {
                    "step": step_event.get("step"),
                    "done": False,
                }
                if "status" in step_event:
                    result["status"] = step_event["status"]
                if "duration_ms" in step_event:
                    result["duration_ms"] = step_event["duration_ms"]
                return result

            # Run the workflow with the NL2SQLRequest
            async for event in workflow.run_stream(nl2sql_request):
                # Log all events for debugging
                logger.debug("Received workflow event: %s", type(event).__name__)

                # Handle step events from queue - include duration_ms
                while not step_queue.empty():
                    try:
                        step_event = step_queue.get_nowait()
                        yield f"data: {json.dumps(format_step_event(step_event))}\n\n"
                    except asyncio.QueueEmpty:
                        break

                if isinstance(event, WorkflowOutputEvent):
                    logger.info(
                        "Received WorkflowOutputEvent with data type: %s", type(event.data).__name__
                    )
                    try:
                        # Parse the NL2SQL response
                        from models import NL2SQLResponse

                        # The output comes from event.data (can be str or dict)
                        output_data = event.data
                        if isinstance(output_data, str):
                            response_data = json.loads(output_data)
                        else:
                            response_data = output_data
                        response = NL2SQLResponse.model_validate(response_data)

                        # Update orchestrator context for potential refinements
                        # NL2SQLResponse now contains template_json and extracted_params
                        orchestrator.update_context(
                            response, response.template_json, response.extracted_params
                        )

                        # Store orchestrator in session cache
                        if orchestrator.thread_id:
                            store_orchestrator(orchestrator.thread_id, orchestrator)

                        # Render and emit response
                        output = orchestrator.render_response(response)
                        yield f"data: {json.dumps(output)}\n\n"

                    except json.JSONDecodeError as e:
                        # If not JSON, emit as text
                        logger.error("Failed to parse NL2SQL response: %s", e)
                        yield f"data: {json.dumps({'text': str(event.data), 'thread_id': orchestrator.thread_id})}\n\n"

                elif isinstance(event, ExecutorInvokedEvent):
                    logger.info("Executor invoked: %s", event.executor_id)
                    # Drain step events when executor starts
                    while not step_queue.empty():
                        try:
                            step_event = step_queue.get_nowait()
                            yield f"data: {json.dumps(format_step_event(step_event))}\n\n"
                        except asyncio.QueueEmpty:
                            break

                elif isinstance(event, ExecutorCompletedEvent):
                    logger.info("Executor completed: %s", event.executor_id)
                    # Drain step events when executor completes
                    while not step_queue.empty():
                        try:
                            step_event = step_queue.get_nowait()
                            yield f"data: {json.dumps(format_step_event(step_event))}\n\n"
                        except asyncio.QueueEmpty:
                            break

                elif isinstance(event, WorkflowStatusEvent):
                    logger.info("Workflow status: %s", event.state)
                    if event.state == WorkflowRunState.IDLE:
                        logger.info("Workflow completed (IDLE)")

                elif isinstance(event, RequestInfoEvent):
                    # Workflow is requesting clarification from the user
                    logger.info("Received RequestInfoEvent with request_id: %s", event.request_id)

                    from models import ClarificationRequest

                    if isinstance(event.data, ClarificationRequest):
                        # Store the paused workflow for later resumption
                        store_paused_workflow(event.request_id, workflow)
                        logger.info("Stored paused workflow for request_id=%s", event.request_id)

                        # Build the clarification response for the frontend
                        clarification_data = {
                            "needs_clarification": True,
                            "clarification": {
                                "request_id": event.request_id,
                                "parameter_name": event.data.parameter_name,
                                "prompt": event.data.prompt,
                                "allowed_values": event.data.allowed_values,
                            },
                            "thread_id": orchestrator.thread_id,
                            "done": False,
                        }
                        yield f"data: {json.dumps(clarification_data)}\n\n"
                        logger.info("Sent clarification request to frontend: %s", event.data.prompt)

                        # Emit steps_complete signal
                        yield f"data: {json.dumps({'steps_complete': True, 'done': False})}\n\n"

                        # Store orchestrator for when user responds
                        if orchestrator.thread_id:
                            store_orchestrator(orchestrator.thread_id, orchestrator)

                        # Final done signal - workflow is paused awaiting user response
                        yield f"data: {json.dumps({'done': True, 'thread_id': orchestrator.thread_id})}\n\n"
                        return  # Exit - workflow is paused

            # Drain any remaining step events after workflow completes
            while not step_queue.empty():
                try:
                    step_event = step_queue.get_nowait()
                    yield f"data: {json.dumps(format_step_event(step_event))}\n\n"
                except asyncio.QueueEmpty:
                    break

        # Done
        yield f"data: {json.dumps({'done': True, 'thread_id': orchestrator.thread_id})}\n\n"

    except Exception as e:
        logger.error("Orchestrator error: %s", e, exc_info=True)
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"

    finally:
        clear_step_queue()


@router.get("/stream")
async def chat_stream(
    message: str = Query(..., description="User message"),
    thread_id: str | None = Query(None, description="Foundry thread ID (omit for new thread)"),
    title: str | None = Query(None, description="Thread title (for new threads only)"),
    request_id: str | None = Query(None, description="Request ID for clarification responses"),
    user_id: str | None = Depends(get_optional_user_id),
):
    """
    SSE streaming chat with ConversationOrchestrator architecture.

    This endpoint supports conversational refinements like:
    - "show me for 90 days" (modify time parameter)
    - "what about top 20?" (modify count parameter)

    The orchestrator:
    1. Classifies intent (data query, refinement, or conversation)
    2. For data: invokes NL2SQL workflow with context
    3. Tracks conversation context for refinements

    For clarification responses:
    - When request_id is provided, resumes the paused workflow
    """
    # If this is a clarification response, use the clarification handler
    if request_id:
        paused_workflow = get_paused_workflow(request_id)
        if paused_workflow:
            logger.info("Resuming paused workflow for request_id=%s", request_id)
            return StreamingResponse(
                generate_clarification_response_stream(
                    paused_workflow, message, request_id, thread_id, user_id
                ),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
        logger.warning("No paused workflow found for request_id=%s", request_id)

    return StreamingResponse(
        generate_orchestrator_streaming_response(message, thread_id, user_id, title),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
