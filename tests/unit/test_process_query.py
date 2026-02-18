"""Integration tests for ``process_query()`` pipeline.

All tests use injected fakes — no Azure credentials, no network,
no filesystem access.  LLM-dependent functions (``extract_parameters``,
``build_query``) are mocked; pure validators may be mocked to test
specific routing branches.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from entities.nl2sql_controller.pipeline import process_query
from entities.workflow.clients import PipelineClients
from models import (
    ClarificationRequest,
    NL2SQLRequest,
    NL2SQLResponse,
    ParameterDefinition,
    SQLDraft,
)

_MOD = "entities.nl2sql_controller.pipeline"

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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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
    assert "Kaboom" in result.error


# ── 14. SQL Execution Failure ────────────────────────────────────────────


@patch(f"{_MOD}.AgentThread")
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
@patch(f"{_MOD}.AgentThread")
@patch(f"{_MOD}.validate_query")
@patch(f"{_MOD}.build_query", new_callable=AsyncMock)
async def test_dynamic_path_calls_refine_columns(
    mock_build: AsyncMock,
    mock_val_query: MagicMock,
    _mock_thread: MagicMock,
    mock_refine: MagicMock,
) -> None:
    """Dynamic queries pass through refine_columns for column display."""
    from entities.shared.column_filter import ColumnRefinementResult

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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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


@patch(f"{_MOD}.AgentThread")
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
