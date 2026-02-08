"""
Parameter Validator Executor for workflow integration.

This executor validates extracted parameter values against their validation rules
using REGEX, type checks, range validation, and allowed value lists.
No LLM is used - all validation is done programmatically.

Note: Do NOT use 'from __future__ import annotations' in this module.
The Agent Framework's @handler decorator validates WorkflowContext type annotations
at class definition time, which is incompatible with PEP 563 stringified annotations.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any

from agent_framework import (
    Executor,
    WorkflowContext,
    handler,
)
from models import (
    ParameterDefinition,
    ParameterValidation,
    SQLDraft,
    SQLDraftMessage,
)

logger = logging.getLogger(__name__)


def _validate_integer(value: Any, validation: ParameterValidation, param_name: str) -> list[str]:
    """
    Validate an integer parameter value.

    Returns:
        List of validation error messages (empty if valid)
    """
    violations = []

    # Type check
    if not isinstance(value, (int, float)):
        try:
            value = int(value)
        except (ValueError, TypeError):
            violations.append(
                f"Parameter '{param_name}': expected integer, got '{type(value).__name__}'"
            )
            return violations

    # Coerce float to int if it's a whole number
    if isinstance(value, float):
        if value != int(value):
            violations.append(f"Parameter '{param_name}': expected integer, got float with decimal")
            return violations
        value = int(value)

    # Range validation
    if validation.min is not None:
        try:
            min_val = int(validation.min)
            if value < min_val:
                violations.append(
                    f"Parameter '{param_name}': value {value} is below minimum {min_val}"
                )
        except (ValueError, TypeError):
            pass

    if validation.max is not None:
        try:
            max_val = int(validation.max)
            if value > max_val:
                violations.append(
                    f"Parameter '{param_name}': value {value} exceeds maximum {max_val}"
                )
        except (ValueError, TypeError):
            pass

    return violations


def _validate_string(value: Any, validation: ParameterValidation, param_name: str) -> list[str]:
    """
    Validate a string parameter value.

    Returns:
        List of validation error messages (empty if valid)
    """
    violations = []

    # Convert to string if needed
    str_value = str(value)

    # Allowed values check
    if validation.allowed_values:
        # Case-insensitive comparison
        allowed_upper = [v.upper() for v in validation.allowed_values]
        if str_value.upper() not in allowed_upper:
            allowed_list = ", ".join(f"'{v}'" for v in validation.allowed_values)
            violations.append(
                f"Parameter '{param_name}': value '{str_value}' not in allowed values: {allowed_list}"
            )

    # Regex validation
    if validation.regex:
        try:
            if not re.match(validation.regex, str_value):
                violations.append(
                    f"Parameter '{param_name}': value '{str_value}' does not match pattern '{validation.regex}'"
                )
        except re.error as e:
            violations.append(f"Parameter '{param_name}': invalid regex pattern: {e}")

    return violations


def _parse_date(value: Any) -> datetime | None:
    """
    Try to parse a date value from various formats.

    Returns:
        datetime object or None if parsing fails
    """
    if isinstance(value, datetime):
        return value

    if not isinstance(value, str):
        return None

    # Common date formats to try
    formats = [
        "%Y-%m-%d",  # 2024-01-15
        "%Y/%m/%d",  # 2024/01/15
        "%m/%d/%Y",  # 01/15/2024
        "%d/%m/%Y",  # 15/01/2024
        "%Y-%m-%d %H:%M:%S",  # 2024-01-15 10:30:00
        "%Y-%m-%dT%H:%M:%S",  # 2024-01-15T10:30:00
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


def _validate_date(value: Any, validation: ParameterValidation, param_name: str) -> list[str]:
    """
    Validate a date parameter value.

    Returns:
        List of validation error messages (empty if valid)
    """
    violations = []

    # Skip SQL expressions like GETDATE(), DATEADD, etc.
    if isinstance(value, str):
        sql_functions = ["GETDATE", "DATEADD", "DATEDIFF", "CURRENT_DATE", "NOW"]
        if any(func in value.upper() for func in sql_functions):
            return violations

    parsed_date = _parse_date(value)
    if parsed_date is None:
        violations.append(f"Parameter '{param_name}': could not parse date value '{value}'")
        return violations

    # Range validation
    if validation.min is not None:
        min_date = _parse_date(validation.min)
        if min_date and parsed_date < min_date:
            violations.append(
                f"Parameter '{param_name}': date {value} is before minimum {validation.min}"
            )

    if validation.max is not None:
        max_date = _parse_date(validation.max)
        if max_date and parsed_date > max_date:
            violations.append(
                f"Parameter '{param_name}': date {value} is after maximum {validation.max}"
            )

    return violations


def _validate_float(value: Any, validation: ParameterValidation, param_name: str) -> list[str]:
    """
    Validate a float/decimal parameter value.

    Returns:
        List of validation error messages (empty if valid)
    """
    violations = []

    # Type check
    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (ValueError, TypeError):
            violations.append(
                f"Parameter '{param_name}': expected number, got '{type(value).__name__}'"
            )
            return violations

    # Range validation
    if validation.min is not None:
        try:
            min_val = float(validation.min)
            if value < min_val:
                violations.append(
                    f"Parameter '{param_name}': value {value} is below minimum {min_val}"
                )
        except (ValueError, TypeError):
            pass

    if validation.max is not None:
        try:
            max_val = float(validation.max)
            if value > max_val:
                violations.append(
                    f"Parameter '{param_name}': value {value} exceeds maximum {max_val}"
                )
        except (ValueError, TypeError):
            pass

    return violations


def _validate_parameter(param_name: str, value: Any, definition: ParameterDefinition) -> list[str]:
    """
    Validate a single parameter value against its definition.

    Args:
        param_name: The parameter name
        value: The extracted value
        definition: The parameter definition with validation rules

    Returns:
        List of validation error messages (empty if valid)
    """
    violations = []

    # Check if required and missing
    if definition.required and value is None:
        if not definition.ask_if_missing:
            violations.append(f"Parameter '{param_name}': required value is missing")
        return violations

    # Skip validation if no value
    if value is None:
        return violations

    # Skip validation if no validation rules defined
    if not definition.validation:
        return violations

    validation = definition.validation

    # Dispatch based on type
    val_type = validation.type.lower()

    if val_type == "integer":
        violations.extend(_validate_integer(value, validation, param_name))
    elif val_type == "string":
        violations.extend(_validate_string(value, validation, param_name))
    elif val_type == "date":
        violations.extend(_validate_date(value, validation, param_name))
    elif val_type in ("float", "decimal", "number"):
        violations.extend(_validate_float(value, validation, param_name))
    else:
        # Unknown type - log warning but don't fail
        logger.warning("Unknown validation type '%s' for parameter '%s'", val_type, param_name)

    return violations


def validate_all_parameters(
    extracted_parameters: dict[str, Any], parameter_definitions: list[ParameterDefinition]
) -> tuple[bool, list[str]]:
    """
    Validate all extracted parameters against their definitions.

    Args:
        extracted_parameters: Dict of parameter name -> extracted value
        parameter_definitions: List of parameter definitions with validation rules

    Returns:
        Tuple of (is_valid, list of violation messages)
    """
    all_violations = []

    # Build lookup for definitions
    def_lookup = {d.name: d for d in parameter_definitions}

    # Validate each extracted parameter
    for param_name, value in extracted_parameters.items():
        definition = def_lookup.get(param_name)
        if not definition:
            # Parameter not in definitions - could be extra data from LLM
            logger.warning("Extracted parameter '%s' not found in definitions", param_name)
            continue

        violations = _validate_parameter(param_name, value, definition)
        all_violations.extend(violations)

    # Check for required parameters that weren't extracted
    for definition in parameter_definitions:
        if definition.required and definition.name not in extracted_parameters:
            # Only flag if there's no default
            if definition.default_value is None and not definition.ask_if_missing:
                all_violations.append(f"Parameter '{definition.name}': required but not provided")

    return len(all_violations) == 0, all_violations


class ParameterValidatorExecutor(Executor):
    """
    Executor that validates extracted parameter values.

    This executor:
    1. Receives SQLDraft with extracted parameters from param_extractor
    2. Validates each parameter against its definition rules (type, range, regex, etc.)
    3. Returns validated SQLDraft or SQLDraft with violations

    No LLM is used - all validation is done programmatically.
    """

    def __init__(self, executor_id: str = "param_validator"):
        """
        Initialize the Parameter Validator executor.

        Args:
            executor_id: Executor ID for workflow routing
        """
        super().__init__(id=executor_id)
        logger.info("ParameterValidatorExecutor initialized")

    @handler
    async def handle_validation_request(
        self, request_msg: SQLDraftMessage, ctx: WorkflowContext[SQLDraftMessage]
    ) -> None:
        """
        Handle a parameter validation request.

        Args:
            request_msg: Wrapped JSON string containing SQLDraft
            ctx: Workflow context for sending the response
        """
        logger.info("ParameterValidatorExecutor received validation request")

        # Emit step start event
        step_name = "Validating parameters"
        emit_step_end_fn = None
        try:
            from api.step_events import emit_step_end, emit_step_start

            emit_step_start(step_name)
            emit_step_end_fn = emit_step_end
        except ImportError:
            pass

        def finish_step():
            if emit_step_end_fn:
                emit_step_end_fn(step_name)

        try:
            # Parse the request
            draft_data = json.loads(request_msg.response_json)
            draft = SQLDraft.model_validate(draft_data)

            logger.info(
                "Validating parameters for query (source=%s, template_id=%s)",
                draft.source,
                draft.template_id,
            )

            # Get extracted parameters and definitions
            extracted_params = draft.extracted_parameters or {}
            param_definitions = draft.parameter_definitions or []

            if not param_definitions:
                # No definitions to validate against - mark as validated and pass through
                logger.info("No parameter definitions provided, skipping validation")
                validated_draft = SQLDraft(
                    status=draft.status,
                    source=draft.source,
                    completed_sql=draft.completed_sql,
                    user_query=draft.user_query,
                    reasoning=draft.reasoning,
                    retry_count=draft.retry_count,
                    template_id=draft.template_id,
                    template_json=draft.template_json,
                    extracted_parameters=draft.extracted_parameters,
                    defaults_used=draft.defaults_used,
                    missing_parameters=draft.missing_parameters,
                    clarification_prompt=draft.clarification_prompt,
                    tables_used=draft.tables_used,
                    params_validated=True,  # Mark as validated
                    parameter_definitions=draft.parameter_definitions,
                    parameter_violations=draft.parameter_violations,
                    error=draft.error,
                )
                finish_step()
                response_msg = SQLDraftMessage(
                    source="param_validator", response_json=validated_draft.model_dump_json()
                )
                await ctx.send_message(response_msg)
                return

            # Validate all parameters
            is_valid, violations = validate_all_parameters(extracted_params, param_definitions)

            if is_valid:
                logger.info("All parameters validated successfully")
                # Update draft with params_validated=True
                validated_draft = SQLDraft(
                    status=draft.status,
                    source=draft.source,
                    completed_sql=draft.completed_sql,
                    user_query=draft.user_query,
                    reasoning=draft.reasoning,
                    retry_count=draft.retry_count,
                    template_id=draft.template_id,
                    template_json=draft.template_json,
                    extracted_parameters=draft.extracted_parameters,
                    defaults_used=draft.defaults_used,
                    missing_parameters=draft.missing_parameters,
                    clarification_prompt=draft.clarification_prompt,
                    tables_used=draft.tables_used,
                    params_validated=True,  # Mark as validated
                    parameter_definitions=draft.parameter_definitions,
                    parameter_violations=[],
                    error=draft.error,
                )
                finish_step()
                response_msg = SQLDraftMessage(
                    source="param_validator", response_json=validated_draft.model_dump_json()
                )
                await ctx.send_message(response_msg)
            else:
                logger.warning("Parameter validation failed: %s", violations)

                # Update the draft with violations
                validated_draft = SQLDraft(
                    status="error",
                    source=draft.source,
                    completed_sql=draft.completed_sql,
                    user_query=draft.user_query,
                    reasoning=draft.reasoning,
                    retry_count=draft.retry_count,
                    template_id=draft.template_id,
                    template_json=draft.template_json,
                    extracted_parameters=draft.extracted_parameters,
                    defaults_used=draft.defaults_used,
                    missing_parameters=draft.missing_parameters,
                    clarification_prompt=draft.clarification_prompt,
                    tables_used=draft.tables_used,
                    parameter_definitions=draft.parameter_definitions,
                    parameter_violations=violations,
                    error=f"Parameter validation failed: {'; '.join(violations)}",
                )

                finish_step()
                response_msg = SQLDraftMessage(
                    source="param_validator", response_json=validated_draft.model_dump_json()
                )
                await ctx.send_message(response_msg)

        except Exception as e:
            logger.error("Parameter validation error: %s", e)

            error_draft = SQLDraft(
                status="error",
                source="template",
                error=f"Parameter validation error: {e!s}",
                parameter_violations=[str(e)],
            )

            finish_step()
            response_msg = SQLDraftMessage(
                source="param_validator", response_json=error_draft.model_dump_json()
            )
            await ctx.send_message(response_msg)
