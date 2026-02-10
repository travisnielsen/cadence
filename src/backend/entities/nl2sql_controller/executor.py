"""
NL2SQL Agent Executor for workflow integration.

Note: Do NOT use 'from __future__ import annotations' in this module.
The Agent Framework's @handler decorator validates WorkflowContext type annotations
at class definition time, which is incompatible with PEP 563 stringified annotations.
"""

import json
import logging
from pathlib import Path
from typing import Any

from agent_framework import (
    AgentResponse,
    ChatAgent,
    Executor,
    Role,
    WorkflowContext,
    handler,
    response_handler,
)
from agent_framework_azure_ai import AzureAIClient
from pydantic import ValidationError

# Type alias for V2 client
AzureAIAgentClient = AzureAIClient

import contextlib

from entities.shared.tools import execute_sql, search_query_templates, search_tables
from models import (
    ClarificationMessage,
    ClarificationRequest,
    ExtractionRequestMessage,
    NL2SQLRequest,
    NL2SQLResponse,
    ParameterExtractionRequest,
    QueryBuilderRequest,
    QueryBuilderRequestMessage,
    QueryTemplate,
    SQLDraft,
    SQLDraftMessage,
    TableMetadata,
)

logger = logging.getLogger(__name__)

# Type alias for NL2SQL output messages
# NL2SQL sends str (JSON) to chat, ExtractionRequestMessage to param_extractor,
# QueryBuilderRequestMessage to query_builder, and SQLDraftMessage to query_validator/param_validator
NL2SQLOutputMessage = str | ExtractionRequestMessage | QueryBuilderRequestMessage | SQLDraftMessage

# Key for storing pending clarification state
CLARIFICATION_STATE_KEY = "pending_clarification"


def _substitute_parameters(sql_template: str, params: dict) -> str:
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
            str_value = value.upper() if value.upper() in {"ASC", "DESC", "NULL"} else str(value)
        else:
            str_value = str(value)

        result = result.replace(token, str_value)

    return result


def _format_defaults_for_display(defaults_used: dict[str, Any]) -> dict[str, str]:
    """
    Format defaults_used dict into human-readable descriptions.

    Args:
        defaults_used: Dict of parameter name -> default value

    Returns:
        Dict of parameter name -> human-readable description
    """
    if not defaults_used:
        return {}

    descriptions: dict[str, str] = {}
    for name, value in defaults_used.items():
        # Format common parameter patterns
        if name == "days":
            descriptions[name] = f"last {value} days"
        elif name == "from_date" and isinstance(value, str) and "GETDATE()" in value.upper():
            descriptions[name] = "relative to current date"
        elif name in {"limit", "top"}:
            descriptions[name] = f"showing top {value} results"
        elif name in {"order", "sort"}:
            descriptions[name] = f"sorted {value}"
        else:
            # Generic format
            descriptions[name] = str(value)

    return descriptions


def _load_prompt() -> str:
    """Load prompt from prompt.md in this folder."""
    prompt_path = Path(__file__).parent / "prompt.md"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


class NL2SQLController(Executor):
    """
    Controller that orchestrates NL2SQL query processing.

    This controller orchestrates a multi-step workflow:
    1. Search query_templates index to understand user intent
    2. If high confidence match: route to ParameterExtractor for SQL generation
    3. Execute the generated SQL and return results
    4. Handle clarification requests from ParameterExtractor

    If no high confidence template match is found, asks for clarification.
    """

    agent: ChatAgent

    def __init__(self, chat_client: AzureAIAgentClient, executor_id: str = "nl2sql") -> None:
        """
        Initialize the NL2SQL executor.

        Args:
            chat_client: The Azure AI agent client for creating the agent
            executor_id: Executor ID for workflow routing
        """
        instructions = _load_prompt()

        # Agent uses search_query_templates to understand intent
        # SQL execution happens directly in the executor after parameter extraction
        self.agent = ChatAgent(
            name="nl2sql-agent",
            instructions=instructions,
            chat_client=chat_client,
            tools=[search_query_templates, execute_sql],
        )

        super().__init__(id=executor_id)

        # Track if we're acting as the workflow entry point (for orchestrator pattern)
        # When True, final responses use yield_output instead of send_message
        self._is_entry_point = False

        logger.info(
            "NL2SQLController initialized with tools: ['search_query_templates', 'execute_sql']"
        )

    async def _send_final_response(
        self, response: NL2SQLResponse, ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Send the final NL2SQL response.

        Uses yield_output when acting as entry point (orchestrator pattern),
        otherwise uses send_message (v1 workflow with ChatAgent).
        """
        response_json = response.model_dump_json()
        if self._is_entry_point:
            await ctx.yield_output(response_json)  # type: ignore[reportArgumentType]
        else:
            await ctx.send_message(response_json)

    @handler
    async def handle_question(
        self, question: str, ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Handle a user question by searching for query templates and orchestrating extraction.

        Workflow:
        1. Search query_templates for matching intent
        2. If high confidence: route to ParameterExtractor
        3. If low confidence: ask for clarification

        Args:
            question: The user's natural language question
            ctx: Workflow context for sending the response (as JSON string)
        """
        logger.info("NL2SQLController processing question: %s", question[:100])

        try:
            # Step 1: Search for query templates
            # Note: AIFunction wrapper is awaitable but type checker doesn't understand it
            search_result = await search_query_templates(question)  # type: ignore[misc]

            if search_result.get("has_high_confidence_match") and search_result.get("best_match"):
                # High confidence AND unambiguous match found - route to parameter extractor
                best_match = search_result["best_match"]
                template = QueryTemplate.model_validate(best_match)

                logger.info(
                    "High confidence unambiguous template match: '%s' (score: %.3f, gap: %.3f)",
                    template.intent,
                    template.score,
                    search_result.get("ambiguity_gap", 0.0),
                )

                # Build extraction request and route to parameter extractor
                extraction_request = ParameterExtractionRequest(
                    user_query=question,
                    template=template,
                )

                # Store the original question for potential clarification flow
                await ctx.set_shared_state(
                    CLARIFICATION_STATE_KEY,
                    {
                        "original_question": question,
                        "template": template.model_dump(),
                    },
                )

                # Route to parameter extractor
                request_msg = ExtractionRequestMessage(
                    request_json=extraction_request.model_dump_json()
                )
                await ctx.send_message(request_msg, target_id="param_extractor")

            else:
                # Either low confidence or ambiguous match
                is_ambiguous = search_result.get("is_ambiguous", False)
                confidence_score = search_result.get("confidence_score", 0)
                confidence_threshold = search_result.get("confidence_threshold", 0.75)

                if is_ambiguous:
                    # Ambiguous match - multiple templates with similar high scores
                    # Ask for clarification rather than generating a dynamic query
                    all_matches = search_result.get("all_matches", [])
                    matching_intents = [
                        m.get("intent", "unknown")
                        for m in all_matches[:3]
                        if m.get("score", 0) >= confidence_threshold
                    ]

                    logger.info(
                        "Ambiguous template match (gap: %.3f < %.3f). Top matches: %s",
                        search_result.get("ambiguity_gap", 0),
                        search_result.get("ambiguity_gap_threshold", 0.05),
                        matching_intents,
                    )

                    # Build clarification message listing possible interpretations
                    intent_list = ", ".join(f"'{intent}'" for intent in matching_intents)
                    error_message = (
                        f"Your question could match multiple query types: {intent_list}. "
                        "Could you please be more specific about what data you're looking for?"
                    )

                    nl2sql_response = NL2SQLResponse(
                        sql_query="",
                        error=error_message,
                        confidence_score=confidence_score,
                    )
                    await self._send_final_response(nl2sql_response, ctx)

                else:
                    # Low confidence - no good template match found
                    # Fallback to dynamic query generation via table search
                    logger.info(
                        "No high confidence template match (best score: %.3f, threshold: %.3f). "
                        "Attempting dynamic query generation via table search.",
                        confidence_score,
                        confidence_threshold,
                    )

                    # Search for relevant tables
                    table_search_result = await search_tables(question)  # type: ignore[misc]

                    if table_search_result.get("has_matches") and table_search_result.get("tables"):
                        # Found relevant tables - route to query builder
                        tables_data = table_search_result["tables"]
                        tables = [TableMetadata.model_validate(t) for t in tables_data]

                        logger.info(
                            "Found %d relevant tables for dynamic query: %s",
                            len(tables),
                            [t.table for t in tables],
                        )

                        # Store state for potential debugging/logging
                        await ctx.set_shared_state(
                            CLARIFICATION_STATE_KEY,
                            {
                                "original_question": question,
                                "dynamic_query": True,
                                "tables": [t.model_dump() for t in tables],
                            },
                        )

                        # Build query builder request and route
                        query_request = QueryBuilderRequest(
                            user_query=question,
                            tables=tables,
                        )

                        request_msg = QueryBuilderRequestMessage(
                            request_json=query_request.model_dump_json()
                        )
                        await ctx.send_message(request_msg, target_id="query_builder")

                    else:
                        # No tables found either - return error
                        logger.info(
                            "No relevant tables found. Table search result: %s",
                            table_search_result.get("message", "unknown"),
                        )

                        error_message = (
                            "I couldn't find a matching query pattern or relevant tables for your question. "
                            "Could you please rephrase or provide more details about what data you're looking for?"
                        )

                        nl2sql_response = NL2SQLResponse(
                            sql_query="",
                            error=error_message,
                            confidence_score=confidence_score,
                        )
                        await self._send_final_response(nl2sql_response, ctx)

        except Exception as e:
            logger.exception("NL2SQL execution error")
            nl2sql_response = NL2SQLResponse(sql_query="", error=str(e))
            await self._send_final_response(nl2sql_response, ctx)

    @handler
    async def handle_nl2sql_request(
        self, request: NL2SQLRequest, ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Handle an NL2SQLRequest from the ConversationOrchestrator.

        This handler supports both new queries and refinements.
        For refinements, it uses the provided template or previous SQL context.

        Args:
            request: The NL2SQL request with optional refinement context
            ctx: Workflow context for sending the response
        """
        # Mark as entry point - final responses should use yield_output
        self._is_entry_point = True

        logger.info(
            "NL2SQLController handling NL2SQLRequest: is_refinement=%s, query=%s",
            request.is_refinement,
            request.user_query[:100],
        )

        try:
            if request.is_refinement and request.previous_template_json:
                # Template-based refinement: use the previous template with overrides
                await self._handle_refinement(request, ctx)
            elif request.is_refinement and request.previous_sql:
                # Dynamic refinement: use the previous SQL as context
                await self._handle_dynamic_refinement(request, ctx)
            else:
                # New query: delegate to existing handler
                await self.handle_question(request.user_query, ctx)

        except Exception as e:
            logger.exception("NL2SQL request error")
            nl2sql_response = NL2SQLResponse(sql_query="", error=str(e))
            await self._send_final_response(nl2sql_response, ctx)

    async def _handle_refinement(
        self, request: NL2SQLRequest, ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Handle a refinement request using the previous template.

        Merges base_params with param_overrides and routes to parameter extraction.
        """
        try:
            template_data = json.loads(request.previous_template_json or "{}")
            template = QueryTemplate.model_validate(template_data)
        except (json.JSONDecodeError, ValidationError):
            logger.exception("Failed to parse previous template")
            # Fall back to treating as a new question
            await self.handle_question(request.user_query, ctx)
            return

        # Merge base params with overrides
        merged_params = dict(request.base_params or {})
        if request.param_overrides:
            merged_params.update(request.param_overrides)
            logger.info("Applied param overrides: %s", request.param_overrides)

        # Store clarification state with the merged context
        await ctx.set_shared_state(
            CLARIFICATION_STATE_KEY,
            {
                "original_question": request.user_query,
                "template": template.model_dump(),
                "is_refinement": True,
                "base_params": request.base_params,
                "param_overrides": request.param_overrides,
            },
        )

        # Build extraction request with the enriched query
        # Include the param overrides hint for the extractor
        enriched_query = request.user_query
        if request.param_overrides:
            override_hints = ", ".join(f"{k}={v}" for k, v in request.param_overrides.items())
            enriched_query = f"{request.user_query} (Use these values: {override_hints})"

        extraction_request = ParameterExtractionRequest(
            user_query=enriched_query,
            template=template,
        )

        logger.info(
            "Routing refinement to param_extractor: template=%s, merged_params=%s",
            template.intent,
            merged_params,
        )

        request_msg = ExtractionRequestMessage(request_json=extraction_request.model_dump_json())
        await ctx.send_message(request_msg, target_id="param_extractor")

    async def _handle_dynamic_refinement(
        self, request: NL2SQLRequest, ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Handle a refinement request for a dynamic (non-template) query.

        Re-uses table metadata from the previous query if available.
        """
        logger.info(
            "Handling dynamic refinement: previous_question=%s, tables=%s",
            request.previous_question,
            request.previous_tables,
        )

        # Try to re-use table metadata from previous query
        tables: list[TableMetadata] = []

        if request.previous_tables_json:
            # Re-use the full table metadata from the previous query
            try:
                tables_data = json.loads(request.previous_tables_json)
                tables = [TableMetadata.model_validate(t) for t in tables_data]
                logger.info("Re-using %d tables from previous query context", len(tables))
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning("Failed to parse previous tables JSON: %s", e)

        if not tables:
            # Fall back to searching based on the new query
            logger.info("No cached table metadata, searching for tables")
            table_search = await search_tables(request.user_query)  # type: ignore[misc]
            if table_search.get("has_matches") and table_search.get("tables"):
                tables = [TableMetadata.model_validate(t) for t in table_search["tables"]]

        if not tables:
            nl2sql_response = NL2SQLResponse(
                sql_query="",
                error="Unable to refine query - no table metadata available.",
                query_source="dynamic",
            )
            await self._send_final_response(nl2sql_response, ctx)
            return

        # Build an enriched query that includes the refinement context
        enriched_query = (
            f"Modify this previous query based on the user's request.\n\n"
            f"Previous question: {request.previous_question}\n"
            f"Previous SQL: {request.previous_sql}\n\n"
            f"User's refinement request: {request.user_query}\n\n"
            f"Generate a new SQL query that applies the user's requested changes to the previous query."
        )

        # Route to QueryBuilder
        query_request = QueryBuilderRequest(
            user_query=enriched_query,
            tables=tables,
            retry_count=0,
        )

        # Store context for potential follow-up (with full metadata for chained refinements)
        await ctx.set_shared_state(
            CLARIFICATION_STATE_KEY,
            {
                "original_question": request.user_query,
                "dynamic_query": True,
                "tables": [t.model_dump() for t in tables],
                "is_refinement": True,
                "previous_sql": request.previous_sql,
                "previous_question": request.previous_question,
            },
        )

        request_msg = QueryBuilderRequestMessage(request_json=query_request.model_dump_json())
        await ctx.send_message(request_msg, target_id="query_builder")

    @handler
    async def handle_sql_draft(
        self, sql_draft_msg: SQLDraftMessage, ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Handle SQLDraft from ParameterExtractor, QueryBuilder, ParameterValidator, or QueryValidator.

        This is called after:
        1. ParameterExtractor has extracted parameters and built SQL (source="param_extractor")
        2. QueryBuilder has generated dynamic SQL (source="query_builder")
        3. ParameterValidator has validated parameter values (source="param_validator", params_validated=True)
        4. QueryValidator has validated the SQL query (source="query_validator", query_validated=True)

        Routing logic:
        - If query_validated=True: execute SQL or handle violations
        - If params_validated=False and source="param_extractor": route to param_validator
        - If params_validated=True and not query_validated: route to query_validator
        - If source="query_builder" and not query_validated: route to query_validator
        - If error/needs_clarification: handle appropriately

        Args:
            sql_draft_msg: Wrapped JSON string containing SQLDraft
            ctx: Workflow context for sending the response
        """
        logger.info("NL2SQLController received SQLDraft from source=%s", sql_draft_msg.source)

        try:
            # Parse the SQLDraft
            draft_data = json.loads(sql_draft_msg.response_json)
            sql_draft = SQLDraft.model_validate(draft_data)

            if sql_draft.status == "success":
                completed_sql = sql_draft.completed_sql

                # If from param_extractor with no SQL, build it from template + extracted params
                if (
                    not completed_sql
                    and sql_draft.source == "template"
                    and sql_draft.extracted_parameters
                ):
                    try:
                        clarification_state = await ctx.get_shared_state(CLARIFICATION_STATE_KEY)
                        template_data = (
                            clarification_state.get("template") if clarification_state else None
                        )
                        if template_data:
                            sql_template = template_data.get("sql_template")
                            if sql_template:
                                completed_sql = _substitute_parameters(
                                    sql_template, sql_draft.extracted_parameters
                                )
                                sql_draft.completed_sql = completed_sql
                                logger.info("Built SQL from template: %s", completed_sql[:200])
                    except (KeyError, TypeError):
                        logger.exception("Failed to get template for SQL substitution")

                if not completed_sql:
                    raise ValueError("SQL draft succeeded but no SQL was generated")

                # Check if query has been validated
                if sql_draft.query_validated:
                    # Query has been validated - check for violations and execute
                    if sql_draft.query_violations:
                        # Validation failed - check retry count
                        retry_count = sql_draft.retry_count
                        violations = sql_draft.query_violations

                        logger.warning(
                            "Query validation failed (retry=%d): %s", retry_count, violations
                        )

                        if retry_count < 1:
                            # Allow one retry - re-submit to query_builder with validation feedback
                            logger.info("Retrying query generation with validation feedback")

                            # Get stored state for table metadata
                            tables: list[TableMetadata] = []
                            try:
                                clarification_state = await ctx.get_shared_state(
                                    CLARIFICATION_STATE_KEY
                                )
                                tables_data = clarification_state.get("tables", [])
                                if tables_data:
                                    tables = [TableMetadata.model_validate(t) for t in tables_data]
                            except (KeyError, TypeError):
                                pass

                            # Search for tables if we don't have them
                            if not tables:
                                table_search_result = await search_tables(sql_draft.user_query)  # type: ignore[misc]
                                if table_search_result.get(
                                    "has_matches"
                                ) and table_search_result.get("tables"):
                                    tables = [
                                        TableMetadata.model_validate(t)
                                        for t in table_search_result["tables"]
                                    ]
                                else:
                                    violation_list = "; ".join(violations)
                                    nl2sql_response = NL2SQLResponse(
                                        sql_query="",
                                        error=f"Query validation failed: {violation_list}. Unable to retry - no table metadata available.",
                                        query_source="dynamic",
                                    )
                                    await self._send_final_response(nl2sql_response, ctx)
                                    return

                            # Enrich the user query with validation feedback
                            violation_list = "; ".join(violations)
                            enriched_query = (
                                f"{sql_draft.user_query}\n\n"
                                f"[IMPORTANT: Your previous query was rejected for validation errors: {violation_list}. "
                                f"Please generate a corrected SQL query that addresses these issues.]"
                            )

                            query_request = QueryBuilderRequest(
                                user_query=enriched_query,
                                tables=tables,
                                retry_count=retry_count + 1,
                            )

                            await ctx.set_shared_state(
                                CLARIFICATION_STATE_KEY,
                                {
                                    "original_question": sql_draft.user_query,
                                    "dynamic_query": True,
                                    "tables": [t.model_dump() for t in tables],
                                    "retry_count": retry_count + 1,
                                    "validation_violations": violations,
                                },
                            )

                            request_msg = QueryBuilderRequestMessage(
                                request_json=query_request.model_dump_json()
                            )
                            await ctx.send_message(request_msg, target_id="query_builder")
                        else:
                            # Max retries exceeded
                            violation_summary = "; ".join(violations)
                            error_message = (
                                f"I was unable to generate a valid query for your request. "
                                f"Validation issues: {violation_summary}. "
                                f"Please try rephrasing your question or be more specific about what data you need."
                            )

                            nl2sql_response = NL2SQLResponse(
                                sql_query="",
                                error=error_message,
                                query_source="dynamic",
                            )

                            logger.error(
                                "Query validation failed after retry: %s", violation_summary
                            )
                            await self._send_final_response(nl2sql_response, ctx)
                    else:
                        # Query is valid - execute the SQL
                        logger.info("Validation passed. Executing SQL: %s", completed_sql[:200])

                        sql_result = await execute_sql(completed_sql)  # type: ignore[misc]

                        # Clear clarification state
                        with contextlib.suppress(Exception):
                            await ctx.set_shared_state(CLARIFICATION_STATE_KEY, None)

                        query_source = "template" if sql_draft.template_id else "dynamic"
                        confidence = 0.85 if sql_draft.template_id else 0.7

                        # Build human-readable defaults description
                        defaults_description = _format_defaults_for_display(sql_draft.defaults_used)

                        nl2sql_response = NL2SQLResponse(
                            sql_query=completed_sql,
                            sql_response=sql_result.get("rows", []),
                            columns=sql_result.get("columns", []),
                            row_count=sql_result.get("row_count", 0),
                            confidence_score=confidence,
                            used_cached_query=bool(sql_draft.template_id),
                            query_source=query_source,
                            error=sql_result.get("error")
                            if not sql_result.get("success")
                            else None,
                            defaults_used=defaults_description,
                            # Context tracking for template refinements
                            template_json=sql_draft.template_json,
                            extracted_params=sql_draft.extracted_parameters or {},
                            # Context tracking for dynamic refinements
                            tables_used=sql_draft.tables_used,
                            tables_metadata_json=sql_draft.tables_metadata_json,
                            original_question=sql_draft.user_query,
                        )

                        logger.info(
                            "NL2SQL completed: source=%s, rows=%d",
                            query_source,
                            nl2sql_response.row_count,
                        )
                        await self._send_final_response(nl2sql_response, ctx)

                elif sql_draft.source == "param_extractor" or (  # pyright: ignore[reportUnnecessaryComparison]
                    sql_draft.template_id and not sql_draft.params_validated
                ):
                    # Template-based: route to parameter validator
                    # Note: SQL substitution already happened at the start of this handler

                    logger.info("Routing SQLDraft to parameter validator")

                    # Forward the message to param_validator
                    forward_msg = SQLDraftMessage(
                        source="nl2sql_controller", response_json=sql_draft.model_dump_json()
                    )
                    await ctx.send_message(forward_msg, target_id="param_validator")

                elif sql_draft.params_validated or sql_draft.source in {
                    "query_builder",
                    "param_validator",
                }:
                    # Parameters validated or dynamic query - route to query_validator
                    logger.info("Routing SQLDraft to query validator: %s", completed_sql[:200])

                    # Store state for potential retry
                    if sql_draft.template_id:
                        await ctx.set_shared_state(
                            CLARIFICATION_STATE_KEY,
                            {
                                "original_question": sql_draft.user_query,
                                "template_id": sql_draft.template_id,
                                "template_based": True,
                            },
                        )
                    else:
                        existing_tables = []
                        try:
                            existing_state = await ctx.get_shared_state(CLARIFICATION_STATE_KEY)
                            if existing_state and "tables" in existing_state:
                                existing_tables = existing_state["tables"]
                        except (KeyError, TypeError):
                            pass

                        await ctx.set_shared_state(
                            CLARIFICATION_STATE_KEY,
                            {
                                "original_question": sql_draft.user_query,
                                "dynamic_query": True,
                                "tables": existing_tables,
                                "tables_used": sql_draft.tables_used,
                            },
                        )

                    # Forward to query_validator
                    forward_msg = SQLDraftMessage(
                        source="nl2sql_controller", response_json=sql_draft.model_dump_json()
                    )
                    await ctx.send_message(forward_msg, target_id="query_validator")

                else:
                    # Unknown source - route to query_validator as fallback
                    logger.warning(
                        "Unknown SQLDraft source=%s, routing to query_validator", sql_draft.source
                    )
                    forward_msg = SQLDraftMessage(
                        source="nl2sql_controller", response_json=sql_draft.model_dump_json()
                    )
                    await ctx.send_message(forward_msg, target_id="query_validator")

            elif sql_draft.status == "needs_clarification":
                # Need clarification from user - use request_info to pause workflow
                missing_params = sql_draft.missing_parameters or []

                # Get allowed values from the first missing parameter (most common case)
                allowed_values: list[str] = []
                param_name = ""
                if missing_params:
                    first_missing = missing_params[0]
                    param_name = first_missing.name

                    # Try to get allowed_values from parameter_definitions
                    for param_def in sql_draft.parameter_definitions:
                        if param_def.name == param_name and param_def.validation:
                            if param_def.validation.allowed_values:
                                allowed_values = param_def.validation.allowed_values
                            break

                # Build a friendly clarification prompt
                if allowed_values:
                    clarification_prompt = f"Please choose a category: {', '.join(allowed_values)}"
                else:
                    clarification_prompt = (
                        sql_draft.clarification_prompt
                        or "I need a bit more information to answer your question."
                    )

                # Get the template JSON from the SQLDraft (populated by param_extractor)
                template_json = sql_draft.template_json or ""
                if not template_json:
                    logger.warning(
                        "No template_json in SQLDraft - clarification resumption may fail"
                    )

                # Build clarification request for request_info
                clarification_request = ClarificationRequest(
                    parameter_name=param_name,
                    prompt=clarification_prompt,
                    allowed_values=allowed_values,
                    original_question=sql_draft.user_query or "",
                    template_id=sql_draft.template_id or "",
                    template_json=template_json,
                    extracted_parameters=sql_draft.extracted_parameters or {},
                )

                logger.info(
                    "Requesting clarification from user via request_info: %s", clarification_prompt
                )

                # This pauses the workflow and emits a RequestInfoEvent
                # The workflow will resume when send_responses_streaming is called
                await ctx.request_info(
                    request_data=clarification_request,
                    response_type=str,
                )

            else:
                # Error during extraction or parameter validation
                error_msg = sql_draft.error or "Unknown error during SQL generation"

                # Check if this is a parameter validation error
                if sql_draft.parameter_violations:
                    violation_summary = "; ".join(sql_draft.parameter_violations)
                    error_msg = f"Parameter validation failed: {violation_summary}"

                nl2sql_response = NL2SQLResponse(
                    sql_query="",
                    error=error_msg,
                    query_source="template" if sql_draft.source == "template" else "dynamic",
                )

                logger.error("SQLDraft failed: %s", error_msg)
                await self._send_final_response(nl2sql_response, ctx)

        except Exception as e:
            logger.exception("Error handling SQLDraft")
            nl2sql_response = NL2SQLResponse(sql_query="", error=str(e))
            await self._send_final_response(nl2sql_response, ctx)

    @handler
    async def handle_clarification(
        self, clarification_msg: ClarificationMessage, ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Handle a clarification response from the user.

        This is called when the user provides additional information
        after we requested clarification.

        Args:
            clarification_msg: The user's clarification wrapped in ClarificationMessage
            ctx: Workflow context for sending the response
        """
        clarification = clarification_msg.clarification_text
        logger.info("NL2SQLController received clarification: %s", clarification[:100])

        try:
            # Get the stored clarification state
            clarification_state = await ctx.get_shared_state(CLARIFICATION_STATE_KEY)

            if not clarification_state:
                # No pending clarification - treat as new question
                logger.warning("No clarification state found, treating as new question")
                await self.handle_question(clarification, ctx)
                return

            original_question = clarification_state.get("original_question", "")
            template_data = clarification_state.get("template")

            if not template_data:
                # No template stored - treat as new question
                logger.warning("No template in clarification state, treating as new question")
                await self.handle_question(clarification, ctx)
                return

            # Reconstruct the template
            template = QueryTemplate.model_validate(template_data)

            # Combine original question with clarification
            enriched_query = f"{original_question} (Additional info: {clarification})"

            logger.info(
                "Re-submitting with clarification. Original: '%s', Clarification: '%s'",
                original_question[:50],
                clarification[:50],
            )

            # Build new extraction request with enriched query
            extraction_request = ParameterExtractionRequest(
                user_query=enriched_query,
                template=template,
            )

            # Route to parameter extractor again
            request_msg = ExtractionRequestMessage(
                request_json=extraction_request.model_dump_json()
            )
            await ctx.send_message(request_msg, target_id="param_extractor")

        except KeyError:
            # No clarification state - treat as new question
            logger.warning("KeyError getting clarification state, treating as new question")
            await self.handle_question(clarification, ctx)

        except Exception as e:
            logger.exception("Error handling clarification")
            nl2sql_response = NL2SQLResponse(sql_query="", error=str(e))
            await self._send_final_response(nl2sql_response, ctx)

    @response_handler
    async def on_clarification_response(
        self,
        original_request: ClarificationRequest,
        user_response: str,
        ctx: WorkflowContext[NL2SQLOutputMessage],
    ) -> None:
        """
        Handle clarification response from the user via request_info/send_responses_streaming.

        This is called by Agent Framework when the user provides a response to a
        ClarificationRequest that was emitted via ctx.request_info().

        Args:
            original_request: The ClarificationRequest we sent
            user_response: The user's response (e.g., "Supermarket")
            ctx: Workflow context for continuing the workflow
        """
        logger.info(
            "Received clarification response: '%s' for parameter '%s'",
            user_response[:50],
            original_request.parameter_name,
        )

        try:
            # Reconstruct the template from the stored JSON
            if not original_request.template_json:
                logger.warning("No template in clarification request, treating as new question")
                await self.handle_question(user_response, ctx)
                return

            template_data = json.loads(original_request.template_json)
            template = QueryTemplate.model_validate(template_data)

            # Combine original question with clarification for better extraction
            enriched_query = f"{original_request.original_question} (Answer: {user_response})"

            logger.info(
                "Re-submitting with clarification. Original: '%s', User response: '%s'",
                original_request.original_question[:50],
                user_response[:50],
            )

            # Store clarification state for the extraction
            await ctx.set_shared_state(
                CLARIFICATION_STATE_KEY,
                {
                    "original_question": original_request.original_question,
                    "template": template_data,
                    "clarification_response": user_response,
                },
            )

            # Build new extraction request with enriched query
            extraction_request = ParameterExtractionRequest(
                user_query=enriched_query,
                template=template,
            )

            # Route to parameter extractor again
            request_msg = ExtractionRequestMessage(
                request_json=extraction_request.model_dump_json()
            )
            await ctx.send_message(request_msg, target_id="param_extractor")

        except json.JSONDecodeError:
            logger.exception("Failed to parse template JSON")
            nl2sql_response = NL2SQLResponse(
                sql_query="", error="Failed to resume clarification flow - invalid template data"
            )
            await self._send_final_response(nl2sql_response, ctx)

        except Exception as e:
            logger.exception("Error handling clarification response")
            nl2sql_response = NL2SQLResponse(sql_query="", error=str(e))
            await self._send_final_response(nl2sql_response, ctx)

    @staticmethod
    def _parse_agent_response(response: AgentResponse) -> NL2SQLResponse:
        """Parse the agent's response to extract structured data."""
        sql_query = ""
        sql_response: list[dict] = []
        columns: list[str] = []
        row_count = 0
        confidence_score = 0.0
        used_cached_query = False
        query_source = "dynamic"  # Default to dynamic, update if cached match found
        error = None

        # Extract data from tool call results in the messages
        for message in response.messages:
            if message.role == Role.TOOL:
                for content in message.contents:
                    if hasattr(content, "result"):
                        result = content.result
                        # Parse JSON string if needed
                        if isinstance(result, str):
                            try:
                                result = json.loads(result)
                            except json.JSONDecodeError:
                                continue

                        if isinstance(result, dict):
                            # Check for execute_sql result
                            if "rows" in result and result.get("success", False):
                                sql_response = result.get("rows", [])
                                columns = result.get("columns", [])
                                row_count = result.get("row_count", len(sql_response))

                            # Check for search result with confidence
                            if "has_high_confidence_match" in result:
                                used_cached_query = result.get("has_high_confidence_match", False)
                                if result.get("best_match"):
                                    best_match = result["best_match"]
                                    confidence_score = best_match.get("score", 0.0)
                                    if used_cached_query:
                                        sql_query = best_match.get("query", "")
                                        query_source = "cached"  # Query from cached queries

                            # Check for error
                            if not result.get("success", True) and "error" in result:
                                error = result["error"]

            # Look for function calls to get the SQL query
            if message.role == Role.ASSISTANT:
                for content in message.contents:
                    if (
                        hasattr(content, "name")
                        and content.name == "execute_sql"
                        and hasattr(content, "arguments")
                    ):
                        args = content.arguments
                        if isinstance(args, str):
                            with contextlib.suppress(json.JSONDecodeError):
                                args = json.loads(args)
                        if isinstance(args, dict) and "query" in args:
                            sql_query = args["query"]

        return NL2SQLResponse(
            sql_query=sql_query,
            sql_response=sql_response,
            columns=columns,
            row_count=row_count,
            confidence_score=confidence_score,
            used_cached_query=used_cached_query,
            query_source=query_source,
            error=error,
        )
