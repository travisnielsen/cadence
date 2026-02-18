"""Pure parameter validation logic.

Validates extracted parameter values against their definitions
(type checks, range, regex, allowed values). No I/O, no framework
dependencies — suitable for direct unit testing.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from models import ParameterDefinition, ParameterValidation, SQLDraft

logger = logging.getLogger(__name__)


def _validate_integer(
    value: Any,  # noqa: ANN401
    validation: ParameterValidation,
    param_name: str,
) -> list[str]:
    """Validate an integer parameter value.

    Returns:
        List of validation error messages (empty if valid).
    """
    violations: list[str] = []

    if not isinstance(value, (int, float)):
        try:
            value = int(value)
        except (ValueError, TypeError):
            violations.append(
                f"Parameter '{param_name}': expected integer, got '{type(value).__name__}'"
            )
            return violations

    if isinstance(value, float):
        if value != int(value):  # type: ignore[reportUnnecessaryComparison]
            violations.append(f"Parameter '{param_name}': expected integer, got float with decimal")
            return violations
        value = int(value)

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


def _validate_string(
    value: Any,  # noqa: ANN401
    validation: ParameterValidation,
    param_name: str,
) -> list[str]:
    """Validate a string parameter value.

    Returns:
        List of validation error messages (empty if valid).
    """
    violations: list[str] = []
    str_value = str(value)

    if validation.allowed_values:
        allowed_upper = [v.upper() for v in validation.allowed_values]
        if str_value.upper() not in allowed_upper:
            allowed_list = ", ".join(f"'{v}'" for v in validation.allowed_values)
            violations.append(
                f"Parameter '{param_name}': value '{str_value}' "
                f"not in allowed values: {allowed_list}"
            )

    if validation.regex:
        try:
            if not re.match(validation.regex, str_value):
                violations.append(
                    f"Parameter '{param_name}': value '{str_value}' "
                    f"does not match pattern '{validation.regex}'"
                )
        except re.error as e:
            violations.append(f"Parameter '{param_name}': invalid regex pattern: {e}")

    return violations


def _parse_date(value: Any) -> datetime | None:  # noqa: ANN401
    """Try to parse a date value from various formats.

    Returns:
        Parsed datetime, or ``None`` if parsing fails.
    """
    if isinstance(value, datetime):
        return value

    if not isinstance(value, str):
        return None

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


def _validate_date(
    value: Any,  # noqa: ANN401
    validation: ParameterValidation,
    param_name: str,
) -> list[str]:
    """Validate a date parameter value.

    Returns:
        List of validation error messages (empty if valid).
    """
    violations: list[str] = []

    if isinstance(value, str):
        sql_functions = ["GETDATE", "DATEADD", "DATEDIFF", "CURRENT_DATE", "NOW"]
        if any(func in value.upper() for func in sql_functions):
            return violations

    parsed_date = _parse_date(value)
    if parsed_date is None:
        violations.append(f"Parameter '{param_name}': could not parse date value '{value}'")
        return violations

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


def _validate_float(
    value: Any,  # noqa: ANN401
    validation: ParameterValidation,
    param_name: str,
) -> list[str]:
    """Validate a float/decimal parameter value.

    Returns:
        List of validation error messages (empty if valid).
    """
    violations: list[str] = []

    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (ValueError, TypeError):
            violations.append(
                f"Parameter '{param_name}': expected number, got '{type(value).__name__}'"
            )
            return violations

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


def _validate_parameter(
    param_name: str,
    value: Any,  # noqa: ANN401
    definition: ParameterDefinition,
) -> list[str]:
    """Validate a single parameter value against its definition.

    Args:
        param_name: The parameter name.
        value: The extracted value.
        definition: The parameter definition with validation rules.

    Returns:
        List of validation error messages (empty if valid).
    """
    violations: list[str] = []

    if definition.required and value is None:
        if not definition.ask_if_missing:
            violations.append(f"Parameter '{param_name}': required value is missing")
        return violations

    if value is None:
        return violations

    if not definition.validation:
        return violations

    validation = definition.validation
    val_type = validation.type.lower()

    if val_type == "integer":
        violations.extend(_validate_integer(value, validation, param_name))
    elif val_type == "string":
        violations.extend(_validate_string(value, validation, param_name))
    elif val_type == "date":
        violations.extend(_validate_date(value, validation, param_name))
    elif val_type in {"float", "decimal", "number"}:
        violations.extend(_validate_float(value, validation, param_name))
    else:
        logger.warning("Unknown validation type '%s' for parameter '%s'", val_type, param_name)

    return violations


def validate_all_parameters(
    extracted_parameters: dict[str, Any],
    parameter_definitions: list[ParameterDefinition],
) -> tuple[bool, list[str]]:
    """Validate all extracted parameters against their definitions.

    Args:
        extracted_parameters: Dict of parameter name to extracted value.
        parameter_definitions: List of parameter definitions with validation rules.

    Returns:
        Tuple of (is_valid, list of violation messages).
    """
    all_violations: list[str] = []
    def_lookup = {d.name: d for d in parameter_definitions}

    for param_name, value in extracted_parameters.items():
        definition = def_lookup.get(param_name)
        if not definition:
            logger.warning("Extracted parameter '%s' not found in definitions", param_name)
            continue

        violations = _validate_parameter(param_name, value, definition)
        all_violations.extend(violations)

    all_violations.extend(
        f"Parameter '{definition.name}': required but not provided"
        for definition in parameter_definitions
        if (
            definition.required
            and definition.name not in extracted_parameters
            and definition.default_value is None
            and not definition.ask_if_missing
        )
    )

    return len(all_violations) == 0, all_violations


def validate_parameters(draft: SQLDraft) -> SQLDraft:
    """Validate all parameters in an SQLDraft against their definitions.

    Runs type, range, regex, and allowed-value checks on each extracted
    parameter. For partial-cache parameters, allowed-value checks are
    skipped because the cached list may be incomplete.

    Args:
        draft: The SQL draft containing extracted parameters and definitions.

    Returns:
        A new SQLDraft with validation results applied. On success,
        ``params_validated`` is ``True`` and ``parameter_violations`` is
        empty. On failure, ``status`` is ``"error"`` and violations are
        listed in ``parameter_violations``.
    """
    extracted_params = draft.extracted_parameters or {}
    param_definitions = draft.parameter_definitions or []

    # For partial-cache params, temporarily clear allowed_values so the
    # validator skips strict matching (the cache was capped at max_values
    # and may not contain the user's value).
    partial_names = set(draft.partial_cache_params)
    saved_allowed: dict[str, list[str] | None] = {}
    for pdef in param_definitions:
        if pdef.name in partial_names and pdef.validation:
            saved_allowed[pdef.name] = pdef.validation.allowed_values
            pdef.validation.allowed_values = None

    if not param_definitions:
        logger.info("No parameter definitions provided, skipping validation")
        return draft.model_copy(update={"params_validated": True})

    is_valid, violations = validate_all_parameters(extracted_params, param_definitions)

    # Restore allowed_values that were cleared for partial-cache params
    for pdef in param_definitions:
        if pdef.name in saved_allowed and pdef.validation:
            pdef.validation.allowed_values = saved_allowed[pdef.name]

    if is_valid:
        logger.info("All parameters validated successfully")
        return draft.model_copy(
            update={"params_validated": True, "parameter_violations": []},
        )

    logger.warning("Parameter validation failed: %s", violations)
    return draft.model_copy(
        update={
            "status": "error",
            "parameter_violations": violations,
            "error": f"Parameter validation failed: {'; '.join(violations)}",
        },
    )
