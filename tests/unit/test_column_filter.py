"""Unit tests for column refinement pure functions.

Tests refine_columns(), _is_empty_column(), _rank_columns() for:
- Empty column detection (all NULL, all empty string, mixed)
- Partial data retention (columns with some data kept)
- Column capping with relevance ranking
- Edge cases (zero rows, single column, all empty, order preservation)
- max_cols parameter behavior
"""

from entities.shared.column_filter import (
    ColumnRefinementResult,
    _is_empty_column,
    _rank_columns,
    refine_columns,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_rows(data: dict[str, list]) -> list[dict]:
    """Build row dicts from column-oriented data.

    Example:
        _make_rows({"a": [1, 2], "b": [3, 4]})
        => [{"a": 1, "b": 3}, {"a": 2, "b": 4}]
    """
    keys = list(data.keys())
    length = len(next(iter(data.values())))
    return [{k: data[k][i] for k in keys} for i in range(length)]


# ── _is_empty_column ────────────────────────────────────────────────────


class TestIsEmptyColumn:
    """Tests for _is_empty_column helper."""

    def test_all_none(self) -> None:
        rows = [{"col": None}, {"col": None}, {"col": None}]
        assert _is_empty_column(rows, "col") is True

    def test_all_empty_string(self) -> None:
        rows = [{"col": ""}, {"col": ""}, {"col": ""}]
        assert _is_empty_column(rows, "col") is True

    def test_mixed_none_and_empty(self) -> None:
        rows = [{"col": None}, {"col": ""}, {"col": None}]
        assert _is_empty_column(rows, "col") is True

    def test_has_data(self) -> None:
        rows = [{"col": None}, {"col": "value"}, {"col": None}]
        assert _is_empty_column(rows, "col") is False

    def test_zero_value_not_empty(self) -> None:
        rows = [{"col": 0}, {"col": 0}]
        assert _is_empty_column(rows, "col") is False

    def test_false_value_not_empty(self) -> None:
        rows = [{"col": False}]
        assert _is_empty_column(rows, "col") is False

    def test_missing_key_treated_as_none(self) -> None:
        rows = [{"other": 1}, {"other": 2}]
        assert _is_empty_column(rows, "col") is True


# ── Empty column stripping ──────────────────────────────────────────────


class TestEmptyColumnStripping:
    """Tests that refine_columns strips fully empty columns."""

    def test_strips_all_null_column(self) -> None:
        rows = _make_rows({"name": ["Alice", "Bob"], "age": [30, 25], "notes": [None, None]})
        result = refine_columns(["name", "age", "notes"], rows)
        assert result.columns == ["name", "age"]
        assert result.hidden_columns == []

    def test_strips_all_empty_string_column(self) -> None:
        rows = _make_rows({"name": ["Alice", "Bob"], "bio": ["", ""]})
        result = refine_columns(["name", "bio"], rows)
        assert result.columns == ["name"]
        assert result.hidden_columns == []

    def test_keeps_column_with_partial_data(self) -> None:
        rows = _make_rows({"name": ["Alice", "Bob"], "notes": [None, "important"]})
        result = refine_columns(["name", "notes"], rows)
        assert result.columns == ["name", "notes"]

    def test_strips_multiple_empty_columns(self) -> None:
        rows = _make_rows({
            "id": [1, 2],
            "name": ["A", "B"],
            "empty1": [None, None],
            "empty2": ["", ""],
            "value": [10, 20],
        })
        result = refine_columns(["id", "name", "empty1", "empty2", "value"], rows)
        assert result.columns == ["id", "name", "value"]


# ── Column capping ──────────────────────────────────────────────────────


class TestColumnCapping:
    """Tests for column capping and hidden_columns population."""

    def test_no_capping_under_limit(self) -> None:
        cols = ["a", "b", "c"]
        rows = _make_rows({"a": [1], "b": [2], "c": [3]})
        result = refine_columns(cols, rows, max_cols=5)
        assert result.columns == ["a", "b", "c"]
        assert result.hidden_columns == []

    def test_no_capping_at_limit(self) -> None:
        cols = ["a", "b", "c"]
        rows = _make_rows({"a": [1], "b": [2], "c": [3]})
        result = refine_columns(cols, rows, max_cols=3)
        assert result.columns == cols
        assert result.hidden_columns == []

    def test_caps_over_limit(self) -> None:
        cols = ["a", "b", "c", "d", "e"]
        rows = _make_rows({"a": [1], "b": [2], "c": [3], "d": [4], "e": [5]})
        result = refine_columns(cols, rows, max_cols=3)
        assert len(result.columns) == 3
        assert len(result.hidden_columns) == 2
        # All original columns accounted for
        assert set(result.columns + result.hidden_columns) == set(cols)

    def test_custom_max_cols(self) -> None:
        cols = [f"col{i}" for i in range(12)]
        rows = [dict.fromkeys(cols, i) for i in range(3)]
        result = refine_columns(cols, rows, max_cols=5)
        assert len(result.columns) == 5
        assert len(result.hidden_columns) == 7

    def test_default_max_cols_is_8(self) -> None:
        cols = [f"col{i}" for i in range(12)]
        rows = [dict.fromkeys(cols, i) for i in range(3)]
        result = refine_columns(cols, rows)
        assert len(result.columns) == 8
        assert len(result.hidden_columns) == 4

    def test_rows_preserved_intact(self) -> None:
        """Original rows (all columns) are preserved for client-side expansion."""
        cols = ["a", "b", "c"]
        rows = _make_rows({"a": [1], "b": [2], "c": [3]})
        result = refine_columns(cols, rows, max_cols=2)
        # Rows still contain all columns
        assert "a" in result.rows[0]
        assert "b" in result.rows[0]
        assert "c" in result.rows[0]


# ── Relevance ranking ───────────────────────────────────────────────────


class TestRelevanceRanking:
    """Tests for _rank_columns relevance ordering."""

    def test_user_mentioned_columns_first(self) -> None:
        ranked = _rank_columns(
            ["OrderID", "CustomerName", "OrderDate"],
            user_query="show me customer names",
            sql="SELECT OrderID, CustomerName, OrderDate FROM Sales.Orders",
        )
        assert ranked[0] == "CustomerName"

    def test_group_by_columns_high_priority(self) -> None:
        ranked = _rank_columns(
            ["Total", "CustomerName", "Region"],
            user_query="total sales",
            sql="SELECT CustomerName, Region, SUM(Amount) AS Total FROM Sales.Orders GROUP BY CustomerName, Region",
        )
        # "Total" matches user query ("total" in "total sales")
        assert ranked[0] == "Total"
        # GROUP BY columns should be near the top
        assert "CustomerName" in ranked[:3]
        assert "Region" in ranked[:3]

    def test_name_columns_ranked_higher_than_generic(self) -> None:
        ranked = _rank_columns(
            ["Description", "CustomerName", "Profit"],
            user_query="show profit by customer",
            sql="SELECT CustomerName, Description, Profit FROM Sales.OrderLines",
        )
        # "Profit" matches user query directly → tier 0
        # "CustomerName" matches via stem "customer" in query → tier 0, but "Profit" first by position
        # "Description" is generic → tier 3
        profit_idx = ranked.index("Profit")
        name_idx = ranked.index("CustomerName")
        desc_idx = ranked.index("Description")
        assert profit_idx < desc_idx
        assert name_idx < desc_idx

    def test_positional_tiebreaker(self) -> None:
        """Columns with same tier keep original order."""
        ranked = _rank_columns(
            ["ColA", "ColB", "ColC"],
            user_query="anything",
            sql="SELECT ColA, ColB, ColC FROM T",
        )
        # All tier 3 (no matches), so positional order preserved
        assert ranked == ["ColA", "ColB", "ColC"]


# ── Edge cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests for refine_columns."""

    def test_empty_rows(self) -> None:
        result = refine_columns(["a", "b"], [])
        assert result.columns == ["a", "b"]
        assert result.hidden_columns == []
        assert result.rows == []

    def test_empty_columns(self) -> None:
        result = refine_columns([], [{"a": 1}])
        assert result.columns == []
        assert result.hidden_columns == []

    def test_single_column(self) -> None:
        rows = [{"a": 1}, {"a": 2}]
        result = refine_columns(["a"], rows, max_cols=1)
        assert result.columns == ["a"]
        assert result.hidden_columns == []

    def test_all_columns_empty_keeps_originals(self) -> None:
        """When all columns are empty, keep all for display context."""
        rows = _make_rows({"a": [None, None], "b": ["", ""]})
        result = refine_columns(["a", "b"], rows)
        assert result.columns == ["a", "b"]
        assert result.hidden_columns == []

    def test_single_row(self) -> None:
        rows = [{"a": 1, "b": None, "c": "value"}]
        result = refine_columns(["a", "b", "c"], rows)
        assert result.columns == ["a", "c"]

    def test_result_is_frozen_dataclass(self) -> None:
        result = refine_columns(["a"], [{"a": 1}])
        assert isinstance(result, ColumnRefinementResult)

    def test_max_cols_one(self) -> None:
        cols = ["a", "b", "c"]
        rows = _make_rows({"a": [1], "b": [2], "c": [3]})
        result = refine_columns(cols, rows, max_cols=1)
        assert len(result.columns) == 1
        assert len(result.hidden_columns) == 2


# ── Integration: stripping + capping combined ────────────────────────────


class TestStrippingAndCapping:
    """Tests that stripping and capping work together correctly."""

    def test_strip_then_cap(self) -> None:
        """Empty columns are stripped before capping is applied."""
        cols = ["name", "empty1", "age", "empty2", "city", "score"]
        rows = _make_rows({
            "name": ["Alice"],
            "empty1": [None],
            "age": [30],
            "empty2": [""],
            "city": ["NYC"],
            "score": [95],
        })
        result = refine_columns(cols, rows, max_cols=3)
        # 2 empty columns stripped, leaving 4 non-empty, capped to 3
        assert len(result.columns) == 3
        assert len(result.hidden_columns) == 1
        assert "empty1" not in result.columns + result.hidden_columns
        assert "empty2" not in result.columns + result.hidden_columns

    def test_strip_enough_no_cap_needed(self) -> None:
        """Stripping brings count under the cap."""
        cols = ["a", "empty", "b", "c"]
        rows = _make_rows({"a": [1], "empty": [None], "b": [2], "c": [3]})
        result = refine_columns(cols, rows, max_cols=3)
        assert result.columns == ["a", "b", "c"]
        assert result.hidden_columns == []
