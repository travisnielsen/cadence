"""Unit tests for the build_query() async function.

Tests cover the success path, error handling, response parsing,
reporter integration, and edge cases for dynamic SQL generation
via the query builder.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from entities.query_builder.builder import _parse_llm_response, build_query
from models import QueryBuilderRequest, SQLDraft, TableColumn, TableMetadata

from tests.conftest import SpyReporter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_table(
    name: str = "Sales.Orders",
    *,
    description: str = "Order records",
    columns: list[TableColumn] | None = None,
) -> TableMetadata:
    """Build a minimal TableMetadata for testing.

    Args:
        name: Fully qualified table name.
        description: Table description.
        columns: Optional column list; defaults to a single column.

    Returns:
        A fresh TableMetadata instance.
    """
    if columns is None:
        columns = [
            TableColumn(
                name="OrderID",
                description="Primary key",
                data_type="int",
                is_primary_key=True,
                is_nullable=False,
            ),
        ]
    return TableMetadata(table=name, description=description, columns=columns)


def _make_request(
    user_query: str = "Show all orders",
    *,
    tables: list[TableMetadata] | None = None,
    retry_count: int = 0,
) -> QueryBuilderRequest:
    """Build a minimal QueryBuilderRequest.

    Args:
        user_query: The user's question.
        tables: Tables to include; defaults to one table.
        retry_count: Retry count for the request.

    Returns:
        A fresh QueryBuilderRequest instance.
    """
    if tables is None:
        tables = [_make_table()]
    return QueryBuilderRequest(
        user_query=user_query,
        tables=tables,
        retry_count=retry_count,
    )


def _mock_agent(response_text: str) -> MagicMock:
    """Create a mock ChatAgent that returns the given text.

    Args:
        response_text: The text the mock LLM should return.

    Returns:
        A MagicMock with an async ``run`` method.
    """
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
    """Create a mock AgentThread."""
    return MagicMock()


def _success_json(
    sql: str = "SELECT * FROM Sales.Orders",
    *,
    tables_used: list[str] | None = None,
    confidence: float = 0.85,
    reasoning: str = "Direct query",
) -> str:
    """Build a JSON string representing a successful LLM response.

    Args:
        sql: The SQL query.
        tables_used: Tables used in the query.
        confidence: Confidence score.
        reasoning: Reasoning text.

    Returns:
        JSON-encoded success response.
    """
    return json.dumps({
        "status": "success",
        "completed_sql": sql,
        "tables_used": tables_used or ["Sales.Orders"],
        "confidence": confidence,
        "reasoning": reasoning,
    })


def _error_json(error: str = "Cannot generate query") -> str:
    """Build a JSON string representing an error LLM response.

    Args:
        error: The error message.

    Returns:
        JSON-encoded error response.
    """
    return json.dumps({
        "status": "error",
        "error": error,
    })


# ── Success path ──────────────────────────────────────────────────────


class TestSuccessPath:
    """Tests where the LLM returns a valid success response."""

    async def test_basic_success(self) -> None:
        """LLM success response produces SQLDraft with source='dynamic'."""
        agent = _mock_agent(_success_json())
        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.status == "success"
        assert result.source == "dynamic"
        assert result.completed_sql == "SELECT * FROM Sales.Orders"
        assert result.tables_used == ["Sales.Orders"]
        assert result.confidence == pytest.approx(0.85)
        assert result.reasoning == "Direct query"

    async def test_tables_metadata_serialized(self) -> None:
        """Tables metadata is serialized to JSON in the result."""
        table = _make_table("Sales.Customers", description="Customer data")
        request = _make_request(tables=[table])
        agent = _mock_agent(
            _success_json(
                tables_used=["Sales.Customers"],
            )
        )

        result = await build_query(request, agent, _mock_thread())

        assert result.tables_metadata_json is not None
        parsed_meta = json.loads(result.tables_metadata_json)
        assert len(parsed_meta) == 1
        assert parsed_meta[0]["table"] == "Sales.Customers"

    async def test_retry_count_preserved(self) -> None:
        """Request retry_count flows through to the SQLDraft."""
        request = _make_request(retry_count=3)
        agent = _mock_agent(_success_json())

        result = await build_query(request, agent, _mock_thread())

        assert result.retry_count == 3

    async def test_confidence_clamped_above_one(self) -> None:
        """Confidence > 1.0 is clamped to 1.0."""
        agent = _mock_agent(_success_json(confidence=1.5))

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.confidence == pytest.approx(1.0)

    async def test_confidence_clamped_below_zero(self) -> None:
        """Confidence < 0.0 is clamped to 0.0."""
        agent = _mock_agent(_success_json(confidence=-0.3))

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.confidence == pytest.approx(0.0)

    async def test_missing_confidence_defaults(self) -> None:
        """Missing confidence key defaults to 0.5."""
        response = json.dumps({
            "status": "success",
            "completed_sql": "SELECT 1",
            "tables_used": [],
            "reasoning": "trivial",
        })
        agent = _mock_agent(response)

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.confidence == pytest.approx(0.5)

    async def test_non_numeric_confidence_defaults(self) -> None:
        """Non-numeric confidence value defaults to 0.5."""
        response = json.dumps({
            "status": "success",
            "completed_sql": "SELECT 1",
            "tables_used": [],
            "confidence": "high",
            "reasoning": "trivial",
        })
        agent = _mock_agent(response)

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.confidence == pytest.approx(0.5)

    async def test_user_query_preserved(self) -> None:
        """The user_query from the request is in the result."""
        request = _make_request(user_query="What are the top customers?")
        agent = _mock_agent(_success_json())

        result = await build_query(request, agent, _mock_thread())

        assert result.user_query == "What are the top customers?"

    async def test_multiple_tables(self) -> None:
        """Multiple tables are serialized and passed through."""
        tables = [
            _make_table("Sales.Orders"),
            _make_table("Sales.Customers", description="Customer list"),
        ]
        request = _make_request(tables=tables)
        response = json.dumps({
            "status": "success",
            "completed_sql": "SELECT o.* FROM Sales.Orders o JOIN Sales.Customers c ON o.CID=c.CID",
            "tables_used": ["Sales.Orders", "Sales.Customers"],
            "confidence": 0.9,
            "reasoning": "Join query",
        })
        agent = _mock_agent(response)

        result = await build_query(request, agent, _mock_thread())

        assert result.status == "success"
        assert len(result.tables_used) == 2
        meta = json.loads(result.tables_metadata_json)
        assert len(meta) == 2


# ── Error path ────────────────────────────────────────────────────────


class TestErrorPath:
    """Tests where the LLM returns an error or an exception occurs."""

    async def test_llm_returns_error(self) -> None:
        """LLM error response produces SQLDraft with status='error'."""
        agent = _mock_agent(_error_json("Insufficient table data"))

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.status == "error"
        assert result.source == "dynamic"
        assert result.error == "Insufficient table data"

    async def test_exception_during_run(self) -> None:
        """Exception in agent.run produces SQLDraft with status='error'."""
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.status == "error"
        assert result.source == "dynamic"
        assert "LLM unavailable" in result.error

    async def test_malformed_json_response(self) -> None:
        """Malformed JSON from LLM produces an error SQLDraft."""
        agent = _mock_agent("This is not JSON at all {{{")

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.status == "error"
        assert result.error is not None

    async def test_error_response_preserves_retry_count(self) -> None:
        """Error response still preserves retry_count from request."""
        request = _make_request(retry_count=2)
        agent = _mock_agent(_error_json())

        result = await build_query(request, agent, _mock_thread())

        assert result.status == "error"
        assert result.retry_count == 2

    async def test_error_response_preserves_tables_metadata(self) -> None:
        """Error response from LLM still includes tables_metadata_json."""
        agent = _mock_agent(_error_json())

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.tables_metadata_json is not None


# ── Response parsing ──────────────────────────────────────────────────


class TestResponseParsing:
    """Tests for _parse_llm_response and its integration in build_query."""

    def test_direct_json(self) -> None:
        """Direct JSON string is parsed correctly."""
        payload = {"status": "success", "completed_sql": "SELECT 1"}
        result = _parse_llm_response(json.dumps(payload))

        assert result["status"] == "success"
        assert result["completed_sql"] == "SELECT 1"

    def test_json_in_code_fence(self) -> None:
        """JSON wrapped in markdown code fence is parsed correctly."""
        payload = {"status": "success", "completed_sql": "SELECT 1"}
        text = f"```json\n{json.dumps(payload)}\n```"
        result = _parse_llm_response(text)

        assert result["status"] == "success"

    def test_json_with_surrounding_text(self) -> None:
        """JSON embedded in prose is extracted via regex fallback."""
        payload = {"status": "success", "completed_sql": "SELECT 1"}
        text = f"Here is my answer: {json.dumps(payload)} Hope that helps!"
        result = _parse_llm_response(text)

        assert result["status"] == "success"

    def test_completely_invalid_text(self) -> None:
        """Completely non-JSON text returns an error dict."""
        result = _parse_llm_response("I cannot help with that")

        assert result["status"] == "error"
        assert "Failed to parse" in result["error"]

    def test_empty_string(self) -> None:
        """Empty string returns an error dict."""
        result = _parse_llm_response("")

        assert result["status"] == "error"

    async def test_code_fence_integration(self) -> None:
        """Code-fenced JSON from LLM is handled end-to-end."""
        payload = {
            "status": "success",
            "completed_sql": "SELECT TOP 5 * FROM Sales.Orders",
            "tables_used": ["Sales.Orders"],
            "confidence": 0.7,
            "reasoning": "Top 5 orders",
        }
        text = f"```json\n{json.dumps(payload)}\n```"
        agent = _mock_agent(text)

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.status == "success"
        assert result.completed_sql == "SELECT TOP 5 * FROM Sales.Orders"


# ── Reporter integration ──────────────────────────────────────────────


class TestReporterIntegration:
    """Tests for step_start / step_end reporting."""

    async def test_step_events_on_success(self) -> None:
        """step_start and step_end are called on a successful run."""
        reporter = SpyReporter()
        agent = _mock_agent(_success_json())

        await build_query(_make_request(), agent, _mock_thread(), reporter=reporter)

        assert len(reporter.events) == 2
        assert reporter.events[0] == {"step": "Generating SQL", "status": "started"}
        assert reporter.events[1] == {"step": "Generating SQL", "status": "completed"}

    async def test_step_end_called_on_error(self) -> None:
        """step_end is called even when an exception occurs."""
        reporter = SpyReporter()
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("boom"))

        await build_query(_make_request(), agent, _mock_thread(), reporter=reporter)

        started = [e for e in reporter.events if e["status"] == "started"]
        completed = [e for e in reporter.events if e["status"] == "completed"]
        assert len(started) == 1
        assert len(completed) == 1

    async def test_step_end_called_on_malformed_json(self) -> None:
        """step_end fires even when response parsing fails."""
        reporter = SpyReporter()
        agent = _mock_agent("not json")

        await build_query(_make_request(), agent, _mock_thread(), reporter=reporter)

        assert reporter.events[-1]["status"] == "completed"


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge-case scenarios for build_query."""

    async def test_empty_tables_list(self) -> None:
        """Empty tables list still invokes the LLM and returns a result."""
        request = _make_request(tables=[])
        agent = _mock_agent(_success_json())

        result = await build_query(request, agent, _mock_thread())

        # The function should still call the agent
        agent.run.assert_awaited_once()
        assert isinstance(result, SQLDraft)

    async def test_empty_response_text(self) -> None:
        """Empty text from LLM produces an error SQLDraft."""
        mock_content = MagicMock()
        mock_content.text = ""
        mock_msg = MagicMock()
        mock_msg.contents = [mock_content]
        mock_response = MagicMock()
        mock_response.messages = [mock_msg]
        agent = MagicMock()
        agent.run = AsyncMock(return_value=mock_response)

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.status == "error"

    async def test_no_text_attribute_on_content(self) -> None:
        """Content without a text attribute results in empty response handling."""
        mock_content = MagicMock(spec=[])  # no attributes at all
        mock_msg = MagicMock()
        mock_msg.contents = [mock_content]
        mock_response = MagicMock()
        mock_response.messages = [mock_msg]
        agent = MagicMock()
        agent.run = AsyncMock(return_value=mock_response)

        result = await build_query(_make_request(), agent, _mock_thread())

        assert result.status == "error"

    async def test_agent_called_with_correct_prompt_and_thread(self) -> None:
        """Verify agent.run receives the generation prompt and thread."""
        agent = _mock_agent(_success_json())
        thread = _mock_thread()

        await build_query(_make_request(user_query="Top sellers"), agent, thread)

        agent.run.assert_awaited_once()
        call_args = agent.run.call_args
        prompt_arg = call_args[0][0]
        assert "Top sellers" in prompt_arg
        assert call_args[1]["thread"] is thread

    async def test_table_columns_in_prompt(self) -> None:
        """Table column details appear in the prompt sent to the agent."""
        col = TableColumn(
            name="CityName",
            description="Name of the city",
            data_type="nvarchar",
            is_primary_key=False,
            is_foreign_key=True,
            foreign_key_table="Application.Countries",
            foreign_key_column="CountryID",
        )
        table = _make_table("Application.Cities", columns=[col])
        request = _make_request(tables=[table])
        agent = _mock_agent(_success_json())

        await build_query(request, agent, _mock_thread())

        prompt = agent.run.call_args[0][0]
        assert "CityName" in prompt
        assert "Application.Cities" in prompt
