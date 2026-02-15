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

from models import (
    ExtractionRequestMessage,
    MissingParameter,
    ParameterDefinition,
    ParameterExtractionRequest,
    QueryTemplate,
    SQLDraft,
    SQLDraftMessage,
)

logger = logging.getLogger(__name__)

MIN_PARAM_NAME_LENGTH = 2


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


# Shared state key for Foundry thread ID (V2 uses conversation_id internally)
FOUNDRY_CONVERSATION_ID_KEY = "foundry_conversation_id"

# Key used by Agent Framework for workflow.run_stream() kwargs
WORKFLOW_RUN_KWARGS_KEY = "_workflow_run_kwargs"


# ============================================================================
# Deterministic Fuzzy Matching (Step 1 - before LLM)
# ============================================================================


def _fuzzy_match_allowed_value(user_query: str, allowed_values: list[str]) -> str | None:
    """
    Try to match user query words to an allowed value.

    Returns the matched value or None if no confident match.
    """
    query_lower = user_query.lower()

    for allowed in allowed_values:
        allowed_lower = allowed.lower()

        # Exact match (case-insensitive)
        if allowed_lower in query_lower:
            return allowed

        # Pluralization: "supermarkets" -> "Supermarket"
        if allowed_lower + "s" in query_lower:
            return allowed

        # Word stems: "Computer Store" matches "computers"
        words = allowed_lower.split()
        if words:
            first_word = words[0]
            if first_word + "s" in query_lower:
                return allowed
            # Also check without trailing 's' in query: "novelty" -> "Novelty Shop"
            if first_word in query_lower:
                return allowed

    return None


# ============================================================================
# Confidence Scoring
# ============================================================================

# Maps resolution method to base confidence score (from plan.md D1)
_RESOLUTION_CONFIDENCE: dict[str, float] = {
    "exact_match": 1.0,
    "fuzzy_match": 0.85,
    "llm_validated": 0.75,
    "default_value": 0.7,
    "default_policy": 0.7,
    "llm_unvalidated": 0.65,
    "llm_failed_validation": 0.3,
}


def _compute_confidence(resolution_method: str, confidence_weight: float) -> float:
    """Compute effective confidence for a resolved parameter.

    Effective confidence = base_confidence * max(confidence_weight, 0.3).

    Args:
        resolution_method: How the value was resolved (e.g. "exact_match").
        confidence_weight: Per-parameter weight from ParameterDefinition.

    Returns:
        Effective confidence score (0.0-1.0).

    Raises:
        ValueError: If resolution_method is not recognised.
    """
    base = _RESOLUTION_CONFIDENCE.get(resolution_method)
    if base is None:
        raise ValueError(
            f"Unknown resolution method: {resolution_method!r}. "
            f"Valid methods: {sorted(_RESOLUTION_CONFIDENCE)}"
        )
    return base * max(confidence_weight, 0.3)


def _has_validation_rules(param: ParameterDefinition) -> bool:
    """Check whether a parameter definition has any validation rules."""
    if not param.validation:
        return False
    v = param.validation
    return bool(v.allowed_values or v.min is not None or v.max is not None or v.regex)


def _value_passes_validation(value: str | int | float, param: ParameterDefinition) -> bool:
    """Check if a value passes a parameter's validation rules.

    Returns True when there are no validation rules or
    all applicable rules pass.
    """
    if not param.validation:
        return True
    v = param.validation

    # Check allowed_values
    if v.allowed_values:
        str_val = str(value).lower()
        if not any(a.lower() == str_val for a in v.allowed_values):
            return False

    # Check min/max for numeric types
    if v.type == "integer":
        try:
            num = int(value)
        except (ValueError, TypeError):
            return False
        in_range = (v.min is None or num >= int(v.min)) and (v.max is None or num <= int(v.max))
        if not in_range:
            return False

    # Check regex
    return not (v.regex and not re.search(v.regex, str(value)))


def _build_parameter_confidences(
    resolution_methods: dict[str, str],
    template: QueryTemplate,
) -> dict[str, float]:
    """Compute per-parameter confidence scores.

    Args:
        resolution_methods: Mapping of param name → resolution method.
        template: The query template (used for confidence_weight lookup).

    Returns:
        Mapping of param name → effective confidence score.
    """
    weight_by_name = {p.name: p.confidence_weight for p in template.parameters}
    return {
        name: _compute_confidence(method, weight_by_name.get(name, 1.0))
        for name, method in resolution_methods.items()
    }


def _extract_number_from_query(user_query: str, param_name: str) -> int | None:
    """
    Extract a number from the user query for count-type parameters.

    Handles patterns like "top 5", "first 10", "last 30 days".
    """
    query_lower = user_query.lower()

    # Common patterns for counts
    patterns = [
        r"top\s+(\d+)",
        r"first\s+(\d+)",
        r"last\s+(\d+)",
        r"(\d+)\s+" + param_name.replace("_", r"\s*"),
    ]

    for pattern in patterns:
        match = re.search(pattern, query_lower)
        if match:
            return int(match.group(1))

    return None


class ExtractionResult:
    """Result of parameter extraction with tracking for defaults and resolution methods."""

    def __init__(self) -> None:
        self.extracted: dict[str, Any] = {}
        self.defaults_used: dict[str, Any] = {}
        self.resolution_methods: dict[str, str] = {}


def _pre_extract_parameters(user_query: str, template: QueryTemplate) -> ExtractionResult:
    """
    Deterministically extract parameters before calling LLM.

    Returns ExtractionResult with extracted values, defaults used,
    and resolution methods for each resolved parameter.
    """
    result = ExtractionResult()

    for param in template.parameters:
        # Try to match allowed_values
        if param.validation and param.validation.allowed_values:
            match = _fuzzy_match_allowed_value(user_query, param.validation.allowed_values)
            if match:
                result.extracted[param.name] = match
                # Determine if exact or fuzzy
                query_lower = user_query.lower()
                if match.lower() in query_lower:
                    result.resolution_methods[param.name] = "exact_match"
                else:
                    result.resolution_methods[param.name] = "fuzzy_match"
                continue

        # Try to extract numbers for integer params
        if param.validation and param.validation.type == "integer":
            num = _extract_number_from_query(user_query, param.name)
            if num is not None:
                # Validate against min/max (cast to int for comparison)
                min_val = param.validation.min
                max_val = param.validation.max
                if min_val is not None and num < int(min_val):
                    continue
                if max_val is not None and num > int(max_val):
                    continue
                result.extracted[param.name] = num
                result.resolution_methods[param.name] = "exact_match"
                continue

        # Apply default_policy if available
        if param.default_policy is not None and param.name not in result.extracted:
            result.extracted[param.name] = param.default_policy
            result.defaults_used[param.name] = param.default_policy
            result.resolution_methods[param.name] = "default_policy"
            continue

        # Apply default_value if available
        if param.default_value is not None and param.name not in result.extracted:
            result.extracted[param.name] = param.default_value
            result.defaults_used[param.name] = param.default_value
            result.resolution_methods[param.name] = "default_value"

    return result


def _all_required_params_satisfied(extracted: dict[str, Any], template: QueryTemplate) -> bool:
    """Check if all required parameters are satisfied."""
    for param in template.parameters:
        if param.required and param.name not in extracted and param.default_value is None:
            return False
    return True


# ============================================================================
# Prompt Building and LLM Response Parsing
# ============================================================================


def _load_prompt() -> str:
    """Load prompt from prompt.md in this folder."""
    prompt_path = Path(__file__).parent / "prompt.md"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


def _build_extraction_prompt(user_query: str, template: QueryTemplate) -> str:
    """
    Build a compact prompt for the LLM to extract parameters.

    Args:
        user_query: The user's original question
        template: The matched query template

    Returns:
        A formatted prompt string for the LLM
    """
    # Calculate adjusted reference date (12 years ago for historical data)
    adjusted_date = datetime.now() - timedelta(days=12 * 365)
    adjusted_date_str = adjusted_date.strftime("%Y-%m-%d")

    # Format parameters compactly - only include what's needed for extraction
    params_info = []
    for param in template.parameters:
        param_desc = {
            "name": param.name,
            "required": param.required,
            "ask_if_missing": param.ask_if_missing,
        }
        if param.default_value is not None:
            param_desc["default"] = param.default_value
        if param.validation:
            # Only include relevant validation fields
            v = param.validation
            if v.allowed_values:
                param_desc["allowed_values"] = v.allowed_values
            if v.min is not None:
                param_desc["min"] = v.min
            if v.max is not None:
                param_desc["max"] = v.max
        params_info.append(param_desc)

    return f"""Question: {user_query}
Reference date: {adjusted_date_str}
Parameters: {json.dumps(params_info)}

Extract parameter values. Respond with JSON only."""


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
            str_value = value.upper() if value.upper() in {"ASC", "DESC", "NULL"} else str(value)
        else:
            str_value = str(value)

        result = result.replace(token, str_value)

    return result


class ParameterExtractorExecutor(Executor):
    """
    Executor that extracts parameter values from user queries.

    This executor:
    1. Receives user query + query template from NL2SQLController
    2. Uses LLM to analyze the query and extract parameter values
    3. Validates extracted values against parameter definitions
    4. Returns completed SQL or clarification request
    """

    agent: ChatAgent

    def __init__(
        self, chat_client: AzureAIAgentClient, executor_id: str = "param_extractor"
    ) -> None:
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
            logger.info("ParamExtractor stored Foundry thread ID: %s", thread.service_thread_id)

    @handler
    async def handle_extraction_request(
        self, request_msg: ExtractionRequestMessage, ctx: WorkflowContext[SQLDraftMessage]
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
            request = ParameterExtractionRequest.model_validate(request_data)
            template = request.template
            user_query = request.user_query

            logger.info(
                "Extracting parameters for template '%s' from query: %s",
                template.intent,
                user_query[:100],
            )

            # ================================================================
            # Step 1: Try deterministic fuzzy matching first (fast path)
            # ================================================================
            extraction_result = _pre_extract_parameters(user_query, template)
            extracted_params = extraction_result.extracted
            defaults_used = extraction_result.defaults_used

            # Merge previously extracted params (from prior clarification turns)
            # These take priority since the user already confirmed them.
            if request.previously_extracted:
                for pname, pval in request.previously_extracted.items():
                    if pname not in extracted_params:
                        extracted_params[pname] = pval
                        extraction_result.resolution_methods[pname] = "exact_match"
                        logger.info(
                            "  Preserved param '%s' -> '%s' from prior turn",
                            pname,
                            pval,
                        )

            if extracted_params:
                logger.info("Deterministic extraction found %d parameters:", len(extracted_params))
                for param_name, param_value in extracted_params.items():
                    is_default = " (default)" if param_name in defaults_used else ""
                    logger.info("  Parameter '%s' -> '%s'%s", param_name, param_value, is_default)

            if _all_required_params_satisfied(extracted_params, template):
                # All required params found - skip LLM entirely!
                logger.info(
                    "All required parameters satisfied via deterministic matching - skipping LLM"
                )

                confidences = _build_parameter_confidences(
                    extraction_result.resolution_methods, template
                )

                sql_draft = SQLDraft(
                    status="success",
                    source="template",
                    completed_sql=None,  # SQL substitution done by nl2sql_controller
                    user_query=user_query,
                    reasoning=template.reasoning,
                    template_id=template.id,
                    template_json=template.model_dump_json(),
                    extracted_parameters=extracted_params,
                    defaults_used=defaults_used,
                    parameter_definitions=template.parameters,
                    parameter_confidences=confidences,
                )

                finish_step()
                response_msg = SQLDraftMessage(
                    source="param_extractor", response_json=sql_draft.model_dump_json()
                )
                await ctx.send_message(response_msg)
                return

            # ================================================================
            # Step 2: Fall back to LLM for complex/ambiguous cases
            # ================================================================
            logger.info("Deterministic matching incomplete - falling back to LLM")

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

            # Log the full prompt for debugging
            logger.info("Extraction prompt:\n%s", extraction_prompt)

            # Run the LLM to extract parameters
            response = await self.agent.run(extraction_prompt, thread=thread, metadata=metadata)

            # Store thread ID
            await self._store_thread_id(ctx, thread)

            # Get the response text
            response_text = ""
            for msg in response.messages:
                if hasattr(msg, "contents"):
                    for content in msg.contents:
                        # Use getattr to safely access text attribute
                        text_value = getattr(content, "text", None)
                        if text_value:
                            response_text = text_value
                            break
                    if response_text:
                        break

            # Log the raw LLM response for debugging
            logger.info("LLM response: %s", response_text[:500] if response_text else "(empty)")

            # Parse the LLM response
            parsed = _parse_llm_response(response_text)
            logger.info(
                "Parsed response: status=%s, params=%s",
                parsed.get("status"),
                parsed.get("extracted_parameters"),
            )

            # Build the response based on LLM output
            if parsed.get("status") == "success":
                # Return extracted parameters - SQL substitution happens in nl2sql_controller
                extracted_params = parsed.get("extracted_parameters", {})

                # Merge deterministic extractions (they take priority)
                merged_params = dict(extraction_result.extracted)
                merged_params.update({
                    k: v for k, v in extracted_params.items() if k not in merged_params
                })
                extracted_params = merged_params

                # Log each extracted parameter
                logger.info("Extracted %d parameters:", len(extracted_params))
                for param_name, param_value in extracted_params.items():
                    logger.info("  Parameter '%s' -> '%s'", param_name, param_value)

                # Build param lookup for resolution method assignment
                param_defs_by_name: dict[str, ParameterDefinition] = {
                    p.name: p for p in template.parameters
                }

                # Carry forward deterministic resolution methods
                resolution_methods = dict(extraction_result.resolution_methods)

                # Assign LLM resolution methods for new params
                for pname, pval in extracted_params.items():
                    if pname in resolution_methods:
                        continue  # already resolved deterministically
                    pdef = param_defs_by_name.get(pname)
                    if pdef and _has_validation_rules(pdef):
                        if _value_passes_validation(pval, pdef):
                            resolution_methods[pname] = "llm_validated"
                        else:
                            resolution_methods[pname] = "llm_failed_validation"
                    else:
                        resolution_methods[pname] = "llm_unvalidated"

                # Safety check: verify required ask_if_missing params were actually extracted
                missing_required = []
                for param in template.parameters:
                    if (
                        param.required
                        and param.ask_if_missing
                        and (
                            param.name not in extracted_params
                            or not extracted_params.get(param.name)
                        )
                    ):
                        # Required param with ask_if_missing was not extracted
                        param_allowed: list[str] = []
                        if param.validation and hasattr(param.validation, "allowed_values"):
                            param_allowed = param.validation.allowed_values or []

                        # Try fuzzy match for best_guess
                        best_guess: str | None = None
                        guess_confidence = 0.0
                        alternatives: list[str] | None = None
                        if param_allowed:
                            fuzzy = _fuzzy_match_allowed_value(user_query, param_allowed)
                            if fuzzy:
                                best_guess = fuzzy
                                guess_confidence = 0.6
                            remaining = [v for v in param_allowed if v != best_guess]
                            alternatives = remaining[:5]

                        missing_required.append(
                            MissingParameter(
                                name=param.name,
                                description=f"Please provide a value for '{param.name}'",
                                validation_hint=f"Allowed values: {', '.join(param_allowed)}"
                                if param_allowed
                                else "",
                                best_guess=best_guess,
                                guess_confidence=guess_confidence,
                                alternatives=alternatives,
                            )
                        )
                        logger.warning(
                            "LLM returned success but required param '%s' (ask_if_missing=true) was not extracted",
                            param.name,
                        )

                # Compute confidence scores for all resolved params
                confidences = _build_parameter_confidences(resolution_methods, template)

                if missing_required:
                    # Convert to needs_clarification
                    logger.info(
                        "Converting success to needs_clarification due to %d missing required params",
                        len(missing_required),
                    )
                    sql_draft = SQLDraft(
                        status="needs_clarification",
                        source="template",
                        user_query=user_query,
                        reasoning=template.reasoning,
                        template_id=template.id,
                        template_json=template.model_dump_json(),
                        extracted_parameters=extracted_params,
                        parameter_definitions=template.parameters,
                        missing_parameters=missing_required,
                        clarification_prompt=f"Please provide a value for: {missing_required[0].name}",
                        parameter_confidences=confidences,
                    )
                else:
                    sql_draft = SQLDraft(
                        status="success",
                        source="template",
                        completed_sql=None,  # SQL substitution done by nl2sql_controller
                        user_query=user_query,
                        reasoning=template.reasoning,
                        template_id=template.id,
                        template_json=template.model_dump_json(),
                        extracted_parameters=extracted_params,
                        parameter_definitions=template.parameters,
                        parameter_confidences=confidences,
                    )

            elif parsed.get("status") == "needs_clarification":
                # Build missing parameters list with hypothesis-first fields
                missing = [
                    MissingParameter(
                        name=mp.get("name", ""),
                        description=mp.get("description", ""),
                        validation_hint=mp.get("validation_hint", ""),
                        best_guess=mp.get("best_guess"),
                        guess_confidence=float(mp.get("guess_confidence", 0.0)),
                        alternatives=mp.get("alternatives"),
                    )
                    for mp in parsed.get("missing_parameters", [])
                ]

                # Merge any LLM-extracted params with deterministic extractions
                llm_extracted = parsed.get("extracted_parameters") or {}
                merged_extracted = dict(extraction_result.extracted)
                merged_extracted.update({
                    k: v for k, v in llm_extracted.items() if k not in merged_extracted
                })

                sql_draft = SQLDraft(
                    status="needs_clarification",
                    source="template",
                    user_query=user_query,
                    reasoning=template.reasoning,
                    template_id=template.id,
                    template_json=template.model_dump_json(),
                    extracted_parameters=merged_extracted or None,
                    parameter_definitions=template.parameters,
                    missing_parameters=missing,
                    clarification_prompt=parsed.get("clarification_prompt"),
                )

            else:
                # Error case - check if we should ask for clarification instead
                error_msg = parsed.get("error", "Unknown error during parameter extraction")

                # Check if error is about invalid value for a parameter with ask_if_missing=true
                # Also check if any required parameter with ask_if_missing wasn't extracted
                should_clarify = False
                clarify_param = None

                for param in template.parameters:
                    if param.required and param.ask_if_missing:
                        # Check if this param name (or a substring) appears in error message
                        # e.g., "category_name" should match "No matching category found"
                        param_name_parts = param.name.lower().replace("_", " ").split()
                        error_lower = error_msg.lower()

                        # Match if any significant part of param name is in error
                        matches_error = any(
                            part in error_lower
                            for part in param_name_parts
                            if len(part) > MIN_PARAM_NAME_LENGTH
                        )

                        # Or if the parameter simply wasn't extracted
                        not_extracted = param.name not in parsed.get("extracted_parameters", {})

                        if matches_error or not_extracted:
                            should_clarify = True
                            clarify_param = param
                            logger.info(
                                "Error mentions param '%s' or param not extracted (matches_error=%s, not_extracted=%s)",
                                param.name,
                                matches_error,
                                not_extracted,
                            )
                            break

                if should_clarify and clarify_param:
                    # Convert error to clarification request
                    logger.info(
                        "Converting error to clarification for parameter '%s' (ask_if_missing=true)",
                        clarify_param.name,
                    )

                    # Get allowed values
                    clarify_allowed: list[str] = []
                    if clarify_param.validation and hasattr(
                        clarify_param.validation, "allowed_values"
                    ):
                        clarify_allowed = clarify_param.validation.allowed_values or []

                    # Try fuzzy match for best_guess
                    err_best_guess: str | None = None
                    err_confidence = 0.0
                    err_alternatives: list[str] | None = None
                    if clarify_allowed:
                        fuzzy = _fuzzy_match_allowed_value(user_query, clarify_allowed)
                        if fuzzy:
                            err_best_guess = fuzzy
                            err_confidence = 0.6
                        remaining = [v for v in clarify_allowed if v != err_best_guess]
                        err_alternatives = remaining[:5]

                    missing = [
                        MissingParameter(
                            name=clarify_param.name,
                            description=f"Please select a valid value for '{clarify_param.name}'",
                            validation_hint=f"Allowed values: {', '.join(clarify_allowed)}"
                            if clarify_allowed
                            else "",
                            best_guess=err_best_guess,
                            guess_confidence=err_confidence,
                            alternatives=err_alternatives,
                        )
                    ]

                    clarification_prompt = (
                        f"Please choose from: {', '.join(clarify_allowed)}"
                        if clarify_allowed
                        else f"Please provide a value for '{clarify_param.name}'"
                    )

                    sql_draft = SQLDraft(
                        status="needs_clarification",
                        source="template",
                        user_query=user_query,
                        reasoning=template.reasoning,
                        template_id=template.id,
                        template_json=template.model_dump_json(),
                        extracted_parameters=parsed.get("extracted_parameters", {}),
                        parameter_definitions=template.parameters,
                        missing_parameters=missing,
                        clarification_prompt=clarification_prompt,
                    )
                else:
                    # Regular error - no clarification possible
                    sql_draft = SQLDraft(
                        status="error",
                        source="template",
                        user_query=user_query,
                        template_id=template.id,
                        template_json=template.model_dump_json(),
                        parameter_definitions=template.parameters,
                        error=error_msg,
                    )

            logger.info("Parameter extraction completed with status: %s", sql_draft.status)

        except Exception as e:
            logger.exception("Parameter extraction error")
            sql_draft = SQLDraft(
                status="error",
                source="template",
                error=str(e),
            )

        finish_step()

        # Send the response back to NL2SQL executor using typed wrapper
        response_msg = SQLDraftMessage(
            source="param_extractor", response_json=sql_draft.model_dump_json()
        )
        await ctx.send_message(response_msg)
