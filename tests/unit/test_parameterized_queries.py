"""Unit tests for parameterized query substitution.

Tests the substitute_parameters function and ParameterizedQuery dataclass
from the NL2SQL controller substitution module.
"""

import pytest
from entities.shared.substitution import (
    ParameterizedQuery,
    substitute_parameters,
)


class TestParameterizedQueryDataclass:
    """Verify ParameterizedQuery is frozen and has correct defaults."""

    def test_frozen(self) -> None:
        pq = ParameterizedQuery(display_sql="SELECT 1", exec_sql="SELECT 1")
        with pytest.raises(AttributeError):
            pq.display_sql = "SELECT 2"  # type: ignore[misc]

    def test_default_params(self) -> None:
        pq = ParameterizedQuery(display_sql="SELECT 1", exec_sql="SELECT 1")
        assert pq.exec_params == []


class TestSubstituteParametersInline:
    """Parameters that must be inlined (not parameterized)."""

    def test_sql_keyword_asc(self) -> None:
        template = "SELECT * FROM T ORDER BY col %{{order}}%"
        pq = substitute_parameters(template, {"order": "ASC"})
        assert "ASC" in pq.display_sql
        assert "ASC" in pq.exec_sql
        assert pq.exec_params == []

    def test_sql_keyword_desc_case_insensitive(self) -> None:
        template = "SELECT * FROM T ORDER BY col %{{order}}%"
        pq = substitute_parameters(template, {"order": "desc"})
        assert "DESC" in pq.display_sql
        assert "DESC" in pq.exec_sql
        assert pq.exec_params == []

    def test_null_value(self) -> None:
        template = "SELECT * FROM T WHERE col = %{{val}}%"
        pq = substitute_parameters(template, {"val": None})
        assert "NULL" in pq.display_sql
        assert "NULL" in pq.exec_sql
        assert pq.exec_params == []

    def test_sql_expression_dateadd(self) -> None:
        template = "WHERE OrderDate >= %{{from_date}}%"
        pq = substitute_parameters(template, {"from_date": "DATEADD(YEAR, -12, GETDATE())"})
        assert "DATEADD(YEAR, -12, GETDATE())" in pq.display_sql
        assert "DATEADD(YEAR, -12, GETDATE())" in pq.exec_sql
        assert pq.exec_params == []

    def test_sql_expression_convert(self) -> None:
        template = "WHERE col = %{{expr}}%"
        pq = substitute_parameters(template, {"expr": "CONVERT(DATE, GETDATE())"})
        assert "CONVERT(DATE, GETDATE())" in pq.exec_sql
        assert pq.exec_params == []


class TestSubstituteParametersBindValues:
    """Parameters that should use ? placeholders."""

    def test_integer(self) -> None:
        template = "SELECT TOP %{{count}}% * FROM T"
        pq = substitute_parameters(template, {"count": 10})
        assert pq.display_sql == "SELECT TOP 10 * FROM T"
        assert pq.exec_sql == "SELECT TOP (?) * FROM T"
        assert pq.exec_params == [10]

    def test_float(self) -> None:
        template = "WHERE price > %{{min_price}}%"
        pq = substitute_parameters(template, {"min_price": 9.99})
        assert pq.display_sql == "WHERE price > 9.99"
        assert pq.exec_sql == "WHERE price > ?"
        assert pq.exec_params == [9.99]

    def test_boolean_true(self) -> None:
        template = "WHERE active = %{{flag}}%"
        pq = substitute_parameters(template, {"flag": True})
        assert pq.display_sql == "WHERE active = 1"
        assert pq.exec_sql == "WHERE active = ?"
        assert pq.exec_params == [1]

    def test_boolean_false(self) -> None:
        template = "WHERE active = %{{flag}}%"
        pq = substitute_parameters(template, {"flag": False})
        assert pq.display_sql == "WHERE active = 0"
        assert pq.exec_sql == "WHERE active = ?"
        assert pq.exec_params == [0]

    def test_quoted_string(self) -> None:
        """Token wrapped in quotes: '%{{name}}%' becomes ? placeholder."""
        template = "WHERE category = '%{{category_name}}%'"
        pq = substitute_parameters(template, {"category_name": "Novelty Shop"})
        assert pq.display_sql == "WHERE category = 'Novelty Shop'"
        assert pq.exec_sql == "WHERE category = ?"
        assert pq.exec_params == ["Novelty Shop"]

    def test_unquoted_string(self) -> None:
        """Unquoted string token still uses ? placeholder."""
        template = "WHERE category = %{{category_name}}%"
        pq = substitute_parameters(template, {"category_name": "Novelty Shop"})
        assert pq.display_sql == "WHERE category = Novelty Shop"
        assert pq.exec_sql == "WHERE category = ?"
        assert pq.exec_params == ["Novelty Shop"]


class TestSubstituteParametersMixed:
    """Templates with a mix of inline and parameterized values."""

    def test_template_with_keyword_and_integer(self) -> None:
        template = "SELECT TOP %{{count}}% * FROM T ORDER BY col %{{order}}%"
        pq = substitute_parameters(template, {"count": 5, "order": "DESC"})
        assert pq.display_sql == "SELECT TOP 5 * FROM T ORDER BY col DESC"
        assert pq.exec_sql == "SELECT TOP (?) * FROM T ORDER BY col DESC"
        assert pq.exec_params == [5]

    def test_template_with_expression_and_integer(self) -> None:
        template = (
            "SELECT TOP %{{count}}% * FROM T "
            "WHERE OrderDate >= %{{from_date}}% ORDER BY col %{{order}}%"
        )
        pq = substitute_parameters(
            template,
            {"count": 10, "from_date": "DATEADD(YEAR, -12, GETDATE())", "order": "ASC"},
        )
        assert pq.display_sql == (
            "SELECT TOP 10 * FROM T "
            "WHERE OrderDate >= DATEADD(YEAR, -12, GETDATE()) ORDER BY col ASC"
        )
        assert pq.exec_sql == (
            "SELECT TOP (?) * FROM T "
            "WHERE OrderDate >= DATEADD(YEAR, -12, GETDATE()) ORDER BY col ASC"
        )
        assert pq.exec_params == [10]

    def test_multiple_bind_params_preserve_order(self) -> None:
        template = "SELECT TOP %{{count}}% * FROM T WHERE days > %{{days}}%"
        pq = substitute_parameters(template, {"count": 5, "days": 30})
        assert pq.exec_sql == "SELECT TOP (?) * FROM T WHERE days > ?"
        assert pq.exec_params == [5, 30]

    def test_all_types_combined(self) -> None:
        """Keyword, expression, integer, and quoted string together."""
        template = (
            "SELECT TOP %{{count}}% col FROM T "
            "WHERE cat = '%{{cat}}%' AND dt >= %{{dt}}% "
            "ORDER BY col %{{order}}%"
        )
        pq = substitute_parameters(
            template,
            {"count": 3, "cat": "Toys", "dt": "GETDATE()", "order": "ASC"},
        )
        assert pq.exec_sql == (
            "SELECT TOP (?) col FROM T WHERE cat = ? AND dt >= GETDATE() ORDER BY col ASC"
        )
        assert pq.exec_params == [3, "Toys"]


class TestSubstituteParametersEdgeCases:
    """Edge cases and defensive behavior."""

    def test_unknown_token_ignored(self) -> None:
        template = "SELECT * FROM T WHERE %{{missing}}% = 1"
        pq = substitute_parameters(template, {"other": 42})
        assert pq.display_sql == template
        assert pq.exec_sql == template
        assert pq.exec_params == []

    def test_empty_params(self) -> None:
        template = "SELECT * FROM T"
        pq = substitute_parameters(template, {})
        assert pq.display_sql == template
        assert pq.exec_sql == template
        assert pq.exec_params == []

    def test_generic_object_falls_through(self) -> None:
        """Non-standard types are stringified in display and parameterized."""
        template = "WHERE col = %{{val}}%"
        pq = substitute_parameters(template, {"val": [1, 2]})
        assert pq.display_sql == "WHERE col = [1, 2]"
        assert pq.exec_sql == "WHERE col = ?"
        assert pq.exec_params == [[1, 2]]
