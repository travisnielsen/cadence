"""
NL2SQL Agent Executor for workflow integration.

Note: Do NOT use 'from __future__ import annotations' in this module.
The Agent Framework's @handler decorator validates WorkflowContext type annotations
at class definition time, which is incompatible with PEP 563 stringified annotations.
"""

import json
import logging
import os
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
import operator

from entities.shared.column_filter import refine_columns
from entities.shared.error_recovery import build_error_recovery
from entities.shared.substitution import substitute_parameters
from entities.shared.tools import (
    execute_query_parameterized,
    execute_sql,
    search_query_templates,
    search_tables,
)
from models import (
    ClarificationMessage,
    ClarificationRequest,
    ExtractionRequestMessage,
    MissingParameter,
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

# Confidence routing thresholds
_CONFIDENCE_THRESHOLD_HIGH = 0.85
_CONFIDENCE_THRESHOLD_LOW = 0.6

# Confidence threshold for dynamic query confirmation gate
# Dynamic queries below this threshold require user confirmation before execution
_DYNAMIC_CONFIDENCE_THRESHOLD = float(os.getenv("DYNAMIC_CONFIDENCE_THRESHOLD", "0.7"))

# Type alias for NL2SQL output messages
# NL2SQL sends str (JSON) to chat, ExtractionRequestMessage to param_extractor,
# QueryBuilderRequestMessage to query_builder, and SQLDraftMessage to query_validator/param_validator
NL2SQLOutputMessage = str | ExtractionRequestMessage | QueryBuilderRequestMessage | SQLDraftMessage

# Key for storing pending clarification state
CLARIFICATION_STATE_KEY = "pending_clarification"

# Key for storing unresolved parameters across single-question turns
UNRESOLVED_PARAMS_STATE_KEY = "unresolved_params"


def _format_hypothesis_prompt(missing_params: list[MissingParameter]) -> str:
    """Format a hypothesis-first clarification prompt from missing parameters.

    When a best_guess is available, uses the format:
        "It looks like you want **{best_guess}** for {name}. Is that correct,
        or did you mean {alt1} or {alt2}?"

    When no best_guess is available, falls back to:
        "What value would you like for {name}? Options: {alternatives}"

    Args:
        missing_params: List of MissingParameter objects to format.

    Returns:
        Formatted clarification prompt string.
    """
    parts: list[str] = []
    for mp in missing_params:
        if mp.best_guess:
            alt_text = ""
            if mp.alternatives:
                alt_text = " or ".join(f"**{a}**" for a in mp.alternatives[:3])
                alt_text = f", or did you mean {alt_text}?"
            else:
                alt_text = "?"
            parts.append(
                f"It looks like you want **{mp.best_guess}** for {mp.name}"
                f". Is that correct{alt_text}"
            )
        elif mp.alternatives:
            opts = ", ".join(mp.alternatives[:5])
            parts.append(f"What value would you like for {mp.name}? Options: {opts}")
        else:
            parts.append(f"What value would you like for {mp.name}?")
    return " ".join(parts)


def _format_confirmation_note(
    parameter_confidences: dict[str, float],
    extracted_parameters: dict[str, Any] | None,
) -> str:
    """Build a confirmation note for medium-confidence parameters.

    Args:
        parameter_confidences: Per-parameter confidence scores.
        extracted_parameters: Resolved parameter values.

    Returns:
        A human-readable confirmation note, or empty string if nothing to confirm.
    """
    if not extracted_parameters:
        return ""
    confirm_parts: list[str] = []
    for name, score in parameter_confidences.items():
        if _CONFIDENCE_THRESHOLD_LOW <= score < _CONFIDENCE_THRESHOLD_HIGH:
            value = extracted_parameters.get(name)
            if value is not None:
                confirm_parts.append(f"{name}=**{value}**")
    if not confirm_parts:
        return ""
    return f"I assumed {', '.join(confirm_parts)} for these results. Want me to adjust?"


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
            # Check for pending confirmation (confidence gate acceptance)
            try:
                pending_state = await ctx.get_shared_state(CLARIFICATION_STATE_KEY)
                if (
                    pending_state
                    and isinstance(pending_state, dict)
                    and pending_state.get("pending_confirmation")
                ):
                    # Check if user is accepting or revising
                    accept_keywords = {"yes", "run", "execute", "accept", "go", "ok", "confirm"}
                    question_lower = question.strip().lower().rstrip(".")
                    is_acceptance = question_lower in accept_keywords or question_lower.startswith(
                        "run "
                    )

                    if is_acceptance:
                        logger.info("Confidence gate acceptance — executing pending query")
                        # Deserialize the stored SQLDraft and execute
                        sql_draft_json = pending_state.get("sql_draft_json", "")
                        if sql_draft_json:
                            stored_draft = SQLDraft.model_validate_json(sql_draft_json)
                            # Mark as refinement so the gate doesn't re-trigger
                            stored_state = dict(pending_state)
                            stored_state["is_refinement"] = True
                            stored_state["pending_confirmation"] = False
                            await ctx.set_shared_state(CLARIFICATION_STATE_KEY, stored_state)

                            # Re-submit to query_validator for execution
                            forward_msg = SQLDraftMessage(
                                source="nl2sql_controller",
                                response_json=stored_draft.model_dump_json(),
                            )
                            await ctx.send_message(forward_msg, target_id="query_validator")
                            return

                    # Not an acceptance — treat as a revision/new question
                    logger.info("Confidence gate revision — treating as new question")
                    # Clear the pending confirmation
                    await ctx.set_shared_state(CLARIFICATION_STATE_KEY, None)
            except (KeyError, TypeError):
                pass
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

            # ============================================================
            # Confidence-tier routing (applies before validation pipeline)
            # ============================================================
            if (
                sql_draft.status == "success"
                and sql_draft.parameter_confidences
                and sql_draft_msg.source == "param_extractor"
            ):
                min_conf = min(sql_draft.parameter_confidences.values())
                logger.info(
                    "Confidence routing: min=%.3f, scores=%s",
                    min_conf,
                    sql_draft.parameter_confidences,
                )
                if min_conf < _CONFIDENCE_THRESHOLD_LOW:
                    # Too low — trigger clarification flow
                    logger.info(
                        "Min confidence %.3f < %.2f — converting to needs_clarification",
                        min_conf,
                        _CONFIDENCE_THRESHOLD_LOW,
                    )
                    low_params = sorted(
                        [
                            (name, score)
                            for name, score in sql_draft.parameter_confidences.items()
                            if score < _CONFIDENCE_THRESHOLD_LOW
                        ],
                        key=operator.itemgetter(1),
                    )
                    # Build enriched MissingParameter with best_guess from extracted values
                    # and alternatives from parameter definitions
                    param_defs = {p.name: p for p in sql_draft.parameter_definitions}

                    # Single-question enforcement: ask only about the lowest-confidence
                    # parameter per turn, store the rest for subsequent turns.
                    ask_now = low_params[:1]
                    deferred = low_params[1:]

                    if deferred:
                        logger.info(
                            "Single-question enforcement: asking about %s, deferring %s",
                            [n for n, _ in ask_now],
                            [n for n, _ in deferred],
                        )
                        deferred_data = [{"name": n, "score": s} for n, s in deferred]
                        await ctx.set_shared_state(UNRESOLVED_PARAMS_STATE_KEY, deferred_data)

                    missing: list[MissingParameter] = []
                    for name, score in ask_now:
                        current_value = (sql_draft.extracted_parameters or {}).get(name)
                        pdef = param_defs.get(name)
                        alternatives: list[str] | None = None
                        if pdef and pdef.validation and pdef.validation.allowed_values:
                            alternatives = [
                                v for v in pdef.validation.allowed_values if v != str(current_value)
                            ][:5]
                        missing.append(
                            MissingParameter(
                                name=name,
                                description=(
                                    f"Low confidence ({score:.2f}) "
                                    f"— please confirm the value for '{name}'"
                                ),
                                best_guess=str(current_value)
                                if current_value is not None
                                else None,
                                guess_confidence=score,
                                alternatives=alternatives,
                            )
                        )
                    sql_draft.status = "needs_clarification"
                    sql_draft.missing_parameters = missing
                    sql_draft.clarification_prompt = _format_hypothesis_prompt(missing)
                elif min_conf < _CONFIDENCE_THRESHOLD_HIGH:
                    # Medium confidence — proceed but flag for confirmation
                    sql_draft.needs_confirmation = True
                    logger.info(
                        "Min confidence %.3f in [%.2f, %.2f) — needs_confirmation=True",
                        min_conf,
                        _CONFIDENCE_THRESHOLD_LOW,
                        _CONFIDENCE_THRESHOLD_HIGH,
                    )

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
                                pq = substitute_parameters(
                                    sql_template, sql_draft.extracted_parameters
                                )
                                completed_sql = pq.display_sql
                                sql_draft.completed_sql = completed_sql
                                sql_draft.exec_sql = pq.exec_sql
                                sql_draft.exec_params = list(pq.exec_params)
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
                                    err_msg, err_suggestions = build_error_recovery(
                                        violations, sql_draft.tables_used
                                    )
                                    nl2sql_response = NL2SQLResponse(
                                        sql_query="",
                                        error=err_msg,
                                        query_source="dynamic",
                                        error_suggestions=err_suggestions,
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
                            # Max retries exceeded — build actionable error recovery
                            error_message, recovery_suggestions = build_error_recovery(
                                violations, sql_draft.tables_used
                            )

                            nl2sql_response = NL2SQLResponse(
                                sql_query="",
                                error=error_message,
                                query_source="dynamic",
                                error_suggestions=recovery_suggestions,
                            )

                            logger.error(
                                "Query validation failed after retry: %s",
                                "; ".join(violations),
                            )
                            await self._send_final_response(nl2sql_response, ctx)
                    else:
                        # Query is valid - check confidence gate for dynamic queries
                        logger.info("Validation passed. SQL: %s", completed_sql[:200])

                        # Confidence gate: low-confidence dynamic queries need confirmation
                        is_refinement = False
                        try:
                            existing_state = await ctx.get_shared_state(CLARIFICATION_STATE_KEY)
                            if existing_state and isinstance(existing_state, dict):
                                is_refinement = existing_state.get("is_refinement", False)
                                # Also skip gate if this is an accepted pending query
                                if existing_state.get("pending_confirmation"):
                                    is_refinement = True
                        except (KeyError, TypeError):
                            pass

                        if (
                            sql_draft.source == "dynamic"
                            and sql_draft.confidence < _DYNAMIC_CONFIDENCE_THRESHOLD
                            and not is_refinement
                        ):
                            # Build summary from reasoning or fall back to SQL description
                            query_summary = sql_draft.reasoning or f"Execute: {completed_sql[:150]}"

                            logger.info(
                                "Confidence gate triggered: %.2f < %.2f for dynamic query",
                                sql_draft.confidence,
                                _DYNAMIC_CONFIDENCE_THRESHOLD,
                            )

                            # Store pending query for acceptance
                            await ctx.set_shared_state(
                                CLARIFICATION_STATE_KEY,
                                {
                                    "original_question": sql_draft.user_query,
                                    "pending_confirmation": True,
                                    "dynamic_query": True,
                                    "sql_draft_json": sql_draft.model_dump_json(),
                                    "tables": (
                                        json.loads(sql_draft.tables_metadata_json)
                                        if sql_draft.tables_metadata_json
                                        else []
                                    ),
                                },
                            )

                            # Return confirmation response (no execution)
                            nl2sql_response = NL2SQLResponse(
                                sql_query=completed_sql,
                                needs_clarification=True,
                                query_summary=query_summary,
                                query_confidence=sql_draft.confidence,
                                query_source="dynamic",
                                tables_used=sql_draft.tables_used,
                                tables_metadata_json=sql_draft.tables_metadata_json,
                                original_question=sql_draft.user_query,
                            )
                            await self._send_final_response(nl2sql_response, ctx)
                            return

                        # Execute the SQL
                        logger.info("Executing SQL: %s", completed_sql[:200])

                        # Prefer parameterized execution when bind params are available
                        exec_query = sql_draft.exec_sql or completed_sql
                        exec_params = sql_draft.exec_params or None
                        sql_result = await execute_query_parameterized(exec_query, exec_params)

                        # Clear clarification state
                        with contextlib.suppress(Exception):
                            await ctx.set_shared_state(CLARIFICATION_STATE_KEY, None)

                        query_source = "template" if sql_draft.template_id else "dynamic"
                        confidence = (
                            _CONFIDENCE_THRESHOLD_HIGH
                            if sql_draft.template_id
                            else max(sql_draft.confidence, _CONFIDENCE_THRESHOLD_LOW + 0.1)
                        )

                        # Pass QueryBuilder confidence to response for dynamic queries
                        query_confidence = (
                            sql_draft.confidence if sql_draft.source == "dynamic" else 0.0
                        )

                        # Build human-readable defaults description
                        defaults_description = _format_defaults_for_display(sql_draft.defaults_used)

                        # Build confirmation note for medium-confidence params
                        confirmation_note = ""
                        if sql_draft.needs_confirmation:
                            confirmation_note = _format_confirmation_note(
                                sql_draft.parameter_confidences,
                                sql_draft.extracted_parameters,
                            )

                        # Apply column refinement for dynamic queries
                        result_columns = sql_result.get("columns", [])
                        result_rows = sql_result.get("rows", [])
                        hidden_columns: list[str] = []

                        if sql_draft.source == "dynamic" and sql_result.get("success"):
                            refinement = refine_columns(
                                columns=result_columns,
                                rows=result_rows,
                                user_query=sql_draft.user_query,
                                sql=completed_sql,
                            )
                            result_columns = refinement.columns
                            hidden_columns = refinement.hidden_columns

                        nl2sql_response = NL2SQLResponse(
                            sql_query=completed_sql,
                            sql_response=result_rows,
                            columns=result_columns,
                            row_count=sql_result.get("row_count", 0),
                            confidence_score=confidence,
                            query_confidence=query_confidence,
                            hidden_columns=hidden_columns,
                            query_source=query_source,
                            error=sql_result.get("error")
                            if not sql_result.get("success")
                            else None,
                            defaults_used=defaults_description,
                            confirmation_note=confirmation_note,
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

                # Single-question enforcement: if there are multiple missing params
                # from the LLM response, ask only about the first and defer the rest.
                if len(missing_params) > 1:
                    deferred_mp = [mp.model_dump() for mp in missing_params[1:]]
                    logger.info(
                        "Single-question enforcement (LLM path): asking about '%s', "
                        "deferring %d params",
                        missing_params[0].name,
                        len(deferred_mp),
                    )
                    await ctx.set_shared_state(UNRESOLVED_PARAMS_STATE_KEY, deferred_mp)
                    missing_params = missing_params[:1]

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

                # Build a friendly clarification prompt using hypothesis-first format
                if missing_params and any(mp.best_guess for mp in missing_params):
                    clarification_prompt = _format_hypothesis_prompt(missing_params)
                elif allowed_values:
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

                # Update clarification state with extracted params for handle_clarification path
                try:
                    existing_state = await ctx.get_shared_state(CLARIFICATION_STATE_KEY)
                    if existing_state and isinstance(existing_state, dict):
                        existing_state["extracted_parameters"] = (
                            sql_draft.extracted_parameters or {}
                        )
                        await ctx.set_shared_state(CLARIFICATION_STATE_KEY, existing_state)
                except (KeyError, TypeError):
                    pass

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

            # Retrieve previously extracted parameters from clarification state
            prev_extracted = clarification_state.get("extracted_parameters", {})

            # Combine original question with clarification
            enriched_query = f"{original_question} (Additional info: {clarification})"
            if prev_extracted:
                hints = ", ".join(f"{k}={v}" for k, v in prev_extracted.items())
                enriched_query += f" (Already known: {hints})"

            logger.info(
                "Re-submitting with clarification. Original: '%s', Clarification: '%s', "
                "preserved params: %s",
                original_question[:50],
                clarification[:50],
                list(prev_extracted),
            )

            # Build new extraction request with enriched query and preserved params
            extraction_request = ParameterExtractionRequest(
                user_query=enriched_query,
                template=template,
                previously_extracted=prev_extracted,
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

            # Retrieve previously extracted parameters from the ClarificationRequest
            prev_extracted = original_request.extracted_parameters or {}

            # Combine original question with clarification for better extraction
            enriched_query = f"{original_request.original_question} (Answer: {user_response})"
            if prev_extracted:
                hints = ", ".join(f"{k}={v}" for k, v in prev_extracted.items())
                enriched_query += f" (Already known: {hints})"

            logger.info(
                "Re-submitting with clarification. Original: '%s', User response: '%s', "
                "preserved params: %s",
                original_request.original_question[:50],
                user_response[:50],
                list(prev_extracted),
            )

            # Store clarification state for the extraction (include extracted params)
            await ctx.set_shared_state(
                CLARIFICATION_STATE_KEY,
                {
                    "original_question": original_request.original_question,
                    "template": template_data,
                    "clarification_response": user_response,
                    "extracted_parameters": prev_extracted,
                },
            )

            # Build new extraction request with enriched query and preserved params
            extraction_request = ParameterExtractionRequest(
                user_query=enriched_query,
                template=template,
                previously_extracted=prev_extracted,
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
            query_source=query_source,
            error=error,
        )
