"""
Query Builder Executor for workflow integration.

This executor receives table metadata and user queries, then uses
an LLM to generate SQL queries for dynamic query generation.

Note: Do NOT use 'from __future__ import annotations' in this module.
The Agent Framework's @handler decorator validates WorkflowContext type annotations
at class definition time, which is incompatible with PEP 563 stringified annotations.
"""

import json
import logging
import re
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

from models import (
    QueryBuilderRequest,
    QueryBuilderRequestMessage,
    SQLDraft,
    SQLDraftMessage,
    TableMetadata,
)

logger = logging.getLogger(__name__)


def get_request_user_id() -> str | None:
    """
    Get the user ID from the request context.

    This is a lazy import wrapper to avoid circular imports.
    """
    try:
        from api.step_events import get_request_user_id as _get_request_user_id

        return _get_request_user_id()
    except ImportError:
        return None


# Shared state key for Foundry thread ID
FOUNDRY_CONVERSATION_ID_KEY = "foundry_conversation_id"

# Key used by Agent Framework for workflow.run_stream() kwargs
WORKFLOW_RUN_KWARGS_KEY = "_workflow_run_kwargs"


def _load_prompt() -> str:
    """Load prompt from prompt.md in this folder."""
    prompt_path = Path(__file__).parent / "prompt.md"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


def _build_generation_prompt(user_query: str, tables: list[TableMetadata]) -> str:
    """
    Build the prompt for the LLM to generate a SQL query.

    Args:
        user_query: The user's original question
        tables: List of relevant table metadata

    Returns:
        A formatted prompt string for the LLM
    """
    # Format table metadata for the prompt
    tables_info = []
    for table in tables:
        columns_info = [{"name": col.name, "description": col.description} for col in table.columns]
        tables_info.append({
            "table": table.table,
            "description": table.description,
            "columns": columns_info,
        })

    return f"""Generate a SQL query to answer the following user question.

## User Question
{user_query}

## Available Tables
{json.dumps(tables_info, indent=2)}

Analyze the user question and generate a valid SQL SELECT query using only the tables and columns provided above.
Respond with a JSON object containing your query and reasoning.
"""


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
    json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Return error structure if we can't parse
    return {"status": "error", "error": f"Failed to parse LLM response: {text[:200]}"}


class QueryBuilderExecutor(Executor):
    """
    Executor that generates SQL queries from table metadata.

    This executor:
    1. Receives user query + table metadata from NL2SQLController
    2. Uses LLM to analyze the query and generate SQL
    3. Returns the generated SQL query or an error
    """

    agent: ChatAgent

    def __init__(self, chat_client: AzureAIAgentClient, executor_id: str = "query_builder") -> None:
        """
        Initialize the Query Builder executor.

        Args:
            chat_client: The Azure AI agent client for creating the agent
            executor_id: Executor ID for workflow routing
        """
        instructions = _load_prompt()

        self.agent = ChatAgent(
            name="query-builder-agent",
            instructions=instructions,
            chat_client=chat_client,
        )

        super().__init__(id=executor_id)
        logger.info("QueryBuilderExecutor initialized")

    async def _get_or_create_thread(
        self, ctx: WorkflowContext[Any, Any]
    ) -> tuple[AgentThread, bool]:
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
                    logger.info("QueryBuilder using thread from run kwargs: %s", thread_id)
                    return self.agent.get_new_thread(service_thread_id=thread_id), False
        except KeyError:
            pass

        # Then, check regular shared state (may have been set by previous executor)
        try:
            thread_id = await ctx.get_shared_state(FOUNDRY_CONVERSATION_ID_KEY)
            if thread_id:
                logger.info("QueryBuilder using existing Foundry thread: %s", thread_id)
                return self.agent.get_new_thread(service_thread_id=thread_id), False
        except KeyError:
            pass

        # Create a new thread if none exists yet
        logger.info("QueryBuilder creating new Foundry thread")
        return self.agent.get_new_thread(), True

    @staticmethod
    async def _store_thread_id(ctx: WorkflowContext[Any, Any], thread: AgentThread) -> None:
        """Store the Foundry thread ID in shared state if it was created."""
        if thread.service_thread_id:
            try:
                existing = await ctx.get_shared_state(FOUNDRY_CONVERSATION_ID_KEY)
                if existing:
                    return  # Already stored
            except KeyError:
                pass
            await ctx.set_shared_state(FOUNDRY_CONVERSATION_ID_KEY, thread.service_thread_id)
            logger.info("QueryBuilder stored Foundry thread ID: %s", thread.service_thread_id)

    @handler
    async def handle_query_build_request(
        self, request_msg: QueryBuilderRequestMessage, ctx: WorkflowContext[SQLDraftMessage]
    ) -> None:
        """
        Handle a query build request.

        Args:
            request_msg: Wrapped JSON string containing QueryBuilderRequest
            ctx: Workflow context for sending the response
        """
        logger.info("QueryBuilderExecutor received query build request")

        # Emit step start event
        step_name = "Generating SQL"
        emit_step_end_fn = None
        try:
            from api.step_events import emit_step_end, emit_step_start

            emit_step_start(step_name)
            emit_step_end_fn = emit_step_end
        except ImportError:
            pass

        def finish_step() -> None:
            if emit_step_end_fn:
                emit_step_end_fn(step_name)

        try:
            # Parse the request
            request_data = json.loads(request_msg.request_json)
            request = QueryBuilderRequest.model_validate(request_data)
            tables = request.tables
            user_query = request.user_query
            retry_count = request.retry_count

            logger.info(
                "Building query from %d tables for: %s (retry=%d)",
                len(tables),
                user_query[:100],
                retry_count,
            )

            # Get or create thread for the LLM call
            thread, is_new_thread = await self._get_or_create_thread(ctx)

            # Set metadata for new threads
            metadata = None
            if is_new_thread:
                user_id = get_request_user_id()
                if user_id:
                    metadata = {"user_id": user_id}

            # Build the generation prompt
            generation_prompt = _build_generation_prompt(user_query, tables)

            # Run the LLM to generate the query
            response = await self.agent.run(generation_prompt, thread=thread, metadata=metadata)

            # Store thread ID
            await self._store_thread_id(ctx, thread)

            # Get the response text
            response_text = ""
            for msg in response.messages:
                if hasattr(msg, "contents"):
                    for content in msg.contents:
                        text_value = getattr(content, "text", None)
                        if text_value:
                            response_text = text_value
                            break
                    if response_text:
                        break

            # Parse the LLM response
            parsed = _parse_llm_response(response_text)

            # Serialize tables for refinement context
            tables_metadata_json = json.dumps([t.model_dump() for t in tables])

            # Build the response based on LLM output
            if parsed.get("status") == "success":
                sql_draft = SQLDraft(
                    status="success",
                    source="dynamic",
                    completed_sql=parsed.get("completed_sql"),
                    user_query=user_query,
                    retry_count=retry_count,
                    reasoning=parsed.get("reasoning"),
                    tables_used=parsed.get("tables_used", []),
                    tables_metadata_json=tables_metadata_json,
                )
            else:
                sql_draft = SQLDraft(
                    status="error",
                    source="dynamic",
                    user_query=user_query,
                    retry_count=retry_count,
                    error=parsed.get("error", "Unknown error during query generation"),
                    tables_used=parsed.get("tables_used", []),
                    tables_metadata_json=tables_metadata_json,
                )

            logger.info("Query generation completed with status: %s", sql_draft.status)

        except Exception as e:
            logger.exception("Query generation error")
            sql_draft = SQLDraft(
                status="error",
                source="dynamic",
                error=str(e),
            )

        finish_step()

        # Send the response back to NL2SQL executor using typed wrapper
        response_msg = SQLDraftMessage(
            source="query_builder", response_json=sql_draft.model_dump_json()
        )
        await ctx.send_message(response_msg)
