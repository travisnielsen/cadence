"""Unit tests for extract_parameters() async function.

Tests the main ``extract_parameters()`` entry-point from the parameter
extractor module, covering deterministic fast-path extraction, LLM
fallback, ask_if_missing handling, reporter integration, error recovery,
and confidence scoring.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from entities.parameter_extractor.extractor import extract_parameters
from models import (
    ParameterDefinition,
    ParameterExtractionRequest,
    ParameterValidation,
    QueryTemplate,
)

from tests.conftest import SpyReporter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_template(
    *,
    intent: str = "test_intent",
    question: str = "test question",
    sql_template: str = "SELECT 1",
    parameters: list[ParameterDefinition] | None = None,
    template_id: str = "tpl_001",
) -> QueryTemplate:
    """Build a minimal QueryTemplate for testing."""
    return QueryTemplate(
        id=template_id,
        intent=intent,
        question=question,
        sql_template=sql_template,
        parameters=parameters or [],
    )


def _make_param(name: str, **kwargs: Any) -> ParameterDefinition:
    """Build a ParameterDefinition with sensible defaults.

    All keyword arguments are forwarded to ``ParameterDefinition``.
    """
    return ParameterDefinition(name=name, **kwargs)


def _make_request(
    user_query: str,
    template: QueryTemplate,
    previously_extracted: dict[str, Any] | None = None,
) -> ParameterExtractionRequest:
    """Build a ParameterExtractionRequest."""
    return ParameterExtractionRequest(
        user_query=user_query,
        template=template,
        previously_extracted=previously_extracted or {},
    )


def _mock_agent(response_text: str) -> MagicMock:
    """Build a mock ChatAgent whose run() returns *response_text*."""
    mock_content = MagicMock()
    mock_content.text = response_text
    mock_msg = MagicMock()
    mock_msg.contents = [mock_content]
    mock_response = MagicMock()
    mock_response.messages = [mock_msg]

    agent = MagicMock()
    agent.run = AsyncMock(return_value=mock_response)
    return agent


def _mock_thread() -> MagicMock:
    """Build a mock AgentThread."""
    return MagicMock()


# ===================================================================
# Deterministic Fast Path (no LLM)
# ===================================================================


class TestDeterministicFastPath:
    """Tests where extract_parameters resolves all params without LLM."""

    async def test_fuzzy_match_allowed_value(self) -> None:
        """Allowed-values param matched from query skips LLM."""
        param = _make_param(
            "category",
            validation=ParameterValidation(
                type="string",
                allowed_values=["Supermarket", "Computer Store", "Novelty Shop"],
            ),
        )
        template = _make_template(parameters=[param])
        request = _make_request("Show me supermarket data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert result.extracted_parameters is not None
        assert result.extracted_parameters["category"] == "Supermarket"
        agent.run.assert_not_called()

    async def test_default_value_satisfies(self) -> None:
        """Param with default_value is extracted without LLM."""
        param = _make_param("limit", default_value=10)
        template = _make_template(parameters=[param])
        request = _make_request("Show me data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert result.extracted_parameters is not None
        assert result.extracted_parameters["limit"] == 10
        agent.run.assert_not_called()

    async def test_default_policy_satisfies(self) -> None:
        """Param with default_policy is extracted without LLM."""
        param = _make_param("date_filter", default_policy="current_date")
        template = _make_template(parameters=[param])
        request = _make_request("Show me data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert result.extracted_parameters is not None
        assert result.extracted_parameters["date_filter"] == "current_date"
        assert result.defaults_used.get("date_filter") == "current_date"
        agent.run.assert_not_called()

    async def test_number_extraction_from_query(self) -> None:
        """'top 10' extracts integer param value 10."""
        param = _make_param(
            "top_n",
            validation=ParameterValidation(type="integer", min=1, max=100),
        )
        template = _make_template(parameters=[param])
        request = _make_request("Show me the top 10 customers", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert result.extracted_parameters is not None
        assert result.extracted_parameters["top_n"] == 10
        agent.run.assert_not_called()

    async def test_previously_extracted_params_merged(self) -> None:
        """Previously extracted params are preserved and merged."""
        param = _make_param(
            "category",
            validation=ParameterValidation(
                type="string",
                allowed_values=["Supermarket", "Computer Store"],
            ),
        )
        param2 = _make_param("city_name")
        template = _make_template(parameters=[param, param2])
        request = _make_request(
            "Show supermarket data",
            template,
            previously_extracted={"city_name": "Seattle"},
        )

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert result.extracted_parameters is not None
        assert result.extracted_parameters["category"] == "Supermarket"
        assert result.extracted_parameters["city_name"] == "Seattle"
        agent.run.assert_not_called()

    async def test_optional_param_not_required_for_fast_path(self) -> None:
        """Optional param missing doesn't block fast-path success."""
        required_param = _make_param("limit", default_value=10)
        optional_param = _make_param("sort_order", required=False)
        template = _make_template(parameters=[required_param, optional_param])
        request = _make_request("Show data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        agent.run.assert_not_called()

    async def test_no_parameters_template_succeeds(self) -> None:
        """Template with no parameters succeeds immediately."""
        template = _make_template(parameters=[])
        request = _make_request("Show all data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        agent.run.assert_not_called()


# ===================================================================
# LLM Fallback
# ===================================================================


class TestLLMFallback:
    """Tests where deterministic extraction is incomplete and LLM is called."""

    async def test_llm_success(self) -> None:
        """LLM extracts missing param and returns success."""
        param = _make_param("city_name")
        template = _make_template(parameters=[param])
        request = _make_request("Show data for Seattle", template)

        llm_response = json.dumps({
            "status": "success",
            "extracted_parameters": {"city_name": "Seattle"},
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert result.extracted_parameters is not None
        assert result.extracted_parameters["city_name"] == "Seattle"
        agent.run.assert_called_once()

    async def test_llm_needs_clarification(self) -> None:
        """LLM returns needs_clarification with missing params."""
        param = _make_param("city_name", ask_if_missing=True)
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        llm_response = json.dumps({
            "status": "needs_clarification",
            "missing_parameters": [
                {
                    "name": "city_name",
                    "description": "Which city?",
                    "validation_hint": "Enter a city name",
                },
            ],
            "clarification_prompt": "Which city would you like to see?",
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "needs_clarification"
        assert result.missing_parameters is not None
        assert len(result.missing_parameters) == 1
        assert result.missing_parameters[0].name == "city_name"
        agent.run.assert_called_once()

    async def test_llm_error(self) -> None:
        """LLM returns error JSON → SQLDraft with error status."""
        param = _make_param("city_name")
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        llm_response = json.dumps({
            "status": "error",
            "error": "Cannot determine city from query",
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "error"
        assert result.error is not None
        assert "city" in result.error.lower()
        agent.run.assert_called_once()

    async def test_llm_merges_with_deterministic(self) -> None:
        """Deterministic + LLM params merge; deterministic takes priority."""
        category_param = _make_param(
            "category",
            validation=ParameterValidation(
                type="string",
                allowed_values=["Supermarket", "Computer Store"],
            ),
        )
        city_param = _make_param("city_name")
        template = _make_template(parameters=[category_param, city_param])
        request = _make_request("supermarket data in Seattle", template)

        llm_response = json.dumps({
            "status": "success",
            "extracted_parameters": {
                "category": "Grocery Store",  # LLM gives different value
                "city_name": "Seattle",
            },
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert result.extracted_parameters is not None
        # Deterministic match takes priority over LLM
        assert result.extracted_parameters["category"] == "Supermarket"
        assert result.extracted_parameters["city_name"] == "Seattle"

    async def test_llm_response_in_code_fence(self) -> None:
        """LLM response wrapped in markdown code fence is parsed."""
        param = _make_param("city_name")
        template = _make_template(parameters=[param])
        request = _make_request("Show data for Seattle", template)

        llm_response = (
            '```json\n{"status": "success", "extracted_parameters": {"city_name": "Seattle"}}\n```'
        )
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert result.extracted_parameters is not None
        assert result.extracted_parameters["city_name"] == "Seattle"


# ===================================================================
# ask_if_missing Handling
# ===================================================================


class TestAskIfMissing:
    """Tests for ask_if_missing parameter behavior."""

    async def test_required_ask_if_missing_not_extracted(self) -> None:
        """Required ask_if_missing param not extracted → needs_clarification."""
        param = _make_param("city_name", ask_if_missing=True)
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        llm_response = json.dumps({
            "status": "success",
            "extracted_parameters": {},
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "needs_clarification"
        assert result.missing_parameters is not None
        assert any(mp.name == "city_name" for mp in result.missing_parameters)

    async def test_error_converted_to_clarification(self) -> None:
        """LLM error about ask_if_missing param converts to clarification."""
        param = _make_param("customer_category", ask_if_missing=True)
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        llm_response = json.dumps({
            "status": "error",
            "error": "Cannot determine customer category from query",
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "needs_clarification"
        assert result.missing_parameters is not None
        assert len(result.missing_parameters) >= 1
        assert result.missing_parameters[0].name == "customer_category"

    async def test_error_not_converted_without_ask_if_missing(self) -> None:
        """LLM error for param without ask_if_missing stays as error."""
        param = _make_param("city_name", ask_if_missing=False)
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        llm_response = json.dumps({
            "status": "error",
            "error": "Cannot determine city from query",
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "error"


# ===================================================================
# Reporter Integration
# ===================================================================


class TestReporterIntegration:
    """Tests that step_start and step_end are called correctly."""

    async def test_step_start_and_end_called(self) -> None:
        """SpyReporter records step_start and step_end events."""
        template = _make_template(parameters=[])
        request = _make_request("Show data", template)
        agent = _mock_agent("")
        thread = _mock_thread()
        reporter = SpyReporter()

        await extract_parameters(request, agent, thread, reporter)

        started = [e for e in reporter.events if e["status"] == "started"]
        completed = [e for e in reporter.events if e["status"] == "completed"]
        assert len(started) >= 1
        assert len(completed) >= 1
        assert started[0]["step"] == "Extracting parameters"
        assert completed[0]["step"] == "Extracting parameters"

    async def test_step_end_called_on_success(self) -> None:
        """step_end fires even when extraction succeeds on fast path."""
        param = _make_param("limit", default_value=5)
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)
        agent = _mock_agent("")
        thread = _mock_thread()
        reporter = SpyReporter()

        await extract_parameters(request, agent, thread, reporter)

        statuses = [e["status"] for e in reporter.events]
        assert "started" in statuses
        assert "completed" in statuses

    async def test_step_end_called_on_error(self) -> None:
        """step_end fires even when an exception occurs inside extraction."""
        param = _make_param("city_name")
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("LLM down"))
        thread = _mock_thread()
        reporter = SpyReporter()

        result = await extract_parameters(request, agent, thread, reporter)

        # Should still get step_end
        statuses = [e["status"] for e in reporter.events]
        assert "started" in statuses
        assert "completed" in statuses
        # And result should be error
        assert result.status == "error"


# ===================================================================
# Error Handling
# ===================================================================


class TestErrorHandling:
    """Tests for exception and malformed response recovery."""

    async def test_exception_during_extraction(self) -> None:
        """Exception during agent.run() → SQLDraft with error status."""
        param = _make_param("city_name")
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("Connection timeout"))
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "error"
        assert result.error is not None
        assert "Connection timeout" in result.error

    async def test_malformed_llm_response(self) -> None:
        """Malformed LLM response → error handling via parse failure."""
        param = _make_param("city_name")
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        agent = _mock_agent("This is not JSON at all, just plain text")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        # _parse_llm_response creates {"status": "error", "error": "Failed to parse..."}
        assert result.status == "error"
        assert result.error is not None

    async def test_empty_llm_response(self) -> None:
        """Empty LLM response is handled gracefully."""
        param = _make_param("city_name")
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        # Empty string cannot be parsed → error
        assert result.status == "error"

    async def test_llm_returns_none_text(self) -> None:
        """LLM response with no text content is handled."""
        param = _make_param("city_name")
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        # Mock agent with no text in contents
        mock_content = MagicMock()
        mock_content.text = None
        mock_msg = MagicMock()
        mock_msg.contents = [mock_content]
        mock_response = MagicMock()
        mock_response.messages = [mock_msg]
        agent = MagicMock()
        agent.run = AsyncMock(return_value=mock_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        # Empty response_text → parse error
        assert result.status == "error"


# ===================================================================
# Confidence Scoring
# ===================================================================


class TestConfidenceScoring:
    """Tests for per-parameter confidence scores in SQLDraft."""

    async def test_exact_match_confidence(self) -> None:
        """Param found via exact string match → confidence ~1.0."""
        param = _make_param(
            "category",
            validation=ParameterValidation(
                type="string",
                allowed_values=["Supermarket", "Computer Store"],
            ),
        )
        template = _make_template(parameters=[param])
        # "Supermarket" appears literally in query → exact_match
        request = _make_request("Show Supermarket data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert "category" in result.parameter_confidences
        assert result.parameter_confidences["category"] == pytest.approx(1.0)

    async def test_fuzzy_match_confidence(self) -> None:
        """Param found via fuzzy match → confidence 0.85 * weight."""
        param = _make_param(
            "category",
            confidence_weight=1.0,
            validation=ParameterValidation(
                type="string",
                allowed_values=["Computer Store", "Novelty Shop"],
            ),
        )
        template = _make_template(parameters=[param])
        # "computers" stem-matches "Computer Store" but "computer store"
        # is not a substring of the query → classified as fuzzy_match
        request = _make_request("Show computers data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert "category" in result.parameter_confidences
        # Fuzzy match: 0.85 * 1.0 = 0.85
        assert result.parameter_confidences["category"] == pytest.approx(0.85)

    async def test_default_value_confidence(self) -> None:
        """Param using default_value → confidence 0.7."""
        param = _make_param("limit", default_value=10, confidence_weight=1.0)
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert "limit" in result.parameter_confidences
        assert result.parameter_confidences["limit"] == pytest.approx(0.7)

    async def test_llm_validated_confidence(self) -> None:
        """LLM-extracted param that passes validation → confidence ~0.75."""
        param = _make_param(
            "category",
            validation=ParameterValidation(
                type="string",
                allowed_values=["Seattle", "Portland", "Denver"],
            ),
        )
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        llm_response = json.dumps({
            "status": "success",
            "extracted_parameters": {"category": "Seattle"},
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert "category" in result.parameter_confidences
        assert result.parameter_confidences["category"] == pytest.approx(0.75)

    async def test_llm_unvalidated_confidence(self) -> None:
        """LLM-extracted param without validation rules → confidence ~0.65."""
        param = _make_param("city_name")
        template = _make_template(parameters=[param])
        request = _make_request("Show Seattle data", template)

        llm_response = json.dumps({
            "status": "success",
            "extracted_parameters": {"city_name": "Seattle"},
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert "city_name" in result.parameter_confidences
        assert result.parameter_confidences["city_name"] == pytest.approx(0.65)

    async def test_confidence_weight_applied(self) -> None:
        """Low confidence_weight reduces effective confidence."""
        param = _make_param(
            "limit",
            default_value=10,
            confidence_weight=0.5,
        )
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        # default_value base=0.7, weight=0.5 → 0.35, but floor is 0.6
        assert result.parameter_confidences["limit"] == pytest.approx(0.6)


# ===================================================================
# Allowed Values Hydration
# ===================================================================


class TestAllowedValuesHydration:
    """Tests for database-sourced allowed values hydration."""

    async def test_hydrates_database_allowed_values(self) -> None:
        """Provider hydrates allowed values before extraction."""
        param = _make_param(
            "category",
            allowed_values_source="database",
            table="Sales.CustomerCategories",
            column="CategoryName",
        )
        template = _make_template(parameters=[param])
        request = _make_request("Show Supermarket data", template)

        # Mock provider returns values
        provider = AsyncMock()
        provider.get_allowed_values = AsyncMock(
            return_value=MagicMock(
                values=["Supermarket", "Computer Store", "Novelty Shop"],
                is_partial=False,
            )
        )

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(
            request,
            agent,
            thread,
            allowed_values_provider=provider,
        )

        assert result.status == "success"
        assert result.extracted_parameters is not None
        assert result.extracted_parameters["category"] == "Supermarket"
        provider.get_allowed_values.assert_called_once_with(
            "Sales.CustomerCategories", "CategoryName"
        )

    async def test_partial_cache_params_populated(self) -> None:
        """Partial cache params are tracked in the SQLDraft."""
        param = _make_param(
            "category",
            allowed_values_source="database",
            table="Sales.CustomerCategories",
            column="CategoryName",
        )
        template = _make_template(parameters=[param])
        request = _make_request("Show Supermarket data", template)

        provider = AsyncMock()
        provider.get_allowed_values = AsyncMock(
            return_value=MagicMock(
                values=["Supermarket", "Computer Store"],
                is_partial=True,
            )
        )

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(
            request,
            agent,
            thread,
            allowed_values_provider=provider,
        )

        assert result.status == "success"
        assert "category" in result.partial_cache_params

    async def test_no_provider_skips_hydration(self) -> None:
        """Without provider, database-sourced params are not hydrated."""
        param = _make_param(
            "category",
            allowed_values_source="database",
            table="Sales.CustomerCategories",
            column="CategoryName",
        )
        template = _make_template(parameters=[param])
        request = _make_request("Show Supermarket data", template)

        # LLM fallback since no allowed_values are hydrated
        llm_response = json.dumps({
            "status": "success",
            "extracted_parameters": {"category": "Supermarket"},
        })
        agent = _mock_agent(llm_response)
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        agent.run.assert_called_once()


# ===================================================================
# Return Value Structure
# ===================================================================


class TestReturnValueStructure:
    """Tests that SQLDraft fields are populated correctly."""

    async def test_template_metadata_in_draft(self) -> None:
        """SQLDraft includes template_id, template_json, reasoning."""
        param = _make_param("limit", default_value=5)
        template = _make_template(
            parameters=[param],
            template_id="tpl_042",
            intent="customer_list",
        )
        template.reasoning = "Lists customers by category"
        request = _make_request("Show data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.source == "template"
        assert result.template_id == "tpl_042"
        assert result.template_json is not None
        assert result.reasoning == "Lists customers by category"
        assert result.user_query == "Show data"

    async def test_defaults_used_tracked(self) -> None:
        """defaults_used dict tracks which params used defaults."""
        param1 = _make_param("limit", default_value=10)
        param2 = _make_param("offset", default_value=0)
        template = _make_template(parameters=[param1, param2])
        request = _make_request("Show data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert result.status == "success"
        assert result.defaults_used.get("limit") == 10
        assert result.defaults_used.get("offset") == 0

    async def test_parameter_definitions_passed_through(self) -> None:
        """parameter_definitions from template appear in SQLDraft."""
        param = _make_param("city_name", default_value="Portland")
        template = _make_template(parameters=[param])
        request = _make_request("Show data", template)

        agent = _mock_agent("")
        thread = _mock_thread()

        result = await extract_parameters(request, agent, thread)

        assert len(result.parameter_definitions) == 1
        assert result.parameter_definitions[0].name == "city_name"
