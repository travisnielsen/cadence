"""Integration tests for ``process_query()`` pipeline.

All tests use injected fakes — no Azure credentials, no network,
no filesystem access.  LLM-dependent functions (``extract_parameters``,
``build_query``) are mocked; pure validators may be mocked to test
specific routing branches.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from models import (
    ClarificationRequest,
    NL2SQLRequest,
    NL2SQLResponse,
    ParameterDefinition,
    PromptHint,
    ScenarioAssumption,
    ScenarioAssumptionSet,
    ScenarioComputationResult,
    ScenarioIntent,
    SQLDraft,
)
from nl2sql_controller.pipeline import (
    detect_sparse_signal,
    process_query,
    process_scenario_query,
)
from shared.scenario_math import aggregate_baseline, compute_scenario_metrics
from shared.scenario_narrative import (
    build_narrative_summary,
)
from workflow.clients import PipelineClients

_MOD = "nl2sql_controller.pipeline"

# ── Shared test data ─────────────────────────────────────────────────────

_ALLOWED = frozenset({"Sales.Orders", "Application.Cities"})

_TEMPLATE_DICT: dict = {
    "id": "tpl-orders-by-city",
    "intent": "Show orders by city",
    "question": "Show me orders from a city",
    "sql_template": "SELECT * FROM Sales.Orders WHERE City = %{{city}}%",
    "reasoning": "Filters orders by city name",
    "parameters": [
        {
            "name": "city",
            "column": "City",
            "required": True,
            "ask_if_missing": True,
            "validation": {"type": "string"},
        },
    ],
    "score": 0.92,
}

_TABLE_DICT: dict = {
    "id": "tbl-sales-orders",
    "table": "Sales.Orders",
    "datasource": "WideWorldImporters",
    "description": "Customer orders",
    "columns": [
        {"name": "OrderID", "data_type": "int"},
        {"name": "City", "data_type": "nvarchar"},
    ],
    "score": 0.88,
}

_SQL = "SELECT * FROM Sales.Orders WHERE City = 'Seattle'"
_ROWS = [{"OrderID": 1, "City": "Seattle"}, {"OrderID": 2, "City": "Seattle"}]
_COLS = ["OrderID", "City"]


# ── Helpers ──────────────────────────────────────────────────────────────

# Import test fakes from conftest (available on sys.path via pytest).
from tests.conftest import (
    FakeSqlExecutor,
    FakeTableSearch,
    FakeTemplateSearch,
    SequentialFakeSqlExecutor,
    SpyReporter,
)


def _make_clients(
    *,
    template_results: list[dict] | None = None,
    table_results: list[dict] | None = None,
    sql_rows: list[dict] | None = None,
    sql_columns: list[str] | None = None,
    sql_error: str | None = None,
    reporter: SpyReporter | None = None,
    allowed_tables: frozenset[str] | None = None,
) -> PipelineClients:
    """Build a ``PipelineClients`` with fakes for all I/O."""
    return PipelineClients(
        param_extractor_agent=MagicMock(),
        query_builder_agent=MagicMock(),
        template_search=FakeTemplateSearch(results=template_results or []),
        table_search=FakeTableSearch(tables=table_results or []),
        sql_executor=FakeSqlExecutor(
            rows=sql_rows or [],
            columns=sql_columns or [],
            error=sql_error,
        ),
        reporter=reporter or SpyReporter(),
        allowed_tables=allowed_tables or _ALLOWED,
    )


def _success_draft(  # noqa: PLR0913
    *,
    source: str = "template",
    sql: str = _SQL,
    user_query: str = "Show orders from Seattle",
    template_id: str | None = "tpl-orders-by-city",
    extracted_parameters: dict | None = None,
    confidence: float = 0.0,
    tables_used: list[str] | None = None,
    retry_count: int = 0,
    parameter_confidences: dict[str, float] | None = None,
    needs_confirmation: bool = False,
    parameter_definitions: list[ParameterDefinition] | None = None,
) -> SQLDraft:
    """Build a successful ``SQLDraft`` for testing."""
    return SQLDraft(
        status="success",
        source=source,
        completed_sql=sql,
        user_query=user_query,
        template_id=template_id if source == "template" else None,
        extracted_parameters=extracted_parameters or {"city": "Seattle"},
        confidence=confidence,
        tables_used=tables_used or [],
        retry_count=retry_count,
        parameter_confidences=parameter_confidences or {},
        needs_confirmation=needs_confirmation,
        parameter_definitions=parameter_definitions or [],
    )


def _step_names(reporter: SpyReporter) -> list[str]:
    """Extract unique step names from a SpyReporter's events."""
    return [e["step"] for e in reporter.events]


# ── 1. Template Match Path (Happy Path) ─────────────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.validate_parameters")
@patch(f"{_MOD}.extract_parameters", new_callable=AsyncMock)
async def test_template_path_returns_response_with_rows(
    mock_extract: AsyncMock,
    mock_val_params: MagicMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """High-confidence template match → extract → validate → execute → rows."""
    draft = _success_draft()
    mock_extract.return_value = draft
    mock_val_params.return_value = draft
    mock_val_query.return_value = draft

    spy = SpyReporter()
    clients = _make_clients(
        template_results=[_TEMPLATE_DICT],
        sql_rows=_ROWS,
        sql_columns=_COLS,
        reporter=spy,
    )
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.sql_query == _SQL
    assert result.row_count == 2
    assert result.query_source == "template"
    assert result.error is None
    assert "Understanding intent" in _step_names(spy)
    assert "Executing query" in _step_names(spy)
    mock_extract.assert_awaited_once()


# ── 2. Dynamic Query Path ────────────────────────────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
async def test_dynamic_path_returns_response(
    mock_build: AsyncMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """No template match → dynamic query via build_query → execute."""
    draft = _success_draft(
        source="dynamic",
        template_id=None,
        confidence=0.9,
        tables_used=["Sales.Orders"],
    )
    mock_build.return_value = draft
    mock_val_query.return_value = draft

    spy = SpyReporter()
    clients = _make_clients(
        table_results=[_TABLE_DICT],
        sql_rows=_ROWS,
        sql_columns=_COLS,
        reporter=spy,
    )
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.query_source == "dynamic"
    assert result.row_count == 2
    assert result.error is None
    assert "Searching tables" in _step_names(spy)
    mock_build.assert_awaited_once()


# ── 3. Ambiguous Template Match ──────────────────────────────────────────


async def test_ambiguous_match_returns_error() -> None:
    """Two templates with near-identical scores → ambiguity error."""
    tpl_a = {**_TEMPLATE_DICT, "score": 0.90, "intent": "Orders by city"}
    tpl_b = {**_TEMPLATE_DICT, "id": "tpl-2", "score": 0.89, "intent": "Orders by region"}

    clients = _make_clients(template_results=[tpl_a, tpl_b])
    request = NL2SQLRequest(user_query="Show orders")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is not None
    assert "multiple query types" in result.error


# ── 4. Clarification Path (Low Confidence) ──────────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.extract_parameters", new_callable=AsyncMock)
async def test_low_confidence_returns_clarification(
    mock_extract: AsyncMock,
    _mock_thread: MagicMock,
) -> None:
    """Parameter confidence < 0.6 → ClarificationRequest."""
    draft = _success_draft(
        parameter_confidences={"city": 0.35},
        extracted_parameters={"city": "Seattle"},
        parameter_definitions=[
            ParameterDefinition(
                name="city",
                column="City",
                required=True,
                validation={"type": "string"},
            ),
        ],
    )
    mock_extract.return_value = draft

    clients = _make_clients(template_results=[_TEMPLATE_DICT])
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    result = await process_query(request, clients)

    assert isinstance(result, ClarificationRequest)
    assert result.parameter_name == "city"
    assert result.prompt  # non-empty
    assert result.original_question == "Show orders from Seattle"


# ── 5. Needs Confirmation (Medium Confidence) ───────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.validate_parameters")
@patch(f"{_MOD}.extract_parameters", new_callable=AsyncMock)
async def test_medium_confidence_returns_response_with_confirmation(
    mock_extract: AsyncMock,
    mock_val_params: MagicMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """Parameter confidence in [0.6, 0.85) → response with confirmation_note."""
    draft = _success_draft(
        parameter_confidences={"city": 0.72},
        extracted_parameters={"city": "Seattle"},
    )
    # _apply_confidence_routing sets needs_confirmation=True for [0.6, 0.85)
    draft_with_confirm = draft.model_copy(update={"needs_confirmation": True})
    mock_extract.return_value = draft
    mock_val_params.return_value = draft_with_confirm
    mock_val_query.return_value = draft_with_confirm

    clients = _make_clients(
        template_results=[_TEMPLATE_DICT],
        sql_rows=_ROWS,
        sql_columns=_COLS,
    )
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is None
    assert result.confirmation_note  # non-empty


# ── 6. Parameter Validation Failure ──────────────────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_parameters")
@patch(f"{_MOD}.extract_parameters", new_callable=AsyncMock)
async def test_parameter_validation_failure_returns_error(
    mock_extract: AsyncMock,
    mock_val_params: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """Extraction succeeds but parameter validation fails."""
    draft = _success_draft()
    failed_draft = draft.model_copy(
        update={"parameter_violations": ["city must be a valid city name"]},
    )
    mock_extract.return_value = draft
    mock_val_params.return_value = failed_draft

    clients = _make_clients(template_results=[_TEMPLATE_DICT])
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is not None
    assert "Parameter validation failed" in result.error


# ── 7. Query Validation Failure → Retry Succeeds ────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
async def test_dynamic_query_validation_retry_succeeds(
    mock_build: AsyncMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """First dynamic draft fails validation; retry passes and executes."""
    initial_draft = _success_draft(
        source="dynamic",
        template_id=None,
        confidence=0.9,
        tables_used=["Sales.Orders"],
    )
    failed_draft = initial_draft.model_copy(
        update={"query_violations": ["Disallowed table: dbo.Secrets"]},
    )
    retried_draft = _success_draft(
        source="dynamic",
        template_id=None,
        sql="SELECT OrderID FROM Sales.Orders",
        confidence=0.9,
        tables_used=["Sales.Orders"],
    )

    # First build_query returns the initial draft;
    # second call (retry) returns the retried draft.
    mock_build.side_effect = [initial_draft, retried_draft]

    # First validate_query flags violations; second passes.
    mock_val_query.side_effect = [failed_draft, retried_draft]

    clients = _make_clients(
        table_results=[_TABLE_DICT],
        sql_rows=_ROWS,
        sql_columns=_COLS,
    )
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is None
    assert result.row_count == 2
    assert mock_build.await_count == 2


# ── 8. Query Validation Failure (Max Retries) ───────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
async def test_dynamic_query_validation_max_retries_returns_error(
    mock_build: AsyncMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """Dynamic draft fails validation twice → error with suggestions."""
    initial_draft = _success_draft(
        source="dynamic",
        template_id=None,
        confidence=0.9,
        tables_used=["Sales.Orders"],
    )
    failed_draft = initial_draft.model_copy(
        update={"query_violations": ["Disallowed table: dbo.Secrets"]},
    )
    retry_draft = _success_draft(
        source="dynamic",
        template_id=None,
        confidence=0.9,
        tables_used=["Sales.Orders"],
    )
    retry_failed = retry_draft.model_copy(
        update={"query_violations": ["Still referencing blocked table"]},
    )

    mock_build.side_effect = [initial_draft, retry_draft]
    # Both validate_query calls return violations.
    mock_val_query.side_effect = [failed_draft, retry_failed]

    clients = _make_clients(table_results=[_TABLE_DICT])
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is not None


# ── 9. Dynamic Query Confidence Gate ─────────────────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
async def test_dynamic_low_confidence_triggers_gate(
    mock_build: AsyncMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """Dynamic draft with confidence < 0.7 → needs_clarification gate."""
    draft = _success_draft(
        source="dynamic",
        template_id=None,
        confidence=0.5,
        tables_used=["Sales.Orders"],
    )
    mock_build.return_value = draft
    mock_val_query.return_value = draft  # passes validation

    clients = _make_clients(table_results=[_TABLE_DICT])
    request = NL2SQLRequest(user_query="Show something maybe")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.needs_clarification is True
    assert result.query_source == "dynamic"
    assert result.query_summary  # non-empty


# ── 10. No Tables Found (Dynamic Fallback) ──────────────────────────────


async def test_no_tables_returns_error() -> None:
    """No template match AND no relevant tables → error."""
    clients = _make_clients()  # empty template + table results
    request = NL2SQLRequest(user_query="Show me unicorn data")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is not None
    assert "matching query pattern" in result.error.lower()


# ── 11. Template Refinement ──────────────────────────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.validate_parameters")
@patch(f"{_MOD}.extract_parameters", new_callable=AsyncMock)
async def test_template_refinement_reuses_previous_template(
    mock_extract: AsyncMock,
    mock_val_params: MagicMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """Refinement with previous_template_json → re-runs template pipeline."""
    draft = _success_draft(
        sql="SELECT * FROM Sales.Orders WHERE City = 'Portland'",
        extracted_parameters={"city": "Portland"},
    )
    mock_extract.return_value = draft
    mock_val_params.return_value = draft
    mock_val_query.return_value = draft

    template_json = json.dumps(_TEMPLATE_DICT)
    request = NL2SQLRequest(
        user_query="Change city to Portland",
        is_refinement=True,
        previous_template_json=template_json,
        base_params={"city": "Seattle"},
        param_overrides={"city": "Portland"},
    )

    clients = _make_clients(sql_rows=_ROWS, sql_columns=_COLS)

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is None
    mock_extract.assert_awaited_once()

    # Verify the extraction request included merged params.
    call_args = mock_extract.call_args
    extraction_req = call_args[0][0]
    assert extraction_req.previously_extracted == {"city": "Portland"}


# ── 12. Dynamic Refinement ───────────────────────────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
async def test_dynamic_refinement_reuses_previous_tables(
    mock_build: AsyncMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """Dynamic refinement with previous_tables_json reuses table metadata."""
    draft = _success_draft(
        source="dynamic",
        template_id=None,
        confidence=0.9,
        tables_used=["Sales.Orders"],
    )
    mock_build.return_value = draft
    mock_val_query.return_value = draft

    tables_json = json.dumps([_TABLE_DICT])
    request = NL2SQLRequest(
        user_query="Sort by OrderID descending",
        is_refinement=True,
        previous_sql=_SQL,
        previous_question="Show orders from Seattle",
        previous_tables_json=tables_json,
    )

    clients = _make_clients(sql_rows=_ROWS, sql_columns=_COLS)

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is None
    mock_build.assert_awaited_once()

    # Table search should NOT have been called (tables reused).
    ts = clients.table_search
    assert not ts.calls  # type: ignore[union-attr]


@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
@patch(f"{_MOD}.validate_query")
async def test_dynamic_confirmation_acceptance_executes_previous_sql_directly(
    mock_validate_query: MagicMock,
    mock_build: AsyncMock,
) -> None:
    """Acceptance reply on dynamic refinement executes previous SQL without regeneration."""
    validated = _success_draft(
        source="dynamic",
        template_id=None,
        sql=_SQL,
        confidence=0.9,
        tables_used=["Sales.Orders"],
    )
    mock_validate_query.return_value = validated

    request = NL2SQLRequest(
        user_query="please run it",
        is_refinement=True,
        previous_sql=_SQL,
        previous_question="Show orders from Seattle",
        previous_tables=["Sales.Orders"],
        previous_tables_json=json.dumps([_TABLE_DICT]),
        confirm_previous_sql=True,
    )
    clients = _make_clients(sql_rows=_ROWS, sql_columns=_COLS)

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is None
    assert result.sql_query == _SQL
    assert result.query_source == "dynamic"
    mock_build.assert_not_awaited()


@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
async def test_dynamic_confirmation_missing_action_reprompts_gate(
    mock_build: AsyncMock,
) -> None:
    """Missing confirmation action should re-display confirmation gate without regeneration."""
    request = NL2SQLRequest(
        user_query="yes",
        is_refinement=True,
        previous_sql=_SQL,
        previous_question="Show orders from Seattle",
        previous_tables=["Sales.Orders"],
        previous_tables_json=json.dumps([_TABLE_DICT]),
        reprompt_pending_confirmation=True,
    )
    clients = _make_clients(sql_rows=_ROWS, sql_columns=_COLS)

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.needs_clarification is True
    assert result.query_source == "dynamic"
    assert result.query_summary
    mock_build.assert_not_awaited()


# ── 13. Error Recovery (Unexpected Exception) ───────────────────────────


async def test_unexpected_exception_returns_error() -> None:
    """An unexpected exception is caught and surfaced as an error response."""
    ts = FakeTemplateSearch(results=[_TEMPLATE_DICT])
    # Make the search method raise

    async def _boom(q: str) -> dict:
        raise RuntimeError("Kaboom")

    ts.search = _boom  # type: ignore[assignment]

    clients = PipelineClients(
        param_extractor_agent=MagicMock(),
        query_builder_agent=MagicMock(),
        template_search=ts,
        table_search=FakeTableSearch(),
        sql_executor=FakeSqlExecutor(),
        reporter=SpyReporter(),
        allowed_tables=_ALLOWED,
    )
    request = NL2SQLRequest(user_query="Show orders")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is not None
    assert result.error == "An error occurred processing your query. Please try again."


# ── 14. SQL Execution Failure ────────────────────────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.validate_parameters")
@patch(f"{_MOD}.extract_parameters", new_callable=AsyncMock)
async def test_sql_execution_failure_returns_error(
    mock_extract: AsyncMock,
    mock_val_params: MagicMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """SQL executor reports failure → NL2SQLResponse with error."""
    draft = _success_draft()
    mock_extract.return_value = draft
    mock_val_params.return_value = draft
    mock_val_query.return_value = draft

    clients = _make_clients(
        template_results=[_TEMPLATE_DICT],
        sql_error="Permission denied on Sales.Orders",
    )
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is not None
    assert "Permission denied" in result.error


# ── 15. Column Refinement for Dynamic Queries ────────────────────────────


@patch(f"{_MOD}.refine_columns")
@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
async def test_dynamic_path_calls_refine_columns(
    mock_build: AsyncMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
    mock_refine: MagicMock,
) -> None:
    """Dynamic queries pass through refine_columns for column display."""
    from shared.column_filter import ColumnRefinementResult

    draft = _success_draft(
        source="dynamic",
        template_id=None,
        confidence=0.9,
        tables_used=["Sales.Orders"],
    )
    mock_build.return_value = draft
    mock_val_query.return_value = draft

    mock_refine.return_value = ColumnRefinementResult(
        columns=["OrderID"],
        hidden_columns=["City"],
        rows=_ROWS,
    )

    clients = _make_clients(
        table_results=[_TABLE_DICT],
        sql_rows=_ROWS,
        sql_columns=_COLS,
    )
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    mock_refine.assert_called_once()
    assert result.columns == ["OrderID"]
    assert result.hidden_columns == ["City"]


# ── Edge cases ───────────────────────────────────────────────────────────


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.extract_parameters", new_callable=AsyncMock)
async def test_extraction_error_status_returns_error(
    mock_extract: AsyncMock,
    _mock_thread: MagicMock,
) -> None:
    """extract_parameters returns status='error' → NL2SQLResponse with error."""
    draft = SQLDraft(
        status="error",
        source="template",
        error="LLM extraction failed",
        user_query="Show orders",
    )
    mock_extract.return_value = draft

    clients = _make_clients(template_results=[_TEMPLATE_DICT])
    request = NL2SQLRequest(user_query="Show orders")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is not None
    assert "failed" in result.error.lower()


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
async def test_dynamic_build_error_returns_error(
    mock_build: AsyncMock,
    _mock_thread: MagicMock,
) -> None:
    """build_query returns status='error' → NL2SQLResponse with error."""
    draft = SQLDraft(
        status="error",
        source="dynamic",
        error="Failed to generate SQL",
        user_query="Show orders",
    )
    mock_build.return_value = draft

    clients = _make_clients(table_results=[_TABLE_DICT])
    request = NL2SQLRequest(user_query="Show orders")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.error is not None
    assert "failed" in result.error.lower()


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.validate_parameters")
@patch(f"{_MOD}.extract_parameters", new_callable=AsyncMock)
async def test_template_path_reporter_emits_understanding_intent(
    mock_extract: AsyncMock,
    mock_val_params: MagicMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """Reporter receives start+end events for 'Understanding intent'."""
    draft = _success_draft()
    mock_extract.return_value = draft
    mock_val_params.return_value = draft
    mock_val_query.return_value = draft

    spy = SpyReporter()
    clients = _make_clients(
        template_results=[_TEMPLATE_DICT],
        sql_rows=_ROWS,
        sql_columns=_COLS,
        reporter=spy,
    )
    request = NL2SQLRequest(user_query="Show orders from Seattle")

    await process_query(request, clients)

    intent_events = [e for e in spy.events if e["step"] == "Understanding intent"]
    assert len(intent_events) == 2
    assert intent_events[0]["status"] == "started"
    assert intent_events[1]["status"] == "completed"


@patch(f"{_MOD}.AgentSession")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.validate_parameters")
@patch(f"{_MOD}.extract_parameters", new_callable=AsyncMock)
async def test_template_path_empty_result_set(
    mock_extract: AsyncMock,
    mock_val_params: MagicMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
) -> None:
    """Template path succeeds with zero rows → row_count=0, no error."""
    draft = _success_draft()
    mock_extract.return_value = draft
    mock_val_params.return_value = draft
    mock_val_query.return_value = draft

    clients = _make_clients(
        template_results=[_TEMPLATE_DICT],
        sql_rows=[],
        sql_columns=_COLS,
    )
    request = NL2SQLRequest(user_query="Show orders from Atlantis")

    result = await process_query(request, clients)

    assert isinstance(result, NL2SQLResponse)
    assert result.row_count == 0
    assert result.error is None


# ── Scenario Math Helpers (T019) ─────────────────────────────────────────

_SCENARIO_BASELINE_ROWS: list[dict] = [
    {"StockGroupName": "Novelty Items", "Revenue": 1000.0},
    {"StockGroupName": "Packaging Materials", "Revenue": 2000.0},
    {"StockGroupName": "Novelty Items", "Revenue": 500.0},
    {"StockGroupName": "Clothing", "Revenue": 3000.0},
]


class TestAggregateBaseline:
    """T019: Verify baseline aggregation groups and sums correctly."""

    def test_groups_by_dimension_and_sums(self) -> None:
        """Rows with same dimension key are summed."""
        result = aggregate_baseline(
            _SCENARIO_BASELINE_ROWS,
            "Revenue",
            "StockGroupName",
        )
        assert result["Novelty Items"] == 1500.0
        assert result["Packaging Materials"] == 2000.0
        assert result["Clothing"] == 3000.0

    def test_single_dimension_value(self) -> None:
        """Single unique dimension returns one entry."""
        rows = [
            {"Dim": "X", "Val": 10.0},
            {"Dim": "X", "Val": 20.0},
        ]
        result = aggregate_baseline(rows, "Val", "Dim")
        assert result == {"X": 30.0}

    def test_empty_rows_returns_empty_dict(self) -> None:
        """No rows → empty aggregates."""
        result = aggregate_baseline([], "Revenue", "StockItemName")
        assert result == {}

    def test_missing_metric_key_defaults_to_zero(self) -> None:
        """Row without metric column contributes 0."""
        rows = [{"Dim": "A"}, {"Dim": "A", "Val": 5.0}]
        result = aggregate_baseline(rows, "Val", "Dim")
        assert result["A"] == 5.0


class TestComputeScenarioMetrics:
    """T019: Verify assumption transforms produce correct metrics."""

    def test_pct_delta_positive(self) -> None:
        """5% increase on known baselines."""
        aggregates = {"A": 1000.0, "B": 2000.0}
        metrics = compute_scenario_metrics(
            aggregates,
            "Revenue",
            pct_delta=5.0,
        )
        assert len(metrics) == 2
        m_a = next(m for m in metrics if m.dimension_key == "A")
        assert m_a.baseline == 1000.0
        assert m_a.scenario == 1050.0
        assert m_a.delta_abs == 50.0
        assert abs(m_a.delta_pct - 5.0) < 1e-6

    def test_pct_delta_negative(self) -> None:
        """-10% decrease."""
        aggregates = {"X": 500.0}
        metrics = compute_scenario_metrics(
            aggregates,
            "Revenue",
            pct_delta=-10.0,
        )
        m = metrics[0]
        assert m.scenario == 450.0
        assert m.delta_abs == -50.0
        assert abs(m.delta_pct - (-10.0)) < 1e-6

    def test_abs_delta(self) -> None:
        """Absolute increase of 100."""
        aggregates = {"Y": 800.0}
        metrics = compute_scenario_metrics(
            aggregates,
            "Cost",
            abs_delta=100.0,
        )
        m = metrics[0]
        assert m.scenario == 900.0
        assert m.delta_abs == 100.0

    def test_zero_baseline_delta_pct(self) -> None:
        """Baseline=0 returns fallback delta_pct=0.0."""
        aggregates = {"Z": 0.0}
        metrics = compute_scenario_metrics(
            aggregates,
            "Revenue",
            pct_delta=10.0,
        )
        m = metrics[0]
        assert m.baseline == 0.0
        assert m.scenario == 0.0
        assert m.delta_pct == 0.0

    def test_no_delta_returns_baseline_as_scenario(self) -> None:
        """No pct or abs delta → scenario equals baseline."""
        aggregates = {"Q": 42.0}
        metrics = compute_scenario_metrics(aggregates, "Units")
        assert metrics[0].scenario == 42.0
        assert metrics[0].delta_abs == 0.0


# ── Scenario Pipeline Shape (T019) ──────────────────────────────────────

_SCENARIO_INTENT = ScenarioIntent(
    mode="scenario",
    confidence=0.9,
    reason="what-if detected",
    detected_patterns=["what if"],
)


def _make_assumption_set(
    *,
    scenario_type: str = "price_delta",
    pct: float = 5.0,
    is_complete: bool = True,
) -> ScenarioAssumptionSet:
    """Build a ScenarioAssumptionSet with one pct assumption."""
    return ScenarioAssumptionSet(
        scenario_type=scenario_type,
        assumptions=[
            ScenarioAssumption(
                name="price_delta_pct",
                scope="global",
                value=pct,
                unit="pct",
                source="user",
            ),
        ],
        is_complete=is_complete,
    )


class TestScenarioPayloadShape:
    """T019: process_scenario_query produces correct result shape."""

    async def test_returns_scenario_result(self) -> None:
        """Response has is_scenario=True and scenario_result."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(),
            "what if prices increase 5%",
            clients,
        )
        assert isinstance(result, NL2SQLResponse)
        assert result.is_scenario is True
        assert result.scenario_type == "price_delta"
        assert result.scenario_result is not None
        assert isinstance(
            result.scenario_result,
            ScenarioComputationResult,
        )

    async def test_metrics_have_correct_count(self) -> None:
        """Metric count matches distinct dimension values."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(),
            "what if prices increase 5%",
            clients,
        )
        # 3 distinct StockGroupNames after aggregation
        assert result.scenario_result is not None
        assert len(result.scenario_result.metrics) == 3

    async def test_delta_computation_accuracy(self) -> None:
        """5% increase: Novelty Items baseline=1500 → scenario=1575."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=5.0),
            "what if prices increase 5%",
            clients,
        )
        assert result.scenario_result is not None
        m_a = next(m for m in result.scenario_result.metrics if m.dimension_key == "Novelty Items")
        assert m_a.baseline == 1500.0
        assert m_a.scenario == 1575.0
        assert m_a.delta_abs == 75.0
        assert abs(m_a.delta_pct - 5.0) < 1e-6

    async def test_summary_totals_present(self) -> None:
        """Summary totals contain baseline and scenario sums."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=10.0),
            "what if prices increase 10%",
            clients,
        )
        assert result.scenario_result is not None
        totals = result.scenario_result.summary_totals
        assert "total_revenue_baseline" in totals
        assert "total_revenue_scenario" in totals
        assert totals["total_revenue_baseline"] == 6500.0
        assert totals["total_revenue_scenario"] == 7150.0

    async def test_visualization_payload_present(self) -> None:
        """Response includes ScenarioVisualizationPayload."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(),
            "what if prices increase 5%",
            clients,
        )
        assert result.scenario_visualization is not None
        assert result.scenario_visualization.chart_type == "bar"
        assert len(result.scenario_visualization.series) >= 2
        assert len(result.scenario_visualization.rows) == 3

    async def test_fallback_pct_extraction_from_query(self) -> None:
        """When no assumptions provided, extract pct from query."""
        assumption_set = ScenarioAssumptionSet(
            scenario_type="price_delta",
            assumptions=[],
            missing_requirements=["extraction pending"],
            is_complete=False,
        )
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            assumption_set,
            "what if prices increase 15%",
            clients,
        )
        assert result.scenario_result is not None
        m_b = next(
            m for m in result.scenario_result.metrics if m.dimension_key == "Packaging Materials"
        )
        # 15% of 2000 = 300
        assert m_b.scenario == 2300.0

    async def test_unsupported_scenario_type_returns_error(
        self,
    ) -> None:
        """Unknown scenario_type → error response."""
        assumption_set = ScenarioAssumptionSet(
            scenario_type="unknown_type",
            assumptions=[],
            missing_requirements=["unknown"],
            is_complete=False,
        )
        clients = _make_clients()
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            assumption_set,
            "what if something",
            clients,
        )
        assert result.is_scenario is True
        assert result.error is not None
        assert "Unsupported" in result.error

    async def test_sql_failure_returns_error(self) -> None:
        """Baseline SQL execution failure → error response."""
        clients = _make_clients(sql_error="Connection refused")
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(),
            "what if prices increase 5%",
            clients,
        )
        assert result.is_scenario is True
        assert result.error is not None


# ── Sparse Signal Detection (T046) ──────────────────────────────────────


class TestSparseSignalDetection:
    """T046 [SC-009]: Sparse-signal and missing-signal handling."""

    def test_few_rows_produces_limitation(self) -> None:
        """Fewer than 2 groups → row-count limitation."""
        rows = [{"x": 1}]
        limitations = detect_sparse_signal(rows)
        assert len(limitations) == 1
        assert "1 group" in limitations[0]
        assert "2" in limitations[0]

    def test_sufficient_rows_no_limitation(self) -> None:
        """2+ groups → no row-count limitation."""
        rows = [{"x": i} for i in range(4)]
        limitations = detect_sparse_signal(rows)
        assert len(limitations) == 0

    def test_few_weekly_periods_produces_limitation(self) -> None:
        """Fewer than 8 weekly periods → weekly limitation."""
        # Only 5 distinct weeks
        base_dates = [
            "2025-01-06",
            "2025-01-13",
            "2025-01-20",
            "2025-01-27",
            "2025-02-03",
        ]
        rows = [{"OrderDate": d} for d in base_dates]
        limitations = detect_sparse_signal(rows, "OrderDate")
        assert any("weekly periods" in lim for lim in limitations)

    def test_sufficient_weekly_periods_no_limitation(self) -> None:
        """8+ weekly periods → no weekly limitation."""
        # Use dates spanning 10 distinct ISO weeks
        base_dates = [
            "2025-01-06",  # week 2
            "2025-01-13",  # week 3
            "2025-01-20",  # week 4
            "2025-01-27",  # week 5
            "2025-02-03",  # week 6
            "2025-02-10",  # week 7
            "2025-02-17",  # week 8
            "2025-02-24",  # week 9
            "2025-03-03",  # week 10
            "2025-03-10",  # week 11
        ]
        rows = [{"OrderDate": d} for d in base_dates]
        limitations = detect_sparse_signal(rows, "OrderDate")
        assert not any("weekly" in lim for lim in limitations)

    def test_both_sparse_conditions(self) -> None:
        """Single row AND few weeks → two limitations."""
        rows = [
            {"OrderDate": "2025-01-01"},
        ]
        limitations = detect_sparse_signal(rows, "OrderDate")
        assert len(limitations) == 2

    async def test_pipeline_populates_data_limitations(
        self,
    ) -> None:
        """process_scenario_query populates data_limitations."""
        # Only 1 row → sparse signal (needs ≥2 groups)
        sparse_rows = [
            {"StockGroupName": "A", "Revenue": 100.0},
        ]
        clients = _make_clients(
            sql_rows=sparse_rows,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(),
            "what if prices increase 5%",
            clients,
        )
        assert result.scenario_result is not None
        assert len(result.scenario_result.data_limitations) > 0
        assert any("group" in lim for lim in result.scenario_result.data_limitations)

    def test_no_date_column_skips_weekly_check(self) -> None:
        """Without date_column, only row-count is checked."""
        rows = [{"x": 1}]
        limitations = detect_sparse_signal(rows, date_column=None)
        assert len(limitations) == 1
        assert "group" in limitations[0]


# ── Narrative Summary Consistency (T030) ─────────────────────────────────


def _make_computation_result(
    *,
    metrics: list | None = None,
    data_limitations: list[str] | None = None,
    scenario_type: str = "price_delta",
) -> ScenarioComputationResult:
    """Build a ScenarioComputationResult for narrative tests."""
    if metrics is None:
        aggregates = {"Widget A": 1500.0, "Widget B": 2000.0, "Widget C": 3000.0}
        metrics = compute_scenario_metrics(aggregates, "Revenue", pct_delta=5.0)
    return ScenarioComputationResult(
        request_id="test-narrative",
        scenario_type=scenario_type,
        metrics=metrics,
        summary_totals={},
        data_limitations=data_limitations or [],
    )


class TestNarrativeConsistency:
    """T030: Narrative summary consistency against computed deltas."""

    def test_headline_reflects_dominant_direction_increase(self) -> None:
        """Headline mentions 'increases' for positive dominant delta."""
        result = _make_computation_result()
        narrative = build_narrative_summary(result)
        assert "increases" in narrative.headline

    def test_headline_reflects_dominant_direction_decrease(self) -> None:
        """Headline mentions 'decreases' for negative dominant delta."""
        aggregates = {"X": 1000.0}
        metrics = compute_scenario_metrics(aggregates, "Revenue", pct_delta=-10.0)
        result = _make_computation_result(metrics=metrics)
        narrative = build_narrative_summary(result)
        assert "decreases" in narrative.headline

    def test_key_changes_reference_metric_names_and_deltas(self) -> None:
        """Key change bullets cite actual metric name and delta values."""
        result = _make_computation_result()
        narrative = build_narrative_summary(result)
        for bullet in narrative.key_changes:
            assert "Revenue" in bullet
            assert "%" in bullet

    def test_key_changes_match_computation_values(self) -> None:
        """Numbers in narrative match ScenarioComputationResult exactly."""
        aggregates = {"Only": 2000.0}
        metrics = compute_scenario_metrics(aggregates, "Cost", pct_delta=12.5)
        result = _make_computation_result(metrics=metrics)
        narrative = build_narrative_summary(result)
        m = metrics[0]
        assert "12.5%" in narrative.headline
        assert f"{m.delta_abs:+,.2f}" in narrative.key_changes[0]

    def test_key_changes_limited_to_max(self) -> None:
        """At most MAX_KEY_CHANGES bullets are generated."""
        from shared.scenario_constants import MAX_KEY_CHANGES

        aggregates = {f"Item{i}": float(i * 100) for i in range(1, 10)}
        metrics = compute_scenario_metrics(aggregates, "Revenue", pct_delta=5.0)
        result = _make_computation_result(metrics=metrics)
        narrative = build_narrative_summary(result)
        assert len(narrative.key_changes) <= MAX_KEY_CHANGES

    def test_minimal_impact_headline_states_low_impact(self) -> None:
        """Near-zero deltas produce 'minimal impact' headline (T034)."""
        aggregates = {"A": 10000.0, "B": 20000.0}
        # 0.1% is below LOW_IMPACT_PCT_THRESHOLD (1%)
        metrics = compute_scenario_metrics(
            aggregates,
            "Revenue",
            pct_delta=0.1,
        )
        result = _make_computation_result(metrics=metrics)
        narrative = build_narrative_summary(result)
        assert "minimal impact" in narrative.headline.lower()

    def test_minimal_impact_key_changes_note_near_zero(self) -> None:
        """Near-zero changes are listed with their small values (T034)."""
        aggregates = {"A": 5000.0}
        metrics = compute_scenario_metrics(
            aggregates,
            "Revenue",
            pct_delta=0.05,
        )
        result = _make_computation_result(metrics=metrics)
        narrative = build_narrative_summary(result)
        assert any("near-zero" in c for c in narrative.key_changes)

    def test_confidence_note_not_duplicated_with_limitations(self) -> None:
        """confidence_note is None even when data_limitations exist.

        Data limitations are shown by the dedicated DataLimitations
        UI component, so the narrative should not duplicate them.
        """
        result = _make_computation_result(
            data_limitations=["Only 10 rows (minimum 30)"],
        )
        narrative = build_narrative_summary(result)
        assert narrative.confidence_note is None

    def test_confidence_note_absent_without_limitations(self) -> None:
        """confidence_note is None when no data_limitations."""
        result = _make_computation_result(data_limitations=[])
        narrative = build_narrative_summary(result)
        assert narrative.confidence_note is None

    def test_empty_metrics_produces_fallback(self) -> None:
        """Empty metrics list returns a safe fallback narrative."""
        result = _make_computation_result(metrics=[])
        narrative = build_narrative_summary(result)
        assert narrative.headline
        assert len(narrative.key_changes) >= 1

    async def test_pipeline_includes_narrative(self) -> None:
        """process_scenario_query populates scenario_narrative."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=10.0),
            "what if prices increase 10%",
            clients,
        )
        assert result.scenario_narrative is not None
        assert result.scenario_narrative.headline
        assert len(result.scenario_narrative.key_changes) >= 1
        # Numeric consistency: 10% should appear
        assert "10%" in result.scenario_narrative.headline


# ── Clarification Hints (T035) ──────────────────────────────────────────


class TestClarificationHints:
    """T035 [US4]: Clarification hints for missing assumptions."""

    async def test_incomplete_assumption_set_returns_clarification_hint(
        self,
    ) -> None:
        """When is_complete=False, response includes a clarification hint."""
        assumption_set = ScenarioAssumptionSet(
            scenario_type="price_delta",
            assumptions=[],
            missing_requirements=["price change percentage"],
            is_complete=False,
        )
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            assumption_set,
            "what if prices change",
            clients,
        )
        assert result.scenario_hints is not None
        assert len(result.scenario_hints) >= 1
        hint = result.scenario_hints[0]
        assert hint.kind == "clarification"

    async def test_clarification_hint_includes_example_phrasing(
        self,
    ) -> None:
        """FR-009: Clarification hint includes at least one example."""
        assumption_set = ScenarioAssumptionSet(
            scenario_type="price_delta",
            assumptions=[],
            missing_requirements=["price change percentage"],
            is_complete=False,
        )
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            assumption_set,
            "what if prices change",
            clients,
        )
        assert result.scenario_hints is not None
        hint = result.scenario_hints[0]
        assert len(hint.examples) >= 1

    async def test_clarification_hint_identifies_missing_details(
        self,
    ) -> None:
        """Hint message references the specific missing assumption."""
        assumption_set = ScenarioAssumptionSet(
            scenario_type="demand_delta",
            assumptions=[],
            missing_requirements=["demand change percentage"],
            is_complete=False,
        )
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            assumption_set,
            "what if demand changes",
            clients,
        )
        assert result.scenario_hints is not None
        hint = result.scenario_hints[0]
        assert "demand change percentage" in hint.message.lower()

    async def test_clarification_hint_message_is_human_readable(
        self,
    ) -> None:
        """Hint message is non-empty human-readable guidance."""
        assumption_set = ScenarioAssumptionSet(
            scenario_type="price_delta",
            assumptions=[],
            missing_requirements=["price change percentage"],
            is_complete=False,
        )
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            assumption_set,
            "what if prices change",
            clients,
        )
        assert result.scenario_hints is not None
        hint = result.scenario_hints[0]
        assert len(hint.message) > 20
        assert hint.message[0].isupper()

    async def test_complete_assumption_set_has_no_clarification_hint(
        self,
    ) -> None:
        """Complete assumptions produce no clarification hint."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=5.0),
            "what if prices increase 5%",
            clients,
        )
        if result.scenario_hints:
            assert not any(h.kind == "clarification" for h in result.scenario_hints)


# ── Discoverability Hints (T036) ────────────────────────────────────────


class TestDiscoverabilityHints:
    """T036 [US4]: Discoverability hints for supported scenario categories."""

    def test_discoverability_hint_lists_supported_types(self) -> None:
        """Discoverability hint includes all SUPPORTED_SCENARIO_TYPES."""
        from shared.scenario_constants import SUPPORTED_SCENARIO_TYPES
        from shared.scenario_hints import build_discoverability_hint

        hint = build_discoverability_hint()
        assert hint.kind == "discoverability"
        for st in SUPPORTED_SCENARIO_TYPES:
            assert st in hint.supported_types

    def test_discoverability_hint_includes_examples_per_category(
        self,
    ) -> None:
        """Each supported type has at least one example prompt."""
        from shared.scenario_hints import build_discoverability_hint

        hint = build_discoverability_hint()
        assert len(hint.examples) >= len(hint.supported_types)

    def test_discoverability_hint_message_explains_capabilities(
        self,
    ) -> None:
        """Message describes available scenario capabilities."""
        from shared.scenario_hints import build_discoverability_hint

        hint = build_discoverability_hint()
        assert len(hint.message) > 20
        assert "scenario" in hint.message.lower() or "what-if" in hint.message.lower()

    async def test_discovery_not_in_scenario_pipeline(
        self,
    ) -> None:
        """Discovery hints are emitted by the chat router, not the scenario pipeline.

        After moving to LLM-driven discovery, process_scenario_query no longer
        adds discoverability hints — it only adds clarification hints.
        """
        assumption_set = ScenarioAssumptionSet(
            scenario_type="price_delta",
            assumptions=[],
            missing_requirements=["scenario type"],
            is_complete=False,
        )
        intent = ScenarioIntent(
            mode="scenario",
            confidence=0.7,
            reason="discovery request",
            detected_patterns=["what-if options"],
        )
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            intent,
            assumption_set,
            "show me what-if options",
            clients,
        )
        # Pipeline still produces clarification hints for incomplete assumptions
        assert result.scenario_hints is not None
        has_clarification = any(h.kind == "clarification" for h in result.scenario_hints)
        assert has_clarification
        # Discoverability hints are NOT produced by the pipeline anymore
        has_disco = any(h.kind == "discoverability" for h in result.scenario_hints)
        assert not has_disco

    async def test_prompt_hints_serialize_to_valid_dicts(self) -> None:
        """R006: scenario_hints serialize via model_dump for SSE payload."""
        assumption_set = ScenarioAssumptionSet(
            scenario_type="price_delta",
            assumptions=[],
            missing_requirements=["price change percentage"],
            is_complete=False,
        )
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            assumption_set,
            "what if prices change",
            clients,
        )
        assert result.scenario_hints is not None
        assert len(result.scenario_hints) >= 1
        for hint in result.scenario_hints:
            assert isinstance(hint, PromptHint)
            dumped = hint.model_dump()
            assert dumped["kind"] in {"clarification", "discoverability", "drill_down"}
            assert isinstance(dumped["message"], str)
            assert isinstance(dumped["examples"], list)
            assert isinstance(dumped["supported_types"], list)


# ── Top-N Limiting & Drill-Down Hints ────────────────────────────────────


class TestTopNLimiting:
    """Scenario results are limited to MAX_SCENARIO_CHART_ITEMS groups."""

    async def test_many_groups_bucketed_as_other(self) -> None:
        """More than MAX_SCENARIO_CHART_ITEMS groups → 'Other' bucket."""
        from shared.scenario_constants import MAX_SCENARIO_CHART_ITEMS

        rows = [
            {"StockGroupName": f"Group{i}", "Revenue": float(100 * (20 - i))} for i in range(15)
        ]
        clients = _make_clients(
            sql_rows=rows,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=10.0),
            "what if prices increase 10%",
            clients,
        )
        assert result.scenario_result is not None
        assert len(result.scenario_result.metrics) == MAX_SCENARIO_CHART_ITEMS + 1
        other = next(m for m in result.scenario_result.metrics if m.dimension_key == "Other")
        assert other.baseline > 0

    async def test_few_groups_no_other_bucket(self) -> None:
        """Fewer than MAX_SCENARIO_CHART_ITEMS groups → no bucketing."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=5.0),
            "what if prices increase 5%",
            clients,
        )
        assert result.scenario_result is not None
        assert not any(m.dimension_key == "Other" for m in result.scenario_result.metrics)


class TestDrillDownHints:
    """Drill-down hints suggest group-level exploration."""

    async def test_successful_scenario_includes_drill_down_hint(self) -> None:
        """Complete scenario result includes a drill_down hint."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=10.0),
            "what if prices increase 10%",
            clients,
        )
        assert result.scenario_hints is not None
        drill_hints = [h for h in result.scenario_hints if h.kind == "drill_down"]
        assert len(drill_hints) == 1

    async def test_drill_down_hint_examples_reference_groups(self) -> None:
        """Drill-down examples mention actual group names."""
        clients = _make_clients(
            sql_rows=_SCENARIO_BASELINE_ROWS,
            sql_columns=["StockGroupName", "Revenue"],
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=10.0),
            "what if prices increase 10%",
            clients,
        )
        assert result.scenario_hints is not None
        drill_hint = next(h for h in result.scenario_hints if h.kind == "drill_down")
        assert len(drill_hint.examples) >= 1
        all_examples = " ".join(drill_hint.examples)
        # At least one group name should appear
        assert any(
            name in all_examples for name in ["Clothing", "Packaging Materials", "Novelty Items"]
        )

    def test_build_drill_down_hints_limits_examples(self) -> None:
        """build_drill_down_hints caps examples at 5."""
        from shared.scenario_hints import build_drill_down_hints

        groups = [f"Group{i}" for i in range(20)]
        hint = build_drill_down_hints(groups, "price_delta", 10.0)
        assert hint.kind == "drill_down"
        assert len(hint.examples) == 5

    async def test_scoped_query_runs_item_level_drilldown(self) -> None:
        """Drill-down query runs item-level SQL and shows individual items."""
        item_rows = [
            {"StockItemName": "Hoodie (Black) S", "Revenue": 1500.0},
            {"StockItemName": "Hoodie (Blue) M", "Revenue": 1000.0},
            {"StockItemName": "T-Shirt (White) L", "Revenue": 500.0},
        ]
        parent_response = {
            "success": True,
            "columns": ["StockGroupName", "Revenue"],
            "rows": _SCENARIO_BASELINE_ROWS,
            "row_count": len(_SCENARIO_BASELINE_ROWS),
        }
        drilldown_response = {
            "success": True,
            "columns": ["StockItemName", "Revenue"],
            "rows": item_rows,
            "row_count": len(item_rows),
        }
        executor = SequentialFakeSqlExecutor([parent_response, drilldown_response])
        clients = PipelineClients(
            param_extractor_agent=MagicMock(),
            query_builder_agent=MagicMock(),
            template_search=FakeTemplateSearch(results=[]),
            table_search=FakeTableSearch(tables=[]),
            sql_executor=executor,
            reporter=SpyReporter(),
            allowed_tables=_ALLOWED,
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=10.0),
            "What if we change prices by +10% for the Clothing group?",
            clients,
        )
        assert result.scenario_result is not None
        # Should show individual items, not the parent group
        item_names = {m.dimension_key for m in result.scenario_result.metrics}
        assert "Hoodie (Black) S" in item_names
        assert "Clothing" not in item_names
        # Second SQL call should have bind parameter
        assert len(executor.calls) == 2
        assert executor.calls[1][1] == ["Clothing"]

    async def test_scoped_query_omits_drill_down_hints(self) -> None:
        """Scoped (drill-down) queries do not produce further drill-down hints."""
        item_rows = [
            {"StockItemName": "USB missile  (Green)", "Revenue": 800.0},
        ]
        parent_response = {
            "success": True,
            "columns": ["StockGroupName", "Revenue"],
            "rows": _SCENARIO_BASELINE_ROWS,
            "row_count": len(_SCENARIO_BASELINE_ROWS),
        }
        drilldown_response = {
            "success": True,
            "columns": ["StockItemName", "Revenue"],
            "rows": item_rows,
            "row_count": len(item_rows),
        }
        executor = SequentialFakeSqlExecutor([parent_response, drilldown_response])
        clients = PipelineClients(
            param_extractor_agent=MagicMock(),
            query_builder_agent=MagicMock(),
            template_search=FakeTemplateSearch(results=[]),
            table_search=FakeTableSearch(tables=[]),
            sql_executor=executor,
            reporter=SpyReporter(),
            allowed_tables=_ALLOWED,
        )
        result = await process_scenario_query(
            _SCENARIO_INTENT,
            _make_assumption_set(pct=10.0),
            "What if we change prices by +10% for the Novelty Items group?",
            clients,
        )
        assert result.scenario_hints is None or not any(
            h.kind == "drill_down" for h in result.scenario_hints
        )


class TestDetectGroupScope:
    """Unit tests for _detect_group_scope helper."""

    def test_matches_exact_group_name(self) -> None:
        from nl2sql_controller.pipeline import _detect_group_scope

        groups = ["Clothing", "Novelty Items", "Toys"]
        assert _detect_group_scope("prices by +10% for the Clothing group", groups) == "Clothing"

    def test_case_insensitive_match(self) -> None:
        from nl2sql_controller.pipeline import _detect_group_scope

        groups = ["Clothing", "Novelty Items"]
        assert _detect_group_scope("what about NOVELTY ITEMS?", groups) == "Novelty Items"

    def test_longest_match_wins(self) -> None:
        from nl2sql_controller.pipeline import _detect_group_scope

        groups = ["Packaging", "Packaging Supplier"]
        result = _detect_group_scope("for Packaging Supplier items", groups)
        assert result == "Packaging Supplier"

    def test_no_match_returns_none(self) -> None:
        from nl2sql_controller.pipeline import _detect_group_scope

        groups = ["Clothing", "Toys"]
        assert _detect_group_scope("what about electronics?", groups) is None
