"""Unit tests for clarification formatting functions.

Tests pure helper functions from the NL2SQL pipeline:
_format_hypothesis_prompt, _format_confirmation_note, and
threshold constants.
"""

from entities.nl2sql_controller.pipeline import (
    _CONFIDENCE_THRESHOLD_HIGH,
    _CONFIDENCE_THRESHOLD_LOW,
    _format_confirmation_note,
    _format_hypothesis_prompt,
)
from models import MissingParameter

# ── _format_hypothesis_prompt ────────────────────────────────────────────


class TestFormatHypothesisPrompt:
    """Tests for _format_hypothesis_prompt clarification messages."""

    def test_with_best_guess(self) -> None:
        mp = MissingParameter(
            name="category",
            best_guess="Supermarket",
            alternatives=["Novelty Shop", "Computer Store"],
        )
        result = _format_hypothesis_prompt([mp])
        assert "It looks like you want **Supermarket**" in result
        assert "Novelty Shop" in result
        assert "Computer Store" in result

    def test_without_best_guess(self) -> None:
        mp = MissingParameter(
            name="category",
            best_guess=None,
            alternatives=["A", "B"],
        )
        result = _format_hypothesis_prompt([mp])
        assert "What value would you like for" in result
        assert "Options:" in result

    def test_without_anything(self) -> None:
        mp = MissingParameter(
            name="category",
            best_guess=None,
            alternatives=None,
        )
        result = _format_hypothesis_prompt([mp])
        assert "What value would you like for" in result

    def test_best_guess_no_alternatives(self) -> None:
        mp = MissingParameter(
            name="category",
            best_guess="Supermarket",
            alternatives=None,
        )
        result = _format_hypothesis_prompt([mp])
        assert "It looks like you want **Supermarket**" in result
        assert result.rstrip().endswith("?")

    def test_single_question_enforcement(self) -> None:
        """Verify format works for a single-item list (enforcement happens upstream)."""
        mp = MissingParameter(
            name="status",
            best_guess="Active",
            alternatives=["Inactive"],
        )
        result = _format_hypothesis_prompt([mp])
        assert "It looks like you want **Active**" in result


# ── _format_confirmation_note ────────────────────────────────────────────


class TestFormatConfirmationNote:
    """Tests for _format_confirmation_note medium-confidence notes."""

    def test_medium_confidence(self) -> None:
        result = _format_confirmation_note(
            parameter_confidences={"category": 0.7},
            extracted_parameters={"category": "Supermarket"},
        )
        assert "I assumed" in result
        assert "category=**Supermarket**" in result

    def test_high_confidence(self) -> None:
        result = _format_confirmation_note(
            parameter_confidences={"category": 0.9},
            extracted_parameters={"category": "Supermarket"},
        )
        assert not result

    def test_low_confidence(self) -> None:
        result = _format_confirmation_note(
            parameter_confidences={"category": 0.3},
            extracted_parameters={"category": "Supermarket"},
        )
        assert not result

    def test_no_params(self) -> None:
        result = _format_confirmation_note(
            parameter_confidences={},
            extracted_parameters=None,
        )
        assert not result


# ── Threshold constants ──────────────────────────────────────────────────


class TestThresholdConstants:
    """Verify exported threshold constants."""

    def test_threshold_values(self) -> None:
        assert _CONFIDENCE_THRESHOLD_HIGH == 0.85
        assert _CONFIDENCE_THRESHOLD_LOW == 0.6
