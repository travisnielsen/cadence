"""
Parameter Extractor Executor for workflow integration.

This executor receives query templates and user queries, then uses
an LLM to extract parameter values and build the final SQL query.

Note: Do NOT use 'from __future__ import annotations' in this module.
The Agent Framework's @handler decorator validates WorkflowContext type annotations
at class definition time, which is incompatible with PEP 563 stringified annotations.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent_framework import (
    AgentThread,
    ChatAgent,
    Executor,
    WorkflowContext,
    handler,
)
from agent_framework_azure_ai import AzureAIClient

# Type alias for V2 client
AzureAIAgentClient = AzureAIClient

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from models import (  # type: ignore[import-not-found]
        QueryTemplate,
        ParameterExtractionRequest,
        ParameterExtractionResponse,
        MissingParameter,
        ExtractionRequestMessage,
        ExtractionResponseMessage,
    )
except ImportError:
    from src.entities.models import (
        QueryTemplate,
        ParameterExtractionRequest,
        ParameterExtractionResponse,
        MissingParameter,
        ExtractionRequestMessage,
        ExtractionResponseMessage,
    )

logger = logging.getLogger(__name__)


def get_request_user_id() -> str | None:
    """
    Get the user ID from the request context.
    
    This is a lazy import wrapper to avoid circular imports.
    """
    try:
        from src.api.step_events import get_request_user_id as _get_request_user_id
        return _get_request_user_id()
    except ImportError:
        return None


# Shared state key for Foundry thread ID (V2 uses conversation_id internally)
FOUNDRY_CONVERSATION_ID_KEY = "foundry_conversation_id"

# Key used by Agent Framework for workflow.run_stream() kwargs
WORKFLOW_RUN_KWARGS_KEY = "_workflow_run_kwargs"


def _load_prompt() -> str:
    """Load prompt from prompt.md in this folder."""
    prompt_path = Path(__file__).parent / "prompt.md"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


def _build_extraction_prompt(user_query: str, template: QueryTemplate) -> str:
    """
    Build the prompt for the LLM to extract parameters.

    Args:
        user_query: The user's original question
        template: The matched query template

    Returns:
        A formatted prompt string for the LLM
    """
    # Calculate adjusted reference date (12 years ago for historical data)
    adjusted_date = datetime.now() - timedelta(days=12 * 365)
    adjusted_date_str = adjusted_date.strftime("%Y-%m-%d")

    # Format parameters for the prompt
    params_info = []
    for param in template.parameters:
        param_desc = {
            "name": param.name,
            "required": param.required,
            "ask_if_missing": param.ask_if_missing,
            "default_value": param.default_value,
        }
        if param.validation:
            param_desc["validation"] = param.validation.model_dump()
        params_info.append(param_desc)

    prompt = f"""Extract parameters from the following user question to fill the SQL template.

## Adjusted Reference Date
**{adjusted_date_str}** - Use this date as "today" for any date-related parameters. The database contains historical data from approximately 12 years ago.

## User Question
{user_query}

## SQL Template
{template.sql_template}

## Template Intent
{template.intent}

## Template Example Question
{template.question}

## Parameters to Extract
{json.dumps(params_info, indent=2)}

Analyze the user question and extract values for each parameter.
Respond with a JSON object containing your extraction results.
"""
    return prompt


def _parse_llm_response(response_text: str) -> dict[str, Any]:
    """
    Parse the LLM's JSON response.

    Args:
        response_text: The raw text response from the LLM

    Returns:
        Parsed dictionary from the JSON response
    """
    # Try to extract JSON from the response
    text = response_text.strip()

    # Try direct JSON parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract from markdown code fence
    if "```json" in text:
        try:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                json_str = text[start:end].strip()
                return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass

    # Try to find any JSON object in the response
    json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Return error structure if we can't parse
    return {
        "status": "error",
        "error": f"Failed to parse LLM response: {text[:200]}"
    }


def _substitute_parameters(sql_template: str, params: dict[str, Any]) -> str:
    """
    Substitute parameter tokens in the SQL template.

    Args:
        sql_template: The SQL template with %{{param}}% tokens
        params: Dictionary of parameter name -> value

    Returns:
        SQL string with tokens replaced by values
    """
    result = sql_template
    for name, value in params.items():
        token = f"%{{{{{name}}}}}%"
        # Convert value to string, handling different types
        if value is None:
            str_value = "NULL"
        elif isinstance(value, bool):
            str_value = "1" if value else "0"
        elif isinstance(value, (int, float)):
            str_value = str(value)
        elif isinstance(value, str):
            # Don't quote SQL keywords like ASC/DESC
            if value.upper() in ("ASC", "DESC", "NULL"):
                str_value = value.upper()
            else:
                # For string values that will be used in SQL, they might need quoting
                # But the template should handle this - we just substitute the value
                str_value = str(value)
        else:
            str_value = str(value)
        
        result = result.replace(token, str_value)
    
    return result


class ParameterExtractorExecutor(Executor):
    """
    Executor that extracts parameter values from user queries.

    This executor:
    1. Receives user query + query template from NL2SQLAgentExecutor
    2. Uses LLM to analyze the query and extract parameter values
    3. Validates extracted values against parameter definitions
    4. Returns completed SQL or clarification request
    """

    agent: ChatAgent

    def __init__(self, chat_client: AzureAIAgentClient, executor_id: str = "param_extractor"):
        """
        Initialize the Parameter Extractor executor.

        Args:
            chat_client: The Azure AI agent client for creating the agent
            executor_id: Executor ID for workflow routing
        """
        instructions = _load_prompt()

        self.agent = ChatAgent(
            name="parameter-extractor-agent",
            instructions=instructions,
            chat_client=chat_client,
        )

        super().__init__(id=executor_id)
        logger.info("ParameterExtractorExecutor initialized")

    async def _get_or_create_thread(self, ctx: WorkflowContext[Any, Any]) -> tuple[AgentThread, bool]:
        """
        Get existing Foundry thread from shared state or create a new one.

        Returns:
            Tuple of (thread, is_new) where is_new indicates if this is a new thread
        """
        # First, check workflow run kwargs (set by chat.py via run_stream kwargs)
        try:
            run_kwargs = await ctx.get_shared_state(WORKFLOW_RUN_KWARGS_KEY)
            if run_kwargs and isinstance(run_kwargs, dict):
                thread_id = run_kwargs.get("thread_id")
                if thread_id:
                    logger.info("ParamExtractor using thread from run kwargs: %s", thread_id)
                    return self.agent.get_new_thread(service_thread_id=thread_id), False
        except KeyError:
            pass

        # Then, check regular shared state (may have been set by previous executor)
        try:
            thread_id = await ctx.get_shared_state(FOUNDRY_CONVERSATION_ID_KEY)
            if thread_id:
                logger.info("ParamExtractor using existing Foundry thread: %s", thread_id)
                return self.agent.get_new_thread(service_thread_id=thread_id), False
        except KeyError:
            pass

        # Create a new thread if none exists yet
        logger.info("ParamExtractor creating new Foundry thread")
        return self.agent.get_new_thread(), True

    async def _store_thread_id(self, ctx: WorkflowContext[Any, Any], thread: AgentThread) -> None:
        """Store the Foundry thread ID in shared state if it was created."""
        if thread.service_thread_id:
            try:
                existing = await ctx.get_shared_state(FOUNDRY_CONVERSATION_ID_KEY)
                if existing:
                    return  # Already stored
            except KeyError:
                pass
            await ctx.set_shared_state(FOUNDRY_CONVERSATION_ID_KEY, thread.service_thread_id)
            logger.info("ParamExtractor stored Foundry thread ID: %s", thread.service_thread_id)

    @handler
    async def handle_extraction_request(
        self,
        request_msg: ExtractionRequestMessage,
        ctx: WorkflowContext[ExtractionResponseMessage]
    ) -> None:
        """
        Handle a parameter extraction request.

        Args:
            request_msg: Wrapped JSON string containing ParameterExtractionRequest
            ctx: Workflow context for sending the response
        """
        logger.info("ParameterExtractorExecutor received extraction request")

        # Emit step start event
        step_name = "Extracting parameters"
        emit_step_end_fn = None
        try:
            from src.api.step_events import emit_step_start, emit_step_end
            emit_step_start(step_name)
            emit_step_end_fn = emit_step_end
        except ImportError:
            pass

        def finish_step():
            if emit_step_end_fn:
                emit_step_end_fn(step_name)

        try:
            # Parse the request
            request_data = json.loads(request_msg.request_json)
            request = ParameterExtractionRequest.model_validate(request_data)
            template = request.template
            user_query = request.user_query

            logger.info(
                "Extracting parameters for template '%s' from query: %s",
                template.intent,
                user_query[:100]
            )

            # Get or create thread for the LLM call
            thread, is_new_thread = await self._get_or_create_thread(ctx)

            # Set metadata for new threads
            metadata = None
            if is_new_thread:
                user_id = get_request_user_id()
                if user_id:
                    metadata = {"user_id": user_id}

            # Build the extraction prompt
            extraction_prompt = _build_extraction_prompt(user_query, template)

            # Run the LLM to extract parameters
            response = await self.agent.run(extraction_prompt, thread=thread, metadata=metadata)

            # Store thread ID
            await self._store_thread_id(ctx, thread)

            # Get the response text
            response_text = ""
            for msg in response.messages:
                if hasattr(msg, 'contents'):
                    for content in msg.contents:
                        # Use getattr to safely access text attribute
                        text_value = getattr(content, 'text', None)
                        if text_value:
                            response_text = text_value
                            break
                    if response_text:
                        break

            # Parse the LLM response
            parsed = _parse_llm_response(response_text)

            # Build the response based on LLM output
            if parsed.get("status") == "success":
                # Substitute parameters into the SQL template
                extracted_params = parsed.get("extracted_parameters", {})
                completed_sql = _substitute_parameters(template.sql_template, extracted_params)

                extraction_response = ParameterExtractionResponse(
                    status="success",
                    completed_sql=completed_sql,
                    extracted_parameters=extracted_params,
                    original_query=user_query,
                    template_id=template.id,
                )

            elif parsed.get("status") == "needs_clarification":
                # Build missing parameters list
                missing = []
                for mp in parsed.get("missing_parameters", []):
                    missing.append(MissingParameter(
                        name=mp.get("name", ""),
                        description=mp.get("description", ""),
                        validation_hint=mp.get("validation_hint", ""),
                    ))

                extraction_response = ParameterExtractionResponse(
                    status="needs_clarification",
                    missing_parameters=missing,
                    clarification_prompt=parsed.get("clarification_prompt"),
                    extracted_parameters=parsed.get("extracted_parameters"),
                    original_query=user_query,
                    template_id=template.id,
                )

            else:
                # Error case
                extraction_response = ParameterExtractionResponse(
                    status="error",
                    error=parsed.get("error", "Unknown error during parameter extraction"),
                    original_query=user_query,
                    template_id=template.id,
                )

            logger.info("Parameter extraction completed with status: %s", extraction_response.status)

        except Exception as e:
            logger.error("Parameter extraction error: %s", e)
            extraction_response = ParameterExtractionResponse(
                status="error",
                error=str(e),
            )

        finish_step()

        # Send the response back to NL2SQL executor using typed wrapper
        response_msg = ExtractionResponseMessage(response_json=extraction_response.model_dump_json())
        await ctx.send_message(response_msg)
