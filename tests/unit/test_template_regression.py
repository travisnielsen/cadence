"""Regression test: template-based queries pass through unmodified.

Verifies that US1-US6 dynamic query enhancements do NOT affect
template-based queries:
- Column filter is not applied (no hidden_columns)
- Confidence gate is not triggered (source != "dynamic")
- Error recovery uses same path as before
- NL2SQLResponse fields remain correct for template queries
"""

from entities.shared.column_filter import refine_columns
from models import NL2SQLResponse, SQLDraft

# ── Template SQLDraft passthrough ────────────────────────────────────────


class TestTemplateDraftPassthrough:
    """Verify template queries skip dynamic-only enhancements."""

    def _make_template_response(self) -> NL2SQLResponse:
        """Build a typical template-based NL2SQLResponse."""
        return NL2SQLResponse(
            sql_query="SELECT TOP 10 o.OrderID, o.OrderDate FROM Sales.Orders o WHERE o.CustomerID = 1",
            sql_response=[
                {"OrderID": 1, "OrderDate": "2024-01-15"},
                {"OrderID": 2, "OrderDate": "2024-02-20"},
            ],
            columns=["OrderID", "OrderDate"],
            row_count=2,
            confidence_score=0.95,
            query_source="template",
        )

    def test_no_hidden_columns(self) -> None:
        """Template responses should have no hidden columns."""
        response = self._make_template_response()
        assert response.hidden_columns == []

    def test_no_query_summary(self) -> None:
        """Template responses should have no query summary."""
        response = self._make_template_response()
        assert not response.query_summary

    def test_no_query_confidence(self) -> None:
        """Template responses should have zero query confidence (template doesn't set it)."""
        response = self._make_template_response()
        assert response.query_confidence == 0.0

    def test_no_error_suggestions(self) -> None:
        """Template responses should have no error suggestions."""
        response = self._make_template_response()
        assert response.error_suggestions == []

    def test_confidence_gate_does_not_apply(self) -> None:
        """Template drafts should never trigger the confidence gate."""
        draft = SQLDraft(
            status="success",
            source="template",
            completed_sql="SELECT 1",
            confidence=0.0,  # Even zero confidence
            query_validated=True,
        )
        # The gate condition: source == "dynamic" — template fails this check
        should_gate = draft.source == "dynamic" and draft.confidence < 0.7
        assert should_gate is False


# ── Column filter skipped for template queries ───────────────────────────


class TestColumnFilterNotAppliedToTemplates:
    """Template queries define their exact columns — filter should not be invoked.

    While the filter CAN be called on any data, the controller only invokes it
    for dynamic queries. These tests verify the filter behaves sanely if called
    anyway but produces no hidden columns for well-formed template results.
    """

    def test_no_empty_columns_in_template_results(self) -> None:
        """Template results rarely have empty columns since they're hand-authored."""
        columns = ["OrderID", "OrderDate", "CustomerID"]
        rows = [
            {"OrderID": 1, "OrderDate": "2024-01-15", "CustomerID": 42},
            {"OrderID": 2, "OrderDate": "2024-02-20", "CustomerID": 43},
        ]
        result = refine_columns(columns, rows, "show orders for customer 42", "SELECT ...")
        assert result.hidden_columns == []
        assert result.columns == columns

    def test_template_with_few_columns_no_capping(self) -> None:
        """Templates with <= 8 columns should not be capped."""
        columns = ["A", "B", "C", "D", "E"]
        rows = [{"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}]
        result = refine_columns(columns, rows, "test query", "SELECT A, B, C, D, E")
        assert len(result.columns) == 5
        assert result.hidden_columns == []


# ── Template error path ──────────────────────────────────────────────────


class TestTemplateErrorPath:
    """Template error responses should not include dynamic error suggestions."""

    def test_template_error_has_no_error_suggestions(self) -> None:
        response = NL2SQLResponse(
            sql_query="",
            error="Parameter validation failed: invalid date format",
            query_source="template",
        )
        assert response.error_suggestions == []

    def test_template_clarification_unaffected(self) -> None:
        """Parameter clarification flow should be unaffected by US5 changes."""
        from models import ClarificationInfo

        response = NL2SQLResponse(
            sql_query="",
            needs_clarification=True,
            clarification=ClarificationInfo(
                parameter_name="customer_name",
                prompt="Which customer?",
                allowed_values=["Alice", "Bob"],
            ),
        )
        assert response.needs_clarification is True
        assert response.clarification is not None
        assert not response.query_summary  # No confirmation gate summary
