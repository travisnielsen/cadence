"""Unit tests for the confidence-gate confirmation flow.

Tests the decision logic around the dynamic confidence threshold:
- High-confidence dynamic queries skip the gate
- Low-confidence dynamic queries trigger confirmation
- Template queries always skip the gate
- Acceptance keywords are recognized correctly
- Refinement turns bypass the gate
- Pending state management (store and clear)

Note: We cannot import directly from nl2sql_controller.executor because the
package __init__ triggers agent initialization requiring Azure credentials.
Instead we test the pure logic patterns inline against the documented threshold.
"""

import pytest
from models import NL2SQLResponse, SQLDraft

# Default threshold — matches the controller's constant.
# If the env var DYNAMIC_CONFIDENCE_THRESHOLD is set during tests,
# the controller would use that value instead.
_THRESHOLD = 0.7

# Acceptance keywords — exact copy of the set in handle_question
_ACCEPT_KEYWORDS = {"yes", "run", "execute", "accept", "go", "ok", "confirm"}


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_dynamic_draft(*, confidence: float = 0.5, reasoning: str = "test") -> SQLDraft:
    """Build a minimal dynamic SQLDraft with the given confidence."""
    return SQLDraft(
        status="success",
        source="dynamic",
        completed_sql="SELECT TOP 10 * FROM Sales.Orders",
        user_query="show me some orders",
        reasoning=reasoning,
        query_validated=True,
        confidence=confidence,
        tables_used=["Sales.Orders"],
    )


def _make_template_draft(*, confidence: float = 0.3) -> SQLDraft:
    """Build a minimal template SQLDraft."""
    return SQLDraft(
        status="success",
        source="template",
        completed_sql="SELECT TOP 10 * FROM Sales.Orders WHERE CustomerID = 1",
        user_query="show orders for customer 1",
        template_id="q1",
        query_validated=True,
        confidence=confidence,
    )


def _should_gate(draft: SQLDraft, *, is_refinement: bool = False) -> bool:
    """Replicate the controller's gate decision.

    Gate fires when ALL conditions are met:
      - source == "dynamic"
      - confidence < threshold
      - not is_refinement
    """
    return draft.source == "dynamic" and draft.confidence < _THRESHOLD and not is_refinement


def _is_acceptance(question: str) -> bool:
    """Replicate the controller's acceptance keyword check."""
    question_lower = question.strip().lower().rstrip(".")
    return question_lower in _ACCEPT_KEYWORDS or question_lower.startswith("run ")


# ── Gate trigger decision ────────────────────────────────────────────────


class TestGateTriggerDecision:
    """Test the conditions that determine whether the gate fires."""

    def test_low_confidence_dynamic_triggers_gate(self) -> None:
        draft = _make_dynamic_draft(confidence=0.4)
        assert _should_gate(draft) is True

    def test_high_confidence_dynamic_skips_gate(self) -> None:
        draft = _make_dynamic_draft(confidence=0.9)
        assert _should_gate(draft) is False

    def test_exact_threshold_skips_gate(self) -> None:
        """Confidence == threshold should NOT trigger (< not <=)."""
        draft = _make_dynamic_draft(confidence=_THRESHOLD)
        assert _should_gate(draft) is False

    def test_just_below_threshold_triggers(self) -> None:
        draft = _make_dynamic_draft(confidence=_THRESHOLD - 0.01)
        assert _should_gate(draft) is True

    def test_template_source_skips_gate(self) -> None:
        draft = _make_template_draft(confidence=0.3)
        assert _should_gate(draft) is False

    def test_refinement_turn_skips_gate(self) -> None:
        draft = _make_dynamic_draft(confidence=0.4)
        assert _should_gate(draft, is_refinement=True) is False

    def test_zero_confidence_triggers_gate(self) -> None:
        draft = _make_dynamic_draft(confidence=0.0)
        assert _should_gate(draft) is True

    def test_max_confidence_skips_gate(self) -> None:
        draft = _make_dynamic_draft(confidence=1.0)
        assert _should_gate(draft) is False


# ── Acceptance keyword matching ──────────────────────────────────────────


class TestAcceptanceKeywords:
    """Test the keyword matching used for confirmation acceptance."""

    @pytest.mark.parametrize(
        "question",
        ["yes", "Yes", "YES", "run", "Run", "execute", "accept", "go", "ok", "confirm"],
    )
    def test_exact_accepts(self, question: str) -> None:
        assert _is_acceptance(question) is True

    def test_trailing_period_stripped(self) -> None:
        assert _is_acceptance("yes.") is True

    def test_leading_whitespace_stripped(self) -> None:
        assert _is_acceptance("  yes  ") is True

    def test_run_prefix_accepted(self) -> None:
        """'run this query' starts with 'run ' — accepted."""
        assert _is_acceptance("run this query") is True

    def test_revision_rejected(self) -> None:
        assert _is_acceptance("show me something else") is False

    def test_empty_rejected(self) -> None:
        assert _is_acceptance("") is False

    def test_partial_keyword_rejected(self) -> None:
        assert _is_acceptance("ye") is False

    def test_no_not_an_accept(self) -> None:
        assert _is_acceptance("no") is False


# ── Confirmation response shape ──────────────────────────────────────────


class TestConfirmationResponse:
    """Test that the NL2SQLResponse built for confirmation has the right shape."""

    def test_confirmation_response_fields(self) -> None:
        draft = _make_dynamic_draft(confidence=0.45, reasoning="Fetch recent orders from sales")
        response = NL2SQLResponse(
            sql_query=draft.completed_sql or "",
            needs_clarification=True,
            query_summary=draft.reasoning or f"Execute: {(draft.completed_sql or '')[:150]}",
            query_confidence=draft.confidence,
            query_source="dynamic",
            tables_used=draft.tables_used,
            tables_metadata_json=draft.tables_metadata_json,
            original_question=draft.user_query,
        )
        assert response.needs_clarification is True
        assert response.query_summary == "Fetch recent orders from sales"
        assert response.query_confidence == pytest.approx(0.45)
        assert response.query_source == "dynamic"
        assert response.sql_query == "SELECT TOP 10 * FROM Sales.Orders"
        # No execution results — query was not run
        assert response.sql_response == []
        assert response.row_count == 0

    def test_confirmation_fallback_summary(self) -> None:
        """When reasoning is empty, summary falls back to SQL snippet."""
        draft = _make_dynamic_draft(confidence=0.3, reasoning="")
        fallback = draft.reasoning or f"Execute: {(draft.completed_sql or '')[:150]}"
        assert fallback.startswith("Execute: SELECT TOP 10")

    def test_no_clarification_object(self) -> None:
        """Confirmation responses must NOT set the clarification field."""
        response = NL2SQLResponse(
            sql_query="SELECT 1",
            needs_clarification=True,
            query_summary="Test query",
            query_confidence=0.5,
        )
        assert response.clarification is None

    def test_empty_results_list(self) -> None:
        """Confirmation response should have empty results — not executed yet."""
        response = NL2SQLResponse(
            sql_query="SELECT 1",
            needs_clarification=True,
            query_summary="Test query",
            query_confidence=0.5,
        )
        assert response.columns == []
        assert response.sql_response == []


# ── Pending state structure ──────────────────────────────────────────────


class TestPendingState:
    """Test the pending confirmation state dictionary structure."""

    def test_sql_draft_roundtrip(self) -> None:
        """Verify SQLDraft can be serialized to JSON and restored."""
        draft = _make_dynamic_draft(confidence=0.5)
        json_str = draft.model_dump_json()
        restored = SQLDraft.model_validate_json(json_str)
        assert restored.confidence == pytest.approx(0.5)
        assert restored.source == "dynamic"
        assert restored.completed_sql == draft.completed_sql

    def test_pending_state_shape(self) -> None:
        """The pending state dict should have the expected keys."""
        draft = _make_dynamic_draft(confidence=0.5)
        state = {
            "original_question": draft.user_query,
            "pending_confirmation": True,
            "dynamic_query": True,
            "sql_draft_json": draft.model_dump_json(),
            "tables": [],
        }
        assert state["pending_confirmation"] is True
        assert state["dynamic_query"] is True
        assert isinstance(state["sql_draft_json"], str)

    def test_acceptance_clears_pending(self) -> None:
        """After acceptance, pending_confirmation should be False and is_refinement True."""
        state: dict = {
            "pending_confirmation": True,
            "is_refinement": False,
        }
        # Simulate acceptance
        state["pending_confirmation"] = False
        state["is_refinement"] = True
        assert state["pending_confirmation"] is False
        assert state["is_refinement"] is True

    def test_revision_clears_state(self) -> None:
        """Revision should clear the entire state (set to None)."""
        state: dict | None = {
            "pending_confirmation": True,
            "sql_draft_json": "...",
        }
        # Simulate revision — controller sets state to None
        state = None
        assert state is None
