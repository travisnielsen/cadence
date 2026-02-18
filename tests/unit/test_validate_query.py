"""Unit tests for the pure validate_query() function.

Tests cover syntax checks, statement type checks, table allowlist
compliance, security pattern detection, and edge cases.
"""

from __future__ import annotations

import pytest
from entities.query_validator.validator import validate_query
from models import SQLDraft

# Consistent allowed-tables set used across tests
ALLOWED_TABLES: set[str] = {
    "Sales.Orders",
    "Sales.Customers",
    "Purchasing.Suppliers",
    "Application.People",
}


def _make_draft(
    sql: str | None = None,
    *,
    source: str = "template",
) -> SQLDraft:
    """Build a minimal SQLDraft for testing.

    Args:
        sql: The completed SQL string (or None).
        source: Draft source, defaults to "template".

    Returns:
        A fresh SQLDraft with the given SQL.
    """
    return SQLDraft(
        status="success",
        source=source,
        completed_sql=sql,
        user_query="test question",
    )


# ── Valid queries ─────────────────────────────────────────────────────


class TestValidQueries:
    """Queries that should pass all validation checks."""

    def test_simple_select(self) -> None:
        """Simple SELECT with a single allowed table passes."""
        draft = _make_draft("SELECT TOP 10 CustomerName FROM Sales.Customers")
        result = validate_query(draft, ALLOWED_TABLES)

        assert result.query_validated is True
        assert result.query_violations == []

    def test_select_with_join(self) -> None:
        """SELECT with JOIN on two allowed tables passes."""
        sql = (
            "SELECT o.OrderID, c.CustomerName "
            "FROM Sales.Orders o "
            "JOIN Sales.Customers c ON o.CustomerID = c.CustomerID"
        )
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert result.query_violations == []

    def test_select_with_where(self) -> None:
        """SELECT with a WHERE clause passes."""
        sql = "SELECT SupplierName FROM Purchasing.Suppliers WHERE SupplierID = 1"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert result.query_violations == []

    def test_select_with_group_by_order_by(self) -> None:
        """SELECT with GROUP BY and ORDER BY passes."""
        sql = (
            "SELECT CustomerName, COUNT(*) AS cnt "
            "FROM Sales.Customers c "
            "GROUP BY CustomerName "
            "ORDER BY cnt DESC"
        )
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert result.query_violations == []

    def test_select_with_subquery(self) -> None:
        """SELECT containing a subquery passes."""
        sql = (
            "SELECT CustomerName "
            "FROM Sales.Customers "
            "WHERE CustomerID IN ("
            "  SELECT CustomerID FROM Sales.Orders"
            ")"
        )
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert result.query_violations == []


# ── Syntax checks ────────────────────────────────────────────────────


class TestSyntaxChecks:
    """Queries that fail basic syntax validation."""

    def test_empty_query(self) -> None:
        """Empty string triggers 'Query is empty' violation."""
        result = validate_query(_make_draft(""), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("empty" in v.lower() for v in result.query_violations)

    def test_unbalanced_parentheses(self) -> None:
        """Mismatched parentheses trigger a violation."""
        sql = "SELECT * FROM Sales.Customers WHERE (CustomerID = 1"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("parenthes" in v.lower() for v in result.query_violations)

    def test_unbalanced_single_quotes(self) -> None:
        """Odd number of single quotes triggers a violation."""
        sql = "SELECT * FROM Sales.Customers WHERE Name = 'oops"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("quote" in v.lower() for v in result.query_violations)

    def test_non_select_start(self) -> None:
        """Query not starting with SELECT triggers a violation."""
        sql = "WITH cte AS (SELECT 1) SELECT * FROM cte"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("SELECT" in v for v in result.query_violations)


# ── Statement type checks ────────────────────────────────────────────


class TestStatementTypeChecks:
    """Non-SELECT statements must be rejected."""

    @pytest.mark.parametrize(
        ("keyword", "sql"),
        [
            (
                "INSERT",
                "INSERT INTO Sales.Customers (Name) VALUES ('x')",
            ),
            (
                "UPDATE",
                "UPDATE Sales.Customers SET Name = 'x' WHERE 1=1",
            ),
            (
                "DELETE",
                "DELETE FROM Sales.Customers WHERE 1=1",
            ),
            (
                "DROP",
                "DROP TABLE Sales.Customers",
            ),
        ],
        ids=["insert", "update", "delete", "drop"],
    )
    def test_non_select_statement(self, keyword: str, sql: str) -> None:
        """Non-SELECT statement types are rejected."""
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any(keyword in v for v in result.query_violations), (
            f"Expected violation mentioning {keyword}"
        )

    def test_multiple_statements_semicolon(self) -> None:
        """Semicolon between statements triggers a violation."""
        sql = "SELECT 1; SELECT 2"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any(
            "semicolon" in v.lower() or "multiple" in v.lower() for v in result.query_violations
        )

    def test_trailing_semicolon_ok(self) -> None:
        """A single trailing semicolon is tolerated."""
        sql = "SELECT CustomerName FROM Sales.Customers;"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert not any(
            "semicolon" in v.lower() or "multiple" in v.lower() for v in result.query_violations
        )


# ── Allowlist checks ─────────────────────────────────────────────────


class TestAllowlistChecks:
    """Table allowlist enforcement."""

    def test_table_in_allowlist(self) -> None:
        """Fully qualified table in the allowlist passes."""
        sql = "SELECT * FROM Sales.Customers"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert not any("allowlist" in v.lower() for v in result.query_violations)

    def test_table_not_in_allowlist(self) -> None:
        """Table absent from the allowlist triggers a violation."""
        sql = "SELECT * FROM HR.Employees"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("allowlist" in v.lower() for v in result.query_violations)

    def test_unqualified_table_warning(self) -> None:
        """Unqualified table name produces a warning, not a violation."""
        sql = "SELECT * FROM Customers"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("fully qualified" in w.lower() for w in result.query_warnings)
        # Should NOT be a hard violation
        assert not any("allowlist" in v.lower() for v in result.query_violations)

    def test_alias_not_confused_with_table(self) -> None:
        """Column references via alias (e.g. c.Name) are not flagged."""
        sql = "SELECT c.CustomerName FROM Sales.Customers c WHERE c.CustomerID = 1"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert result.query_violations == []


# ── Security checks ──────────────────────────────────────────────────


class TestSecurityChecks:
    """SQL injection pattern and dangerous keyword detection."""

    def test_union_select_injection(self) -> None:
        """UNION SELECT pattern is detected."""
        sql = "SELECT CustomerName FROM Sales.Customers UNION SELECT password FROM users"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert len(result.query_violations) > 0

    def test_xp_cmdshell(self) -> None:
        """xp_cmdshell pattern is caught."""
        sql = "SELECT 1; xp_cmdshell 'dir'"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any(
            "injection" in v.lower() or "xp_cmdshell" in v.lower() for v in result.query_violations
        )

    def test_waitfor_delay(self) -> None:
        """WAITFOR DELAY time-based injection is caught."""
        sql = "SELECT 1 WAITFOR DELAY '0:0:5'"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("injection" in v.lower() for v in result.query_violations)

    def test_delete_keyword_in_query(self) -> None:
        """DELETE keyword inside a SELECT is flagged as dangerous."""
        sql = "SELECT DELETE FROM Sales.Customers"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("DELETE" in v for v in result.query_violations)

    def test_information_schema(self) -> None:
        """INFORMATION_SCHEMA access is blocked."""
        sql = "SELECT * FROM INFORMATION_SCHEMA.TABLES"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("injection" in v.lower() for v in result.query_violations)

    def test_comment_injection(self) -> None:
        """Semicolon-dash-dash comment injection is caught."""
        sql = "SELECT * FROM Sales.Customers;-- drop table"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any(
            "injection" in v.lower() or "semicolon" in v.lower() for v in result.query_violations
        )

    def test_or_injection(self) -> None:
        """Classic OR '1'='1' injection is caught."""
        sql = "SELECT * FROM Sales.Customers WHERE Name = '' OR '1'='1'"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("injection" in v.lower() for v in result.query_violations)


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary conditions and special scenarios."""

    def test_none_completed_sql(self) -> None:
        """None completed_sql defaults to empty string → 'empty' violation."""
        result = validate_query(_make_draft(None), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("empty" in v.lower() for v in result.query_violations)

    def test_allowed_tables_parameter_changes_behavior(self) -> None:
        """Different allowed_tables sets yield different outcomes."""
        sql = "SELECT * FROM Sales.Customers"

        with_customer = validate_query(_make_draft(sql), {"Sales.Customers"})
        without_customer = validate_query(_make_draft(sql), {"Purchasing.Suppliers"})

        assert with_customer.query_violations == []
        assert any("allowlist" in v.lower() for v in without_customer.query_violations)

    def test_multiple_violations_accumulated(self) -> None:
        """Multiple distinct problems all appear in violations."""
        # INSERT (statement type) + disallowed table + unbalanced parens
        sql = "INSERT INTO HR.Secret (col VALUES ('x'"
        result = validate_query(_make_draft(sql), ALLOWED_TABLES)

        assert result.query_validated is True
        assert len(result.query_violations) >= 2

    def test_original_draft_fields_preserved(self) -> None:
        """Non-validation fields on the draft survive the copy."""
        draft = SQLDraft(
            status="success",
            source="dynamic",
            completed_sql="SELECT 1 FROM Sales.Customers",
            user_query="show me something",
            reasoning="test reasoning",
        )
        result = validate_query(draft, ALLOWED_TABLES)

        assert result.source == "dynamic"
        assert result.user_query == "show me something"
        assert result.reasoning == "test reasoning"

    def test_whitespace_only_query(self) -> None:
        """Whitespace-only SQL is treated as empty."""
        result = validate_query(_make_draft("   "), ALLOWED_TABLES)

        assert result.query_validated is True
        assert any("empty" in v.lower() for v in result.query_violations)
