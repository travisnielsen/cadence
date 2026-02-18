"""Unit tests for parameter validation logic.

Tests the pure ``validate_parameters()`` and ``validate_all_parameters()``
functions from the parameter validator module.
"""

from entities.parameter_validator.validator import (
    validate_all_parameters,
    validate_parameters,
)
from models import ParameterDefinition, ParameterValidation, SQLDraft

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_draft(
    extracted_parameters: dict | None = None,
    parameter_definitions: list[ParameterDefinition] | None = None,
    partial_cache_params: list[str] | None = None,
    status: str = "success",
) -> SQLDraft:
    """Build a minimal SQLDraft for testing."""
    return SQLDraft(
        status=status,
        source="template",
        completed_sql="SELECT 1",
        user_query="test query",
        extracted_parameters=extracted_parameters,
        parameter_definitions=parameter_definitions or [],
        partial_cache_params=partial_cache_params or [],
    )


def _make_param_def(
    name: str,
    *,
    required: bool = True,
    ask_if_missing: bool = False,
    default_value: object = None,
    validation: ParameterValidation | None = None,
) -> ParameterDefinition:
    """Build a ParameterDefinition with sensible defaults."""
    return ParameterDefinition(
        name=name,
        required=required,
        ask_if_missing=ask_if_missing,
        default_value=default_value,
        validation=validation,
    )


# ===================================================================
# validate_parameters() — integration-level tests
# ===================================================================


class TestValidateParametersNoDefinitions:
    """When there are no parameter definitions at all."""

    def test_returns_params_validated_true(self) -> None:
        """No definitions → skip validation, mark as validated."""
        draft = _make_draft(extracted_parameters={"x": 1})
        result = validate_parameters(draft)
        assert result.params_validated is True
        assert result.parameter_violations == []

    def test_no_definitions_no_params(self) -> None:
        """Empty definitions and no extracted params → validated."""
        draft = _make_draft()
        result = validate_parameters(draft)
        assert result.params_validated is True


class TestValidateParametersInteger:
    """Integer type validation via validate_parameters()."""

    def test_all_valid_integer_params(self) -> None:
        """Valid integer within range passes."""
        draft = _make_draft(
            extracted_parameters={"top_n": 10},
            parameter_definitions=[
                _make_param_def(
                    "top_n",
                    validation=ParameterValidation(type="integer", min=1, max=100),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True
        assert result.parameter_violations == []

    def test_integer_out_of_range_below_min(self) -> None:
        """Integer below minimum triggers violation."""
        draft = _make_draft(
            extracted_parameters={"top_n": 0},
            parameter_definitions=[
                _make_param_def(
                    "top_n",
                    validation=ParameterValidation(type="integer", min=1, max=100),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert len(result.parameter_violations) >= 1
        assert "below minimum" in result.parameter_violations[0]

    def test_integer_out_of_range_above_max(self) -> None:
        """Integer above maximum triggers violation."""
        draft = _make_draft(
            extracted_parameters={"top_n": 200},
            parameter_definitions=[
                _make_param_def(
                    "top_n",
                    validation=ParameterValidation(type="integer", min=1, max=100),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("exceeds maximum" in v for v in result.parameter_violations)

    def test_integer_from_string_coercion(self) -> None:
        """String-encoded integer is coerced and validated."""
        draft = _make_draft(
            extracted_parameters={"top_n": "5"},
            parameter_definitions=[
                _make_param_def(
                    "top_n",
                    validation=ParameterValidation(type="integer", min=1, max=100),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True

    def test_integer_invalid_type(self) -> None:
        """Non-numeric string fails integer validation."""
        draft = _make_draft(
            extracted_parameters={"top_n": "abc"},
            parameter_definitions=[
                _make_param_def(
                    "top_n",
                    validation=ParameterValidation(type="integer"),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("expected integer" in v for v in result.parameter_violations)


class TestValidateParametersString:
    """String type validation — allowed values and regex."""

    def test_valid_string_in_allowed_values(self) -> None:
        """String matching an allowed value (case-insensitive) passes."""
        draft = _make_draft(
            extracted_parameters={"status": "active"},
            parameter_definitions=[
                _make_param_def(
                    "status",
                    validation=ParameterValidation(
                        type="string",
                        allowed_values=["Active", "Inactive"],
                    ),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True
        assert result.parameter_violations == []

    def test_string_not_in_allowed_values(self) -> None:
        """String not in allowed values triggers violation."""
        draft = _make_draft(
            extracted_parameters={"status": "deleted"},
            parameter_definitions=[
                _make_param_def(
                    "status",
                    validation=ParameterValidation(
                        type="string",
                        allowed_values=["Active", "Inactive"],
                    ),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("not in allowed values" in v for v in result.parameter_violations)

    def test_regex_validation_success(self) -> None:
        """String matching regex pattern passes."""
        draft = _make_draft(
            extracted_parameters={"code": "ABC-123"},
            parameter_definitions=[
                _make_param_def(
                    "code",
                    validation=ParameterValidation(
                        type="string",
                        regex=r"^[A-Z]{3}-\d{3}$",
                    ),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True

    def test_regex_validation_failure(self) -> None:
        """String not matching regex triggers violation."""
        draft = _make_draft(
            extracted_parameters={"code": "bad"},
            parameter_definitions=[
                _make_param_def(
                    "code",
                    validation=ParameterValidation(
                        type="string",
                        regex=r"^[A-Z]{3}-\d{3}$",
                    ),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("does not match pattern" in v for v in result.parameter_violations)


class TestValidateParametersDate:
    """Date type validation — format and range."""

    def test_valid_date_format(self) -> None:
        """Well-formed date string passes validation."""
        draft = _make_draft(
            extracted_parameters={"from_date": "2024-01-15"},
            parameter_definitions=[
                _make_param_def(
                    "from_date",
                    validation=ParameterValidation(type="date"),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True

    def test_invalid_date_format(self) -> None:
        """Unparseable date string triggers violation."""
        draft = _make_draft(
            extracted_parameters={"from_date": "not-a-date"},
            parameter_definitions=[
                _make_param_def(
                    "from_date",
                    validation=ParameterValidation(type="date"),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("could not parse date" in v for v in result.parameter_violations)

    def test_date_out_of_range(self) -> None:
        """Date before the minimum triggers violation."""
        draft = _make_draft(
            extracted_parameters={"from_date": "2020-01-01"},
            parameter_definitions=[
                _make_param_def(
                    "from_date",
                    validation=ParameterValidation(
                        type="date",
                        min="2023-01-01",
                        max="2025-12-31",
                    ),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("before minimum" in v for v in result.parameter_violations)

    def test_date_after_max(self) -> None:
        """Date after the maximum triggers violation."""
        draft = _make_draft(
            extracted_parameters={"from_date": "2026-06-01"},
            parameter_definitions=[
                _make_param_def(
                    "from_date",
                    validation=ParameterValidation(
                        type="date",
                        min="2023-01-01",
                        max="2025-12-31",
                    ),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("after maximum" in v for v in result.parameter_violations)

    def test_sql_date_function_skipped(self) -> None:
        """SQL date functions like GETDATE() are passed through."""
        draft = _make_draft(
            extracted_parameters={"from_date": "GETDATE()"},
            parameter_definitions=[
                _make_param_def(
                    "from_date",
                    validation=ParameterValidation(
                        type="date",
                        min="2023-01-01",
                    ),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True

    def test_sql_dateadd_function_skipped(self) -> None:
        """DATEADD expressions skip date validation."""
        draft = _make_draft(
            extracted_parameters={"from_date": "DATEADD(YEAR, -1, GETDATE())"},
            parameter_definitions=[
                _make_param_def(
                    "from_date",
                    validation=ParameterValidation(type="date"),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True


class TestValidateParametersFloat:
    """Float/decimal type validation."""

    def test_valid_float_in_range(self) -> None:
        """Float within min/max passes."""
        draft = _make_draft(
            extracted_parameters={"price": 49.99},
            parameter_definitions=[
                _make_param_def(
                    "price",
                    validation=ParameterValidation(type="float", min=0.0, max=1000.0),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True

    def test_float_below_minimum(self) -> None:
        """Float below minimum triggers violation."""
        draft = _make_draft(
            extracted_parameters={"price": -5.0},
            parameter_definitions=[
                _make_param_def(
                    "price",
                    validation=ParameterValidation(type="float", min=0.0, max=1000.0),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("below minimum" in v for v in result.parameter_violations)

    def test_float_above_maximum(self) -> None:
        """Float above maximum triggers violation."""
        draft = _make_draft(
            extracted_parameters={"price": 2000.0},
            parameter_definitions=[
                _make_param_def(
                    "price",
                    validation=ParameterValidation(type="float", min=0.0, max=1000.0),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("exceeds maximum" in v for v in result.parameter_violations)

    def test_float_from_string_coercion(self) -> None:
        """String-encoded float is coerced and validated."""
        draft = _make_draft(
            extracted_parameters={"price": "42.5"},
            parameter_definitions=[
                _make_param_def(
                    "price",
                    validation=ParameterValidation(type="float", min=0.0, max=100.0),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True

    def test_float_invalid_type(self) -> None:
        """Non-numeric string fails float validation."""
        draft = _make_draft(
            extracted_parameters={"price": "expensive"},
            parameter_definitions=[
                _make_param_def(
                    "price",
                    validation=ParameterValidation(type="float"),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("expected number" in v for v in result.parameter_violations)

    def test_decimal_type_alias(self) -> None:
        """'decimal' type alias works identically to 'float'."""
        draft = _make_draft(
            extracted_parameters={"amount": 50},
            parameter_definitions=[
                _make_param_def(
                    "amount",
                    validation=ParameterValidation(type="decimal", min=0, max=100),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True

    def test_number_type_alias(self) -> None:
        """'number' type alias works identically to 'float'."""
        draft = _make_draft(
            extracted_parameters={"count": 7},
            parameter_definitions=[
                _make_param_def(
                    "count",
                    validation=ParameterValidation(type="number", min=1, max=10),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True


class TestValidateParametersRequired:
    """Required / missing parameter handling."""

    def test_required_missing_not_ask_if_missing(self) -> None:
        """Required param missing (ask_if_missing=False) → violation."""
        draft = _make_draft(
            extracted_parameters={"top_n": 10},
            parameter_definitions=[
                _make_param_def("top_n", validation=ParameterValidation(type="integer")),
                _make_param_def("status", ask_if_missing=False),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("required but not provided" in v for v in result.parameter_violations)

    def test_required_missing_ask_if_missing(self) -> None:
        """Required param missing but ask_if_missing=True → NO violation."""
        draft = _make_draft(
            extracted_parameters={"top_n": 10},
            parameter_definitions=[
                _make_param_def("top_n", validation=ParameterValidation(type="integer")),
                _make_param_def("status", ask_if_missing=True),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True
        assert result.parameter_violations == []

    def test_required_param_extracted_as_none_not_ask(self) -> None:
        """Required param present but None (ask_if_missing=False) → violation."""
        draft = _make_draft(
            extracted_parameters={"status": None},
            parameter_definitions=[
                _make_param_def("status", ask_if_missing=False),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("required value is missing" in v for v in result.parameter_violations)

    def test_required_param_extracted_as_none_ask(self) -> None:
        """Required param present but None (ask_if_missing=True) → no violation."""
        draft = _make_draft(
            extracted_parameters={"status": None},
            parameter_definitions=[
                _make_param_def("status", ask_if_missing=True),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True

    def test_no_extracted_parameters_with_definitions(self) -> None:
        """No extracted params but definitions exist → required params flagged."""
        draft = _make_draft(
            extracted_parameters={},
            parameter_definitions=[
                _make_param_def("city"),
                _make_param_def("country"),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert len(result.parameter_violations) == 2

    def test_none_extracted_parameters_with_definitions(self) -> None:
        """extracted_parameters is None → required params flagged."""
        draft = _make_draft(
            extracted_parameters=None,
            parameter_definitions=[
                _make_param_def("city"),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False


class TestValidateParametersPartialCache:
    """Partial cache params skip allowed_values checks."""

    def test_partial_cache_skips_allowed_values(self) -> None:
        """Parameter in partial_cache_params bypasses allowed_values check."""
        draft = _make_draft(
            extracted_parameters={"city": "Timbuktu"},
            parameter_definitions=[
                _make_param_def(
                    "city",
                    validation=ParameterValidation(
                        type="string",
                        allowed_values=["New York", "London"],
                    ),
                ),
            ],
            partial_cache_params=["city"],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True

    def test_partial_cache_allowed_values_restored(self) -> None:
        """After validation, allowed_values are restored on definitions."""
        defs = [
            _make_param_def(
                "city",
                validation=ParameterValidation(
                    type="string",
                    allowed_values=["New York", "London"],
                ),
            ),
        ]
        draft = _make_draft(
            extracted_parameters={"city": "Timbuktu"},
            parameter_definitions=defs,
            partial_cache_params=["city"],
        )
        validate_parameters(draft)
        assert defs[0].validation is not None
        assert defs[0].validation.allowed_values == ["New York", "London"]

    def test_non_partial_cache_still_enforces_allowed_values(self) -> None:
        """Params NOT in partial_cache still get allowed_values checked."""
        draft = _make_draft(
            extracted_parameters={"city": "Timbuktu"},
            parameter_definitions=[
                _make_param_def(
                    "city",
                    validation=ParameterValidation(
                        type="string",
                        allowed_values=["New York", "London"],
                    ),
                ),
            ],
            partial_cache_params=[],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert any("not in allowed values" in v for v in result.parameter_violations)


class TestValidateParametersMultipleViolations:
    """Multiple violations are all reported."""

    def test_multiple_violations_all_reported(self) -> None:
        """Two invalid params produce two violations."""
        draft = _make_draft(
            extracted_parameters={"top_n": 0, "status": "deleted"},
            parameter_definitions=[
                _make_param_def(
                    "top_n",
                    validation=ParameterValidation(type="integer", min=1),
                ),
                _make_param_def(
                    "status",
                    validation=ParameterValidation(
                        type="string",
                        allowed_values=["Active", "Inactive"],
                    ),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is False
        assert len(result.parameter_violations) >= 2

    def test_error_field_populated_on_failure(self) -> None:
        """Failed validation populates the error field."""
        draft = _make_draft(
            extracted_parameters={"top_n": -1},
            parameter_definitions=[
                _make_param_def(
                    "top_n",
                    validation=ParameterValidation(type="integer", min=1),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.error is not None
        assert "Parameter validation failed" in result.error

    def test_status_set_to_error_on_failure(self) -> None:
        """Failed validation sets status to 'error'."""
        draft = _make_draft(
            extracted_parameters={"top_n": -1},
            parameter_definitions=[
                _make_param_def(
                    "top_n",
                    validation=ParameterValidation(type="integer", min=1),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.status == "error"


class TestValidateParametersUnknownType:
    """Unknown validation types are tolerated."""

    def test_unknown_type_no_violation(self) -> None:
        """Unknown validation type logs warning but doesn't violate."""
        draft = _make_draft(
            extracted_parameters={"x": "whatever"},
            parameter_definitions=[
                _make_param_def(
                    "x",
                    validation=ParameterValidation(type="boolean"),
                ),
            ],
        )
        result = validate_parameters(draft)
        assert result.params_validated is True
        assert result.parameter_violations == []


# ===================================================================
# validate_all_parameters() — direct tests
# ===================================================================


class TestValidateAllParametersExtraParam:
    """Extra extracted params not in definitions."""

    def test_extra_param_not_a_violation(self) -> None:
        """Extra parameter is warned about but not a violation."""
        defs = [
            _make_param_def(
                "top_n",
                validation=ParameterValidation(type="integer", min=1),
            ),
        ]
        is_valid, violations = validate_all_parameters({"top_n": 5, "unknown_param": "hello"}, defs)
        assert is_valid is True
        assert violations == []


class TestValidateAllParametersDefaultValue:
    """Params with default_value that are not extracted."""

    def test_default_value_no_violation(self) -> None:
        """Required param missing but has default_value → no violation."""
        defs = [
            _make_param_def("sort_order", default_value="ASC"),
        ]
        is_valid, violations = validate_all_parameters({}, defs)
        assert is_valid is True
        assert violations == []

    def test_required_no_default_violation(self) -> None:
        """Required param missing with no default → violation."""
        defs = [
            _make_param_def("sort_order"),
        ]
        is_valid, violations = validate_all_parameters({}, defs)
        assert is_valid is False
        assert len(violations) == 1
        assert "required but not provided" in violations[0]


class TestValidateAllParametersOptional:
    """Optional parameters that are not extracted."""

    def test_optional_missing_no_violation(self) -> None:
        """Non-required missing param → no violation."""
        defs = [
            _make_param_def("filter", required=False),
        ]
        is_valid, violations = validate_all_parameters({}, defs)
        assert is_valid is True
        assert violations == []

    def test_optional_none_value_no_violation(self) -> None:
        """Non-required param with None value → no violation."""
        defs = [
            _make_param_def(
                "filter",
                required=False,
                validation=ParameterValidation(type="string"),
            ),
        ]
        is_valid, _violations = validate_all_parameters({"filter": None}, defs)
        assert is_valid is True

    def test_no_validation_rules_passes(self) -> None:
        """Param with no validation rules passes unconditionally."""
        defs = [_make_param_def("x")]
        is_valid, violations = validate_all_parameters({"x": "anything"}, defs)
        assert is_valid is True
        assert violations == []
