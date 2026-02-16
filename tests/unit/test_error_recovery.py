"""Unit tests for error recovery classification and suggestion generation.

Tests classify_violations, build_error_recovery, and detect_area_from_tables
for:
- Disallowed-table classification
- Syntax error classification
- Generic failure fallback
- No-table-matches generic guidance
- Suggestion count (2-3 per recovery)
- Schema area detection from table names
"""

from entities.shared.error_recovery import (
    build_error_recovery,
    classify_violations,
    detect_area_from_tables,
)
from models import SchemaSuggestion

# ── classify_violations ─────────────────────────────────────────────────


class TestClassifyViolations:
    """Test violation classification into categories."""

    def test_disallowed_table(self) -> None:
        violations = ["Disallowed table: HR.Employees is not in the allowed list"]
        assert classify_violations(violations) == "disallowed_tables"

    def test_disallowed_table_variant(self) -> None:
        violations = ["Table not allowed: dbo.Users"]
        assert classify_violations(violations) == "disallowed_tables"

    def test_syntax_error(self) -> None:
        violations = ["Syntax error near keyword 'FROM'"]
        assert classify_violations(violations) == "syntax"

    def test_parse_error(self) -> None:
        violations = ["Parse error: unexpected token ')'"]
        assert classify_violations(violations) == "syntax"

    def test_invalid_sql(self) -> None:
        violations = ["Invalid SQL: missing SELECT clause"]
        assert classify_violations(violations) == "syntax"

    def test_generic_violation(self) -> None:
        violations = ["Query references non-existent column 'FooBar'"]
        assert classify_violations(violations) == "generic"

    def test_multiple_violations_disallowed_wins(self) -> None:
        """If any violation contains disallowed table pattern, that category wins."""
        violations = [
            "Some generic issue",
            "Disallowed table: HR.Employees is not in the allowed list",
        ]
        assert classify_violations(violations) == "disallowed_tables"

    def test_empty_violations(self) -> None:
        assert classify_violations([]) == "generic"


# ── detect_area_from_tables ─────────────────────────────────────────────


class TestDetectAreaFromTables:
    """Test schema area detection from table names."""

    def test_sales_area(self) -> None:
        assert detect_area_from_tables(["Sales.Orders"]) == "sales"

    def test_purchasing_area(self) -> None:
        assert detect_area_from_tables(["Purchasing.PurchaseOrders"]) == "purchasing"

    def test_warehouse_area(self) -> None:
        assert detect_area_from_tables(["Warehouse.StockItems"]) == "warehouse"

    def test_application_area(self) -> None:
        assert detect_area_from_tables(["Application.People"]) == "application"

    def test_no_dot_returns_none(self) -> None:
        assert detect_area_from_tables(["Orders"]) is None

    def test_unknown_schema_returns_none(self) -> None:
        assert detect_area_from_tables(["HR.Employees"]) is None

    def test_empty_list_returns_none(self) -> None:
        assert detect_area_from_tables([]) is None

    def test_uses_first_table(self) -> None:
        """Area is detected from the first table in the list."""
        assert detect_area_from_tables(["Sales.Orders", "Warehouse.StockItems"]) == "sales"


# ── build_error_recovery ────────────────────────────────────────────────


class TestBuildErrorRecovery:
    """Test the full error recovery output."""

    def test_disallowed_table_message(self) -> None:
        msg, _ = build_error_recovery(["Disallowed table: HR.Employees"], ["HR.Employees"])
        assert "isn't available" in msg

    def test_syntax_error_message(self) -> None:
        msg, _ = build_error_recovery(["Syntax error near 'FROM'"], ["Sales.Orders"])
        assert "rephrase" in msg.lower()

    def test_generic_error_includes_violations(self) -> None:
        msg, _ = build_error_recovery(["Column 'FooBar' not found"], ["Sales.Orders"])
        assert "FooBar" in msg

    def test_suggestions_from_matched_area(self) -> None:
        _, suggestions = build_error_recovery(["Syntax error"], ["Sales.Orders"])
        assert len(suggestions) >= 2
        assert len(suggestions) <= 3
        assert all(isinstance(s, SchemaSuggestion) for s in suggestions)

    def test_generic_suggestions_for_unknown_area(self) -> None:
        _, suggestions = build_error_recovery(["Some error"], ["HR.Employees"])
        assert len(suggestions) >= 2
        assert len(suggestions) <= 3

    def test_generic_suggestions_for_no_tables(self) -> None:
        _, suggestions = build_error_recovery(["Some error"], [])
        assert len(suggestions) >= 2
        assert len(suggestions) <= 3

    def test_suggestions_are_schema_suggestions(self) -> None:
        _, suggestions = build_error_recovery(["Disallowed table: X"], ["Warehouse.StockItems"])
        for s in suggestions:
            assert hasattr(s, "title")
            assert hasattr(s, "prompt")
            assert isinstance(s.title, str)
            assert isinstance(s.prompt, str)

    def test_each_area_has_suggestions(self) -> None:
        """All four schema areas should produce non-empty suggestions."""
        for table in [
            "Sales.Orders",
            "Purchasing.PurchaseOrders",
            "Warehouse.StockItems",
            "Application.People",
        ]:
            _, suggestions = build_error_recovery(["error"], [table])
            assert len(suggestions) > 0, f"No suggestions for {table}"
