"""Unit tests for ``DataAssistant`` class.

Tests the assistant layer with a mocked ``Agent``.  No network,
no Azure credentials.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from assistant.assistant import (
    SCHEMA_SUGGESTIONS,
    ClassificationResult,
    ConversationContext,
    DataAssistant,
    _detect_schema_area,
)
from models import (
    ClarificationInfo,
    NL2SQLResponse,
    ScenarioIntent,
    SchemaSuggestion,
)
from shared.scenario_constants import SCENARIO_ROUTING_CONFIDENCE_THRESHOLD

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_agent(response_text: str = "") -> MagicMock:
    """Return a mocked ``Agent`` whose ``run()`` returns *response_text*."""
    agent = MagicMock()
    mock_thread = MagicMock()
    mock_thread.service_session_id = "test-thread-123"
    mock_thread.session_id = "local-session-123"
    agent.get_session.return_value = mock_thread
    agent.create_session.return_value = mock_thread

    result = MagicMock()
    result.text = response_text
    agent.run = AsyncMock(return_value=result)
    return agent


def _make_assistant(
    response_text: str = "",
    conversation_id: str | None = "test-thread-123",
) -> DataAssistant:
    """Create a ``DataAssistant`` with a mocked agent."""
    agent = _make_agent(response_text)
    return DataAssistant(agent=agent, conversation_id=conversation_id)


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
        assistant = DataAssistant(agent=agent, conversation_id="t1")

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

    async def test_pending_dynamic_confirmation_uses_llm_action(self) -> None:
        assistant = _make_assistant(
            '{"intent": "refinement", "query": "yes", "confirmation_action": "accept"}'
        )
        assistant.context.pending_dynamic_confirmation = True

        result = await assistant.classify_intent("yes")

        assert result.intent == "refinement"
        assert result.confirmation_action == "accept"
        assert assistant.agent.run.await_count == 1

    async def test_scenario_discovery_flag_parsed(self) -> None:
        response = '{"intent": "conversation", "scenario_discovery": true}'
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("What scenarios can you do?")

        assert result.intent == "conversation"
        assert result.scenario_discovery is True

    async def test_scenario_discovery_defaults_false(self) -> None:
        response = '{"intent": "conversation"}'
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("Hello!")

        assert result.intent == "conversation"
        assert result.scenario_discovery is False

    async def test_explore_data_not_scenario_discovery(self) -> None:
        """'Explore stock groups' is a data query, not scenario discovery."""
        response = '{"intent": "data_query", "query": "Explore stock groups and item categories"}'
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("Explore stock groups and item categories")

        assert result.intent == "data_query"
        assert result.scenario_discovery is False


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

    def test_dynamic_confirmation_acceptance_sets_confirm_previous_sql(self) -> None:
        assistant = _make_assistant()
        assistant.context.query_source = "dynamic"
        assistant.context.last_sql = "SELECT * FROM Sales.Orders"
        assistant.context.last_tables = ["Sales.Orders"]
        assistant.context.last_tables_json = '{"tables": []}'
        assistant.context.last_question = "show orders"
        assistant.context.pending_dynamic_confirmation = True

        classification = ClassificationResult(
            intent="refinement",
            query="yes",
            confirmation_action="accept",
        )

        request = assistant.build_nl2sql_request(classification)

        assert request.confirm_previous_sql is True
        assert assistant.context.pending_dynamic_confirmation is False

    def test_dynamic_confirmation_missing_action_reprompts_and_keeps_pending(self) -> None:
        assistant = _make_assistant()
        assistant.context.query_source = "dynamic"
        assistant.context.last_sql = "SELECT * FROM Sales.Orders"
        assistant.context.last_tables = ["Sales.Orders"]
        assistant.context.last_tables_json = '{"tables": []}'
        assistant.context.last_question = "show orders"
        assistant.context.pending_dynamic_confirmation = True

        classification = ClassificationResult(
            intent="refinement",
            query="yes",
            confirmation_action=None,
        )

        request = assistant.build_nl2sql_request(classification)

        assert request.confirm_previous_sql is False
        assert request.reprompt_pending_confirmation is True
        assert assistant.context.pending_dynamic_confirmation is True

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

    def test_dynamic_confirmation_sets_pending_flag(self) -> None:
        assistant = _make_assistant()
        response = _make_response(
            query_source="dynamic",
            needs_clarification=True,
            query_summary="Please confirm this dynamic query",
            sql_response=[],
            row_count=0,
        )

        assistant.update_context(response, None, {})

        assert assistant.context.pending_dynamic_confirmation is True

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
        assert "conversation_id" in rendered
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
        assistant = DataAssistant(agent=agent, conversation_id="t1")

        result = await assistant.handle_conversation("Hi")

        assert not result


# ── Constructor ──────────────────────────────────────────────────────────


class TestConstructor:
    """Tests for ``DataAssistant.__init__``."""

    def test_accepts_chat_agent(self) -> None:
        agent = _make_agent()
        assistant = DataAssistant(agent=agent, conversation_id="thread-1")

        assert assistant.agent is agent
        assert assistant._initial_conversation_id == "thread-1"

    def test_default_context(self) -> None:
        assistant = _make_assistant()

        assert isinstance(assistant.context, ConversationContext)
        assert not assistant.context.query_source
        assert assistant.context.current_schema_area is None

    def test_thread_id_property_before_thread_creation(self) -> None:
        assistant = _make_assistant(conversation_id="pre-set-id")

        assert assistant.conversation_id == "pre-set-id"

    async def test_thread_id_from_created_thread(self) -> None:
        assistant = _make_assistant()

        await assistant.get_or_create_conversation()

        assert assistant.conversation_id == "test-thread-123"


# ── Scenario intent classification (T012) ────────────────────────────────


class TestScenarioIntentClassification:
    """Tests for scenario (what-if) intent classification."""

    async def test_what_if_phrasing_classified_as_scenario(self) -> None:
        response = json.dumps({
            "intent": "scenario",
            "query": "what if prices increase 5%",
            "scenario_confidence": 0.92,
            "detected_patterns": ["what if", "prices increase"],
            "reason": "User proposes hypothetical price change",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("what if prices increase 5%")

        assert result.intent == "scenario"
        assert result.scenario_intent is not None
        assert result.scenario_intent.mode == "scenario"

    async def test_assume_phrasing_classified_as_scenario(
        self,
    ) -> None:
        response = json.dumps({
            "intent": "scenario",
            "query": "assume costs rise 10%",
            "scenario_confidence": 0.88,
            "detected_patterns": ["assume", "costs rise"],
            "reason": "User assumes cost increase",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("assume costs rise 10%")

        assert result.intent == "scenario"
        assert result.scenario_intent is not None

    async def test_if_changed_phrasing_classified_as_scenario(
        self,
    ) -> None:
        response = json.dumps({
            "intent": "scenario",
            "query": "if we changed supplier pricing",
            "scenario_confidence": 0.85,
            "detected_patterns": [
                "if we changed",
                "supplier pricing",
            ],
            "reason": "Hypothetical supplier pricing change",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("if we changed supplier pricing")

        assert result.intent == "scenario"
        assert result.scenario_intent is not None

    async def test_impact_phrasing_classified_as_scenario(
        self,
    ) -> None:
        response = json.dumps({
            "intent": "scenario",
            "query": "show me the impact of raising demand",
            "scenario_confidence": 0.80,
            "detected_patterns": ["impact", "raising demand"],
            "reason": "User wants impact of demand change",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("show me the impact of raising demand")

        assert result.intent == "scenario"
        assert result.scenario_intent is not None
        assert result.scenario_intent.mode == "scenario"

    async def test_scenario_intent_fields_populated(self) -> None:
        response = json.dumps({
            "intent": "scenario",
            "query": "what if prices increase 5%",
            "scenario_confidence": 0.95,
            "detected_patterns": [
                "what if",
                "prices increase",
                "5%",
            ],
            "reason": "Clear what-if with numeric assumption",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("what if prices increase 5%")

        assert result.scenario_intent is not None
        intent = result.scenario_intent
        assert intent.mode == "scenario"
        assert intent.confidence == 0.95
        assert intent.reason == "Clear what-if with numeric assumption"
        assert len(intent.detected_patterns) == 3
        assert "what if" in intent.detected_patterns

    async def test_scenario_below_confidence_falls_back(
        self,
    ) -> None:
        response = json.dumps({
            "intent": "scenario",
            "query": "something about changing things",
            "scenario_confidence": 0.3,
            "detected_patterns": ["changing"],
            "reason": "Possible scenario intent",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("something about changing things")

        assert result.intent == "data_query"
        assert result.scenario_intent is None

    async def test_scenario_at_threshold_classified(self) -> None:
        response = json.dumps({
            "intent": "scenario",
            "query": "what if costs go up",
            "scenario_confidence": (SCENARIO_ROUTING_CONFIDENCE_THRESHOLD),
            "detected_patterns": ["what if", "costs"],
            "reason": "At threshold",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("what if costs go up")

        assert result.intent == "scenario"
        assert result.scenario_intent is not None

    async def test_scenario_prompt_includes_what_if_rules(
        self,
    ) -> None:
        response = json.dumps({
            "intent": "scenario",
            "query": "what if prices go up",
            "scenario_confidence": 0.9,
            "detected_patterns": ["what if"],
            "reason": "Scenario",
        })
        assistant = _make_assistant(response)

        await assistant.classify_intent("what if prices go up")

        prompt_arg = assistant.agent.run.call_args[0][0]
        assert "scenario" in prompt_arg.lower()

    def test_build_assumption_set_infers_price(self) -> None:
        assistant = _make_assistant()
        intent = ScenarioIntent(
            mode="scenario",
            confidence=0.9,
            reason="Price change",
            detected_patterns=["prices increase"],
        )

        result = assistant.build_scenario_assumption_set(
            intent,
            "what if prices increase 5%",
        )

        assert result.scenario_type == "price_delta"
        assert not result.is_complete
        assert len(result.missing_requirements) > 0

    def test_build_assumption_set_infers_demand(self) -> None:
        assistant = _make_assistant()
        intent = ScenarioIntent(
            mode="scenario",
            confidence=0.9,
            reason="Demand change",
            detected_patterns=["demand increases"],
        )

        result = assistant.build_scenario_assumption_set(
            intent,
            "what if demand increases 20%",
        )

        assert result.scenario_type == "demand_delta"

    def test_build_assumption_set_infers_supplier(
        self,
    ) -> None:
        assistant = _make_assistant()
        intent = ScenarioIntent(
            mode="scenario",
            confidence=0.9,
            reason="Supplier cost change",
            detected_patterns=["supplier cost increase"],
        )

        result = assistant.build_scenario_assumption_set(
            intent,
            "what if supplier costs go up",
        )

        assert result.scenario_type == "supplier_cost_delta"


# ── Non-scenario regression routing (T013) ───────────────────────────────


class TestNonScenarioRegressionRouting:
    """Regression tests ensuring non-scenario prompts stay routed."""

    async def test_analytics_query_not_scenario(self) -> None:
        response = json.dumps({
            "intent": "data_query",
            "query": "show me total sales by category",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("show me total sales by category")

        assert result.intent == "data_query"
        assert result.scenario_intent is None

    async def test_greeting_not_scenario(self) -> None:
        response = '{"intent": "conversation"}'
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("hello")

        assert result.intent == "conversation"
        assert result.scenario_intent is None

    async def test_thanks_not_scenario(self) -> None:
        response = '{"intent": "conversation"}'
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("thanks")

        assert result.intent == "conversation"
        assert result.scenario_intent is None

    async def test_what_query_not_scenario(self) -> None:
        """'What' in a query does not trigger scenario."""
        response = json.dumps({
            "intent": "data_query",
            "query": "what is the total revenue",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("what is the total revenue?")

        assert result.intent == "data_query"
        assert result.scenario_intent is None

    async def test_refinement_not_scenario(self) -> None:
        response = json.dumps({
            "intent": "refinement",
            "query": "make it 90 days",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("make it 90 days")

        assert result.intent == "refinement"
        assert result.scenario_intent is None

    async def test_scenario_without_patterns_falls_back(
        self,
    ) -> None:
        """Scenario with no detected patterns falls back."""
        response = json.dumps({
            "intent": "scenario",
            "query": "some question",
            "scenario_confidence": 0.8,
            "detected_patterns": [],
            "reason": "Uncertain",
        })
        assistant = _make_assistant(response)

        result = await assistant.classify_intent("some question")

        assert result.intent == "data_query"
        assert result.scenario_intent is None
