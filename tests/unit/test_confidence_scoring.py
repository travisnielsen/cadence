"""Unit tests for confidence scoring functions.

Tests pure helper functions from the parameter extractor executor:
_compute_confidence, _has_validation_rules, _value_passes_validation,
and _build_parameter_confidences.
"""

import pytest
from entities.parameter_extractor.executor import (
    _build_parameter_confidences,
    _compute_confidence,
    _has_validation_rules,
    _value_passes_validation,
)
from models import ParameterDefinition, ParameterValidation, QueryTemplate

# ── _compute_confidence ──────────────────────────────────────────────────


class TestComputeConfidence:
    """Tests for _compute_confidence resolution method scoring."""

    def test_exact_match(self) -> None:
        assert _compute_confidence("exact_match", 1.0) == 1.0

    def test_fuzzy_match(self) -> None:
        assert _compute_confidence("fuzzy_match", 1.0) == 0.85

    def test_default_value(self) -> None:
        assert _compute_confidence("default_value", 1.0) == 0.7

    def test_llm_validated(self) -> None:
        assert _compute_confidence("llm_validated", 1.0) == 0.75

    def test_llm_unvalidated(self) -> None:
        assert _compute_confidence("llm_unvalidated", 1.0) == 0.65

    def test_llm_failed_validation(self) -> None:
        assert _compute_confidence("llm_failed_validation", 1.0) == 0.3

    def test_with_weight(self) -> None:
        """LLM-validated has no floor, so weight applies directly."""
        result = _compute_confidence("llm_validated", 0.7)
        assert result == pytest.approx(0.525)

    def test_minimum_weight(self) -> None:
        """Weight floors at 0.3, so llm_unvalidated (0.65) * 0.3 = 0.195."""
        result = _compute_confidence("llm_unvalidated", 0.1)
        assert result == pytest.approx(0.195)

    def test_exact_match_floor(self) -> None:
        """Exact match has a floor of 0.85 regardless of low weight."""
        assert _compute_confidence("exact_match", 0.4) == pytest.approx(0.85)
        assert _compute_confidence("exact_match", 0.1) == pytest.approx(0.85)

    def test_fuzzy_match_floor(self) -> None:
        """Fuzzy match has a floor of 0.6 regardless of low weight."""
        assert _compute_confidence("fuzzy_match", 0.4) == pytest.approx(0.6)

    def test_default_value_floor(self) -> None:
        """Default value has a floor of 0.6 regardless of low weight."""
        assert _compute_confidence("default_value", 0.4) == pytest.approx(0.6)
        assert _compute_confidence("default_value", 0.1) == pytest.approx(0.6)

    def test_default_policy_floor(self) -> None:
        """Default policy has a floor of 0.6 regardless of low weight."""
        assert _compute_confidence("default_policy", 0.4) == pytest.approx(0.6)

    def test_llm_methods_no_floor(self) -> None:
        """LLM-based methods have no floor — weight fully applies."""
        assert _compute_confidence("llm_unvalidated", 0.4) == pytest.approx(0.26)
        assert _compute_confidence("llm_failed_validation", 0.4) == pytest.approx(0.12)

    def test_unknown_method_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown resolution method"):
            _compute_confidence("invalid", 1.0)


# ── _has_validation_rules ────────────────────────────────────────────────


class TestHasValidationRules:
    """Tests for _has_validation_rules on ParameterDefinition."""

    def test_with_allowed_values(self) -> None:
        param = ParameterDefinition(
            name="test",
            validation=ParameterValidation(type="string", allowed_values=["A", "B"]),
        )
        assert _has_validation_rules(param) is True

    def test_with_min_max(self) -> None:
        param = ParameterDefinition(
            name="test",
            validation=ParameterValidation(type="integer", min=1, max=100),
        )
        assert _has_validation_rules(param) is True

    def test_none_validation(self) -> None:
        param = ParameterDefinition(name="test", validation=None)
        assert _has_validation_rules(param) is False

    def test_empty_validation(self) -> None:
        """Validation present but no actual rules set."""
        param = ParameterDefinition(
            name="test",
            validation=ParameterValidation(type="string"),
        )
        assert _has_validation_rules(param) is False


# ── _value_passes_validation ─────────────────────────────────────────────


class TestValuePassesValidation:
    """Tests for _value_passes_validation rule checking."""

    def test_allowed_values_pass(self) -> None:
        param = ParameterDefinition(
            name="test",
            validation=ParameterValidation(type="string", allowed_values=["ASC", "DESC"]),
        )
        assert _value_passes_validation("ASC", param) is True

    def test_allowed_values_fail(self) -> None:
        param = ParameterDefinition(
            name="test",
            validation=ParameterValidation(type="string", allowed_values=["ASC", "DESC"]),
        )
        assert _value_passes_validation("INVALID", param) is False

    def test_integer_range_pass(self) -> None:
        param = ParameterDefinition(
            name="test",
            validation=ParameterValidation(type="integer", min=1, max=10),
        )
        assert _value_passes_validation(5, param) is True

    def test_integer_range_fail(self) -> None:
        param = ParameterDefinition(
            name="test",
            validation=ParameterValidation(type="integer", min=1, max=10),
        )
        assert _value_passes_validation(15, param) is False

    def test_no_rules(self) -> None:
        param = ParameterDefinition(name="test", validation=None)
        assert _value_passes_validation("anything", param) is True


# ── _build_parameter_confidences ─────────────────────────────────────────


class TestBuildParameterConfidences:
    """Tests for _build_parameter_confidences template-weighted scoring."""

    def test_basic_build(self) -> None:
        template = QueryTemplate(
            intent="test",
            question="test",
            sql_template="SELECT 1",
            parameters=[
                ParameterDefinition(name="count", confidence_weight=1.0),
                ParameterDefinition(name="order", confidence_weight=1.0),
            ],
        )
        resolution_methods = {"count": "exact_match", "order": "fuzzy_match"}
        result = _build_parameter_confidences(resolution_methods, template)
        assert result == {"count": pytest.approx(1.0), "order": pytest.approx(0.85)}

    def test_min_confidence_routing_tiers(self) -> None:
        """Verify min(confidences) can route to the three tiers."""
        template = QueryTemplate(
            intent="test",
            question="test",
            sql_template="SELECT 1",
            parameters=[
                ParameterDefinition(name="a", confidence_weight=1.0),
                ParameterDefinition(name="b", confidence_weight=1.0),
            ],
        )

        # High tier: all >= 0.85
        high = _build_parameter_confidences({"a": "exact_match", "b": "fuzzy_match"}, template)
        assert min(high.values()) >= 0.85

        # Medium tier: one at 0.7
        medium = _build_parameter_confidences({"a": "exact_match", "b": "default_value"}, template)
        assert 0.6 <= min(medium.values()) < 0.85

        # Low tier: one at 0.3
        low = _build_parameter_confidences(
            {"a": "exact_match", "b": "llm_failed_validation"}, template
        )
        assert min(low.values()) < 0.6
