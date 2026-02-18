"""Unit tests for ``DataAssistant`` class.

Tests the assistant layer with a mocked ``ChatAgent``.  No network,
no Azure credentials.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from entities.assistant.assistant import (
    SCHEMA_SUGGESTIONS,
    ClassificationResult,
    ConversationContext,
    DataAssistant,
    _detect_schema_area,
)
from models import ClarificationInfo, NL2SQLResponse, SchemaSuggestion

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_agent(response_text: str = "") -> MagicMock:
    """Return a mocked ``ChatAgent`` whose ``run()`` returns *response_text*."""
    agent = MagicMock()
    mock_thread = MagicMock()
    mock_thread.service_thread_id = "test-thread-123"
    agent.get_new_thread.return_value = mock_thread

    result = MagicMock()
    result.text = response_text
    agent.run = AsyncMock(return_value=result)
    return agent


def _make_assistant(
    response_text: str = "",
    thread_id: str | None = "test-thread-123",
) -> DataAssistant:
    """Create a ``DataAssistant`` with a mocked agent."""
    agent = _make_agent(response_text)
    return DataAssistant(agent=agent, thread_id=thread_id)


def _make_response(**overrides) -> NL2SQLResponse:
    """Create an ``NL2SQLResponse`` with sensible defaults."""
    defaults: dict = {
        "sql_query": "SELECT * FROM Sales.Orders",
        "sql_response": [{"OrderID": 1, "City": "Seattle"}],
        "columns": ["OrderID", "City"],
        "row_count": 1,
        "query_source": "template",
        "confidence_score": 0.92,
    }
    defaults.update(overrides)
    return NL2SQLResponse(**defaults)


_TEMPLATE_JSON = json.dumps({
    "id": "tpl-orders",
    "intent": "Show orders",
    "parameters": [{"name": "city", "column": "City"}],
})


# ── classify_intent ──────────────────────────────────────────────────────


class TestClassifyIntent:
    """Tests for ``DataAssistant.classify_intent``."""

    async def test_data_query_intent(self) -> None:
        response = '{"intent": "data_query", "query": "Show orders from Seattle"}'
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("Show orders from Seattle")

        assert result.intent == "data_query"
        assert result.query == "Show orders from Seattle"

    async def test_refinement_intent(self) -> None:
        response = '{"intent": "refinement", "query": "Change to 90 days"}'
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("make it 90 days")

        assert result.intent == "refinement"
        assert result.query == "Change to 90 days"

    async def test_conversation_intent(self) -> None:
        response = '{"intent": "conversation"}'
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("Hello there!")

        assert result.intent == "conversation"

    async def test_parse_failure_defaults_to_conversation(self) -> None:
        assistant = _make_assistant("This is not JSON at all.")

        result = await assistant.classify_intent("gibberish")

        assert result.intent == "conversation"

    async def test_json_embedded_in_text(self) -> None:
        response = (
            'Here is the classification: {"intent": "data_query", "query": "Show top customers"}'
        )
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("Show top customers")

        assert result.intent == "data_query"
        assert result.query == "Show top customers"

    async def test_context_included_for_template_refinement(self) -> None:
        assistant = _make_assistant('{"intent": "refinement", "query": "change city"}')
        assistant.context.query_source = "template"
        assistant.context.last_template_json = _TEMPLATE_JSON
        assistant.context.last_params = {"city": "Seattle"}
        assistant.context.last_defaults_used = {}
        assistant.context.last_query = "Show orders"

        await assistant.classify_intent("change city to Portland")

        prompt_arg = assistant.agent.run.call_args[0][0]
        assert "TEMPLATE-BASED" in prompt_arg
        assert "city" in prompt_arg

    async def test_context_included_for_dynamic_refinement(self) -> None:
        assistant = _make_assistant('{"intent": "refinement", "query": "add filter"}')
        assistant.context.query_source = "dynamic"
        assistant.context.last_sql = "SELECT * FROM Sales.Orders"
        assistant.context.last_question = "show orders"
        assistant.context.last_tables = ["Sales.Orders"]

        await assistant.classify_intent("add a date filter")

        prompt_arg = assistant.agent.run.call_args[0][0]
        assert "DYNAMIC" in prompt_arg
        assert "Sales.Orders" in prompt_arg

    async def test_empty_response_defaults_to_conversation(self) -> None:
        agent = _make_agent("")
        result_mock = MagicMock()
        result_mock.text = None
        agent.run = AsyncMock(return_value=result_mock)
        assistant = DataAssistant(agent=agent, thread_id="t1")

        result = await assistant.classify_intent("hello")

        assert result.intent == "conversation"

    async def test_param_overrides_extracted(self) -> None:
        response = json.dumps({
            "intent": "refinement",
            "query": "change to Portland",
            "param_overrides": {"city": "Portland"},
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("change to Portland")

        assert result.param_overrides == {"city": "Portland"}


# ── build_nl2sql_request ─────────────────────────────────────────────────


class TestBuildNL2SQLRequest:
    """Tests for ``DataAssistant.build_nl2sql_request``."""

    def test_new_data_query(self) -> None:
        assistant = _make_assistant()
        classification = ClassificationResult(
            intent="data_query",
            query="Show orders from Seattle",
        )

        request = assistant.build_nl2sql_request(classification)

        assert request.user_query == "Show orders from Seattle"
        assert request.is_refinement is False

    def test_template_refinement(self) -> None:
        assistant = _make_assistant()
        assistant.context.query_source = "template"
        assistant.context.last_template_json = _TEMPLATE_JSON
        assistant.context.last_params = {"city": "Seattle"}

        classification = ClassificationResult(
            intent="refinement",
            query="change city to Portland",
            param_overrides={"city": "Portland"},
        )

        request = assistant.build_nl2sql_request(classification)

        assert request.is_refinement is True
        assert request.previous_template_json == _TEMPLATE_JSON
        assert request.base_params == {"city": "Seattle"}
        assert request.param_overrides == {"city": "Portland"}

    def test_dynamic_refinement(self) -> None:
        assistant = _make_assistant()
        assistant.context.query_source = "dynamic"
        assistant.context.last_sql = "SELECT * FROM Sales.Orders"
        assistant.context.last_tables = ["Sales.Orders"]
        assistant.context.last_tables_json = '{"tables": []}'
        assistant.context.last_question = "show orders"

        classification = ClassificationResult(
            intent="refinement",
            query="add date filter",
        )

        request = assistant.build_nl2sql_request(classification)

        assert request.is_refinement is True
        assert request.previous_sql == "SELECT * FROM Sales.Orders"
        assert request.previous_tables == ["Sales.Orders"]
        assert request.previous_tables_json == '{"tables": []}'
        assert request.previous_question == "show orders"

    def test_refinement_without_context_falls_back(self) -> None:
        assistant = _make_assistant()

        classification = ClassificationResult(
            intent="refinement",
            query="change to 90 days",
        )

        request = assistant.build_nl2sql_request(classification)

        assert request.is_refinement is False
        assert request.user_query == "change to 90 days"


# ── update_context ───────────────────────────────────────────────────────


class TestUpdateContext:
    """Tests for ``DataAssistant.update_context``."""

    def test_template_query_updates_context(self) -> None:
        assistant = _make_assistant()
        response = _make_response(
            query_source="template",
            tables_used=["Sales.Orders"],
        )

        assistant.update_context(response, _TEMPLATE_JSON, {"city": "Seattle"})

        assert assistant.context.last_template_json == _TEMPLATE_JSON
        assert assistant.context.last_params == {"city": "Seattle"}
        assert assistant.context.query_source == "template"
        assert assistant.context.last_sql is None
        assert assistant.context.last_tables == []

    def test_dynamic_query_updates_context(self) -> None:
        assistant = _make_assistant()
        response = _make_response(
            query_source="dynamic",
            tables_used=["Sales.Orders"],
            tables_metadata_json='{"tables": []}',
            original_question="show me orders",
        )

        assistant.update_context(response, None, {})

        assert assistant.context.last_sql == "SELECT * FROM Sales.Orders"
        assert assistant.context.last_tables == ["Sales.Orders"]
        assert assistant.context.query_source == "dynamic"
        assert assistant.context.last_template_json is None
        assert assistant.context.last_params == {}

    def test_error_response_does_not_update(self) -> None:
        assistant = _make_assistant()
        original_context = ConversationContext()
        assistant.context = original_context

        response = _make_response(error="SQL execution failed")

        assistant.update_context(response, _TEMPLATE_JSON, {"city": "Seattle"})

        assert not assistant.context.query_source
        assert assistant.context.last_template_json is None

    def test_schema_area_detected(self) -> None:
        assistant = _make_assistant()
        response = _make_response(tables_used=["Sales.Orders"])

        assistant.update_context(response, _TEMPLATE_JSON, {})

        assert assistant.context.current_schema_area == "sales"

    def test_schema_depth_increments_same_area(self) -> None:
        assistant = _make_assistant()
        response1 = _make_response(tables_used=["Sales.Orders"])
        response2 = _make_response(
            sql_query="SELECT * FROM Sales.Invoices",
            tables_used=["Sales.Invoices"],
        )

        assistant.update_context(response1, _TEMPLATE_JSON, {})
        assistant.update_context(response2, _TEMPLATE_JSON, {})

        assert assistant.context.schema_exploration_depth == 2
        assert assistant.context.current_schema_area == "sales"

    def test_schema_depth_resets_on_area_change(self) -> None:
        assistant = _make_assistant()
        sales_response = _make_response(tables_used=["Sales.Orders"])
        warehouse_response = _make_response(
            sql_query="SELECT * FROM Warehouse.StockItems",
            tables_used=["Warehouse.StockItems"],
        )

        assistant.update_context(sales_response, _TEMPLATE_JSON, {})
        assert assistant.context.schema_exploration_depth == 1

        assistant.update_context(warehouse_response, None, {})
        assert assistant.context.schema_exploration_depth == 1
        assert assistant.context.current_schema_area == "warehouse"


# ── enrich_response ──────────────────────────────────────────────────────


class TestEnrichResponse:
    """Tests for ``DataAssistant.enrich_response``."""

    def test_adds_suggestions_for_success(self) -> None:
        assistant = _make_assistant()
        assistant.context.current_schema_area = "sales"
        assistant.context.schema_exploration_depth = 1

        response = _make_response()

        enriched = assistant.enrich_response(response)

        assert len(enriched.suggestions) > 0
        assert all(isinstance(s, SchemaSuggestion) for s in enriched.suggestions)

    def test_no_suggestions_for_error(self) -> None:
        assistant = _make_assistant()
        assistant.context.current_schema_area = "sales"
        assistant.context.schema_exploration_depth = 1

        response = _make_response(error="query failed")

        enriched = assistant.enrich_response(response)

        assert enriched.suggestions == []

    def test_no_suggestions_for_clarification(self) -> None:
        assistant = _make_assistant()
        assistant.context.current_schema_area = "sales"
        assistant.context.schema_exploration_depth = 1

        response = _make_response(
            needs_clarification=True,
            clarification=ClarificationInfo(
                parameter_name="city",
                prompt="Which city?",
                allowed_values=["Seattle", "Portland"],
            ),
        )

        enriched = assistant.enrich_response(response)

        assert enriched.suggestions == []


# ── render_response ──────────────────────────────────────────────────────


class TestRenderResponse:
    """Tests for ``DataAssistant.render_response``."""

    def test_successful_response_with_data(self) -> None:
        assistant = _make_assistant()
        response = _make_response()

        rendered = assistant.render_response(response)

        assert "text" in rendered
        assert "thread_id" in rendered
        assert "tool_call" in rendered
        assert rendered["tool_call"]["result"]["sql_query"] == "SELECT * FROM Sales.Orders"
        assert rendered["tool_call"]["result"]["sql_response"] == [
            {"OrderID": 1, "City": "Seattle"},
        ]

    def test_clarification_response(self) -> None:
        assistant = _make_assistant()
        response = _make_response(
            needs_clarification=True,
            clarification=ClarificationInfo(
                parameter_name="city",
                prompt="Which city do you want?",
                allowed_values=["Seattle", "Portland"],
            ),
        )

        rendered = assistant.render_response(response)

        assert "**Which city do you want?**" in rendered["text"]

    def test_error_response(self) -> None:
        assistant = _make_assistant()
        response = _make_response(error="SQL execution failed")

        rendered = assistant.render_response(response)

        assert rendered["text"].startswith("**Error:**")

    def test_defaults_used_response(self) -> None:
        assistant = _make_assistant()
        response = _make_response(
            defaults_used={"days": "Using default of 30 days"},
        )

        rendered = assistant.render_response(response)

        assert "Using default:" in rendered["text"]


# ── _build_suggestions ───────────────────────────────────────────────────


class TestBuildSuggestions:
    """Tests for ``DataAssistant._build_suggestions``."""

    def test_returns_suggestions_for_valid_area(self) -> None:
        suggestions = DataAssistant._build_suggestions("sales", 1)

        assert len(suggestions) >= 2
        assert all(isinstance(s, SchemaSuggestion) for s in suggestions)

    def test_returns_empty_for_none_area(self) -> None:
        suggestions = DataAssistant._build_suggestions(None, 1)

        assert suggestions == []

    def test_rotates_based_on_depth(self) -> None:
        s1 = DataAssistant._build_suggestions("sales", 1)
        s2 = DataAssistant._build_suggestions("sales", 2)

        assert s1[0].title != s2[0].title

    def test_cross_area_at_depth_threshold(self) -> None:
        suggestions = DataAssistant._build_suggestions("sales", 3)

        sales_titles = {s.title for s in SCHEMA_SUGGESTIONS["sales"]}
        has_cross_area = any(s.title not in sales_titles for s in suggestions)
        assert has_cross_area

    def test_recovery_suggestion_for_empty_results(self) -> None:
        suggestions = DataAssistant._build_suggestions(
            "sales",
            1,
            has_results=False,
        )

        assert suggestions[0].title == "Try broader filters"
        assert "sales" in suggestions[0].prompt


# ── _detect_schema_area ──────────────────────────────────────────────────


class TestDetectSchemaArea:
    """Tests for the module-level ``_detect_schema_area`` function."""

    def test_extracts_sales(self) -> None:
        assert _detect_schema_area(["Sales.Orders"]) == "sales"

    def test_returns_none_for_empty_list(self) -> None:
        assert _detect_schema_area([]) is None

    def test_returns_none_for_unknown_schema(self) -> None:
        assert _detect_schema_area(["Unknown.Table"]) is None

    def test_returns_none_for_unqualified_name(self) -> None:
        assert _detect_schema_area(["Orders"]) is None

    def test_extracts_warehouse(self) -> None:
        assert _detect_schema_area(["Warehouse.StockItems"]) == "warehouse"

    def test_extracts_application(self) -> None:
        assert _detect_schema_area(["Application.Cities"]) == "application"


# ── handle_conversation ──────────────────────────────────────────────────


class TestHandleConversation:
    """Tests for ``DataAssistant.handle_conversation``."""

    async def test_returns_agent_response(self) -> None:
        assistant = _make_assistant("Hello! How can I help?")

        result = await assistant.handle_conversation("Hi there")

        assert result == "Hello! How can I help?"

    async def test_returns_empty_on_none(self) -> None:
        agent = _make_agent("")
        result_mock = MagicMock()
        result_mock.text = None
        agent.run = AsyncMock(return_value=result_mock)
        assistant = DataAssistant(agent=agent, thread_id="t1")

        result = await assistant.handle_conversation("Hi")

        assert not result


# ── Constructor ──────────────────────────────────────────────────────────


class TestConstructor:
    """Tests for ``DataAssistant.__init__``."""

    def test_accepts_chat_agent(self) -> None:
        agent = _make_agent()
        assistant = DataAssistant(agent=agent, thread_id="thread-1")

        assert assistant.agent is agent
        assert assistant._initial_thread_id == "thread-1"

    def test_default_context(self) -> None:
        assistant = _make_assistant()

        assert isinstance(assistant.context, ConversationContext)
        assert not assistant.context.query_source
        assert assistant.context.current_schema_area is None

    def test_thread_id_property_before_thread_creation(self) -> None:
        assistant = _make_assistant(thread_id="pre-set-id")

        assert assistant.thread_id == "pre-set-id"

    async def test_thread_id_from_created_thread(self) -> None:
        assistant = _make_assistant()

        await assistant.get_or_create_thread()

        assert assistant.thread_id == "test-thread-123"
