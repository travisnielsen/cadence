"""Unit tests for schema area detection and suggestion functions.

Tests _detect_schema_area, SCHEMA_SUGGESTIONS, ConversationOrchestrator._build_suggestions,
and update_context schema tracking.
"""

from unittest.mock import MagicMock

from entities.orchestrator.orchestrator import (
    SCHEMA_SUGGESTIONS,
    ConversationOrchestrator,
    _detect_schema_area,
)
from models import NL2SQLResponse, SchemaSuggestion

# ── _detect_schema_area ──────────────────────────────────────────────────


class TestDetectSchemaArea:
    """Tests for _detect_schema_area table-name parsing."""

    def test_sales(self) -> None:
        assert _detect_schema_area(["Sales.Orders"]) == "sales"

    def test_warehouse(self) -> None:
        assert _detect_schema_area(["Warehouse.StockItems"]) == "warehouse"

    def test_purchasing(self) -> None:
        assert _detect_schema_area(["Purchasing.PurchaseOrders"]) == "purchasing"

    def test_application(self) -> None:
        assert _detect_schema_area(["Application.People"]) == "application"

    def test_mixed_tables(self) -> None:
        """First table wins."""
        assert _detect_schema_area(["Sales.Orders", "Application.People"]) == "sales"

    def test_empty_tables(self) -> None:
        assert _detect_schema_area([]) is None

    def test_no_dot_table(self) -> None:
        assert _detect_schema_area(["Orders"]) is None

    def test_unknown_schema(self) -> None:
        assert _detect_schema_area(["Unknown.Table"]) is None


# ── SCHEMA_SUGGESTIONS ───────────────────────────────────────────────────


class TestSchemaSuggestions:
    """Tests for SCHEMA_SUGGESTIONS constant."""

    def test_has_four_areas(self) -> None:
        assert set(SCHEMA_SUGGESTIONS.keys()) == {"sales", "purchasing", "warehouse", "application"}


# ── SchemaSuggestion model ───────────────────────────────────────────────


class TestSchemaSuggestionModel:
    """Tests for SchemaSuggestion Pydantic model."""

    def test_serialization(self) -> None:
        s = SchemaSuggestion(title="Test", prompt="Test prompt")
        d = s.model_dump()
        assert d["title"] == "Test"
        assert d["prompt"] == "Test prompt"


# ── _build_suggestions ───────────────────────────────────────────────────


class TestBuildSuggestions:
    """Tests for ConversationOrchestrator._build_suggestions."""

    def test_none_area(self) -> None:
        assert ConversationOrchestrator._build_suggestions(None, 1) == []

    def test_sales_returns_three(self) -> None:
        result = ConversationOrchestrator._build_suggestions("sales", 1)
        assert len(result) == 3
        assert all(isinstance(s, SchemaSuggestion) for s in result)

    def test_depth_rotation(self) -> None:
        r1 = ConversationOrchestrator._build_suggestions("sales", 1)
        r2 = ConversationOrchestrator._build_suggestions("sales", 2)
        assert r1[0].title != r2[0].title

    def test_cross_area_at_depth_3(self) -> None:
        result = ConversationOrchestrator._build_suggestions("sales", 3)
        sales_titles = {s.title for s in SCHEMA_SUGGESTIONS["sales"]}
        assert result[-1].title not in sales_titles

    def test_empty_results(self) -> None:
        result = ConversationOrchestrator._build_suggestions("sales", 1, has_results=False)
        assert result[0].title == "Try broader filters"


# ── update_context schema tracking ───────────────────────────────────────


def _make_orchestrator() -> ConversationOrchestrator:
    """Create an orchestrator with a mocked client."""
    mock_client = MagicMock()
    return ConversationOrchestrator(client=mock_client)


class TestUpdateContextSchemaTracking:
    """Tests for update_context schema area and depth tracking."""

    def test_schema_tracking(self) -> None:
        orch = _make_orchestrator()
        response = NL2SQLResponse(
            sql_query="SELECT * FROM Sales.Orders",
            sql_response=[{"col": "val"}],
            row_count=1,
        )
        orch.update_context(response, template_json=None, params={})
        assert orch.context.current_schema_area == "sales"
        assert orch.context.schema_exploration_depth == 1

    def test_depth_increment(self) -> None:
        orch = _make_orchestrator()
        for _ in range(2):
            response = NL2SQLResponse(
                sql_query="SELECT * FROM Sales.Orders",
                sql_response=[{"col": "val"}],
                row_count=1,
            )
            orch.update_context(response, template_json=None, params={})
        assert orch.context.schema_exploration_depth == 2

    def test_area_change_resets_depth(self) -> None:
        orch = _make_orchestrator()
        sales = NL2SQLResponse(
            sql_query="SELECT * FROM Sales.Orders",
            sql_response=[{"col": "val"}],
            row_count=1,
        )
        warehouse = NL2SQLResponse(
            sql_query="SELECT * FROM Warehouse.StockItems",
            sql_response=[{"col": "val"}],
            row_count=1,
        )
        orch.update_context(sales, template_json=None, params={})
        orch.update_context(warehouse, template_json=None, params={})
        assert orch.context.current_schema_area == "warehouse"
        assert orch.context.schema_exploration_depth == 1
