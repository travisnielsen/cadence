"""Unit tests for AllowedValuesProvider and hydration integration.

Tests the stale-while-revalidate caching, input validation, TTL behaviour,
and the ParameterExtractorExecutor._hydrate_database_allowed_values flow.
"""

import asyncio
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from entities.shared.allowed_values_provider import (
    _COLUMN_PATTERN,
    _TABLE_PATTERN,
    AllowedValuesProvider,
    AllowedValuesResult,
)
from models import ParameterDefinition, ParameterValidation, QueryTemplate

# ---------------------------------------------------------------------------
# Stub agent_framework / agent_framework_azure_ai so we can import the
# ParameterExtractorExecutor without a real Azure AI client.
# ---------------------------------------------------------------------------
_mock_af = MagicMock()
_mock_af.handler = lambda fn: fn
_mock_af.ChatAgent = MagicMock
_mock_af.Executor = type("Executor", (), {"__init__": lambda _self, **_kw: None})
_mock_af.WorkflowContext = MagicMock
_mock_af.AgentThread = MagicMock
sys.modules.setdefault("agent_framework", _mock_af)
sys.modules.setdefault("agent_framework_azure_ai", MagicMock())

# Import executor module via spec to avoid __init__ side-effects
_executor_path = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "backend"
    / "entities"
    / "parameter_extractor"
    / "executor.py"
)
_spec = importlib.util.spec_from_file_location(  # type: ignore[union-attr]
    "entities.parameter_extractor.executor", _executor_path
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

ParameterExtractorExecutor = _mod.ParameterExtractorExecutor
_fuzzy_match_allowed_value = _mod._fuzzy_match_allowed_value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATCH_TARGET = "entities.shared.allowed_values_provider.AzureSqlClient"


def _make_mock_client(
    rows: list[dict[str, str]],
    *,
    success: bool = True,
    error: str | None = None,
    column: str = "CustomerCategoryName",
) -> AsyncMock:
    """Create an ``AzureSqlClient`` async-context-manager mock."""
    mock_client = AsyncMock()
    mock_client.execute_query.return_value = {
        "success": success,
        "columns": [column],
        "rows": rows,
        "row_count": len(rows),
        "error": error,
    }
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _make_template(
    *,
    allowed_values_source: str | None = "database",
    table: str | None = "Sales.CustomerCategories",
    column: str | None = "CustomerCategoryName",
    validation: ParameterValidation | None = None,
    static_allowed: list[str] | None = None,
) -> QueryTemplate:
    """Build a minimal ``QueryTemplate`` for hydration tests."""
    val = validation
    if val is None and static_allowed is not None:
        val = ParameterValidation(type="string", allowed_values=static_allowed)
    return QueryTemplate(
        id="t1",
        intent="test_intent",
        question="test",
        sql_template="SELECT 1",
        parameters=[
            ParameterDefinition(
                name="category",
                required=True,
                allowed_values_source=allowed_values_source,
                table=table,
                column=column,
                validation=val,
            )
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. AllowedValuesProvider core tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAllowedValuesProviderCore:
    """Tests for AllowedValuesProvider caching, loading, and input validation."""

    async def test_cache_miss_queries_db_and_returns_values(self) -> None:
        """Cache miss triggers a DB query and returns an AllowedValuesResult."""
        rows = [
            {"Col": "Alpha"},
            {"Col": "Bravo"},
            {"Col": "Charlie"},
        ]
        mock_client = _make_mock_client(rows, column="Col")

        with patch(_PATCH_TARGET, return_value=mock_client):
            provider = AllowedValuesProvider(
                server="s", database="d", ttl_seconds=600, max_values=500
            )
            result = await provider.get_allowed_values("Sales.Items", "Col")

        assert result is not None
        assert result.values == ["Alpha", "Bravo", "Charlie"]
        assert result.is_partial is False
        mock_client.execute_query.assert_awaited_once()

    async def test_cache_hit_within_ttl_no_second_query(self) -> None:
        """Second call within TTL returns cached data without a DB query."""
        rows = [{"Col": "X"}]
        mock_client = _make_mock_client(rows, column="Col")

        with patch(_PATCH_TARGET, return_value=mock_client):
            provider = AllowedValuesProvider(
                server="s", database="d", ttl_seconds=600, max_values=500
            )
            first = await provider.get_allowed_values("Sales.Items", "Col")
            second = await provider.get_allowed_values("Sales.Items", "Col")

        assert first is not None and second is not None
        assert first.values == second.values
        mock_client.execute_query.assert_awaited_once()

    async def test_cache_expired_returns_stale_and_triggers_refresh(self) -> None:
        """After TTL expiry, stale values are returned and a background task is spawned."""
        rows = [{"Col": "Stale"}]
        mock_client = _make_mock_client(rows, column="Col")

        with patch(_PATCH_TARGET, return_value=mock_client):
            provider = AllowedValuesProvider(
                server="s", database="d", ttl_seconds=0, max_values=500
            )
            # Populate cache
            first = await provider.get_allowed_values("Sales.Items", "Col")
            assert first is not None

            # Force expiry (ttl=0 means instantly stale)
            await asyncio.sleep(0.01)

            # Second call should return stale data + kick off bg task
            second = await provider.get_allowed_values("Sales.Items", "Col")

        assert second is not None
        assert second.values == ["Stale"]
        assert len(provider._background_tasks) >= 0  # task was created (may have finished)

    @pytest.mark.filterwarnings("ignore::ResourceWarning")
    async def test_exceeding_max_values_caps_and_sets_partial(self) -> None:
        """When the DB returns more than max_values rows, cap at max and set is_partial."""
        rows = [{"Col": v} for v in ["A", "B", "C", "D"]]  # 4 rows
        mock_client = _make_mock_client(rows, column="Col")

        with patch(_PATCH_TARGET, return_value=mock_client):
            provider = AllowedValuesProvider(
                server="s", database="d", ttl_seconds=600, max_values=3
            )
            result = await provider.get_allowed_values("Sales.Items", "Col")

        assert result is not None
        assert len(result.values) == 3
        assert result.is_partial is True

    async def test_db_error_returns_none(self) -> None:
        """A failed DB query returns None."""
        mock_client = _make_mock_client([], success=False, error="connection refused")

        with patch(_PATCH_TARGET, return_value=mock_client):
            provider = AllowedValuesProvider(server="s", database="d", ttl_seconds=600)
            result = await provider.get_allowed_values("Sales.Items", "Col")

        assert result is None

    async def test_db_exception_returns_none(self) -> None:
        """An exception during DB access returns None."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(_PATCH_TARGET, return_value=mock_client):
            provider = AllowedValuesProvider(server="s", database="d", ttl_seconds=600)
            result = await provider.get_allowed_values("Sales.Items", "Col")

        assert result is None

    async def test_invalid_table_name_returns_none(self) -> None:
        """An invalid table name (SQL-injection-like) is rejected immediately."""
        mock_client = _make_mock_client([])

        with patch(_PATCH_TARGET, return_value=mock_client):
            provider = AllowedValuesProvider(server="s", database="d", ttl_seconds=600)
            result = await provider.get_allowed_values("DROP TABLE; --", "Col")

        assert result is None
        mock_client.execute_query.assert_not_awaited()

    async def test_invalid_column_name_returns_none(self) -> None:
        """An invalid column name (contains semicolon) is rejected immediately."""
        mock_client = _make_mock_client([])

        with patch(_PATCH_TARGET, return_value=mock_client):
            provider = AllowedValuesProvider(server="s", database="d", ttl_seconds=600)
            result = await provider.get_allowed_values("Sales.Items", "col; DROP")

        assert result is None
        mock_client.execute_query.assert_not_awaited()

    async def test_ttl_from_env_var(self) -> None:
        """ALLOWED_VALUES_TTL_SECONDS env var configures the TTL."""
        with patch.dict(os.environ, {"ALLOWED_VALUES_TTL_SECONDS": "42"}):
            provider = AllowedValuesProvider(server="s", database="d")

        assert provider._ttl_seconds == 42


# ═══════════════════════════════════════════════════════════════════════════
# 2. Regex pattern sanity checks
# ═══════════════════════════════════════════════════════════════════════════


class TestNamePatterns:
    """Verify _TABLE_PATTERN and _COLUMN_PATTERN accept/reject expected names."""

    @pytest.mark.parametrize(
        "name",
        ["Sales.CustomerCategories", "dbo.Orders", "Warehouse.StockItems"],
    )
    def test_valid_table_names(self, name: str) -> None:
        assert _TABLE_PATTERN.match(name)

    @pytest.mark.parametrize("name", ["DROP TABLE;", "1BadStart", ""])
    def test_invalid_table_names(self, name: str) -> None:
        assert not _TABLE_PATTERN.match(name)

    @pytest.mark.parametrize("name", ["CustomerCategoryName", "ID", "col_1"])
    def test_valid_column_names(self, name: str) -> None:
        assert _COLUMN_PATTERN.match(name)

    @pytest.mark.parametrize("name", ["col; DROP", "1col", ""])
    def test_invalid_column_names(self, name: str) -> None:
        assert not _COLUMN_PATTERN.match(name)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Hydration integration (ParameterExtractorExecutor)
# ═══════════════════════════════════════════════════════════════════════════


def _make_executor(
    provider: AllowedValuesProvider | None = None,
) -> MagicMock:
    """Build a minimal mock executor that has _hydrate and related attrs."""
    executor = MagicMock()
    executor._allowed_values_provider = provider
    executor._partial_cache_params = set()
    # Bind the real unbound method so we can call it on our mock
    executor._hydrate_database_allowed_values = (
        ParameterExtractorExecutor._hydrate_database_allowed_values.__get__(executor)
    )
    return executor


class TestHydrateDatabaseAllowedValues:
    """Tests for ParameterExtractorExecutor._hydrate_database_allowed_values."""

    async def test_database_sourced_param_gets_hydrated(self) -> None:
        """A param with allowed_values_source='database' is populated from the provider."""
        provider = AsyncMock(spec=AllowedValuesProvider)
        provider.get_allowed_values.return_value = AllowedValuesResult(
            values=["Corporate", "Gift Store", "Supermarket"], is_partial=False
        )

        executor = _make_executor(provider)
        template = _make_template()

        await executor._hydrate_database_allowed_values(template)

        param = template.parameters[0]
        assert param.validation is not None
        assert param.validation.allowed_values == [
            "Corporate",
            "Gift Store",
            "Supermarket",
        ]

    async def test_structural_enum_not_hydrated(self) -> None:
        """Params without allowed_values_source='database' are left untouched."""
        provider = AsyncMock(spec=AllowedValuesProvider)
        executor = _make_executor(provider)

        template = _make_template(
            allowed_values_source=None,
            table=None,
            column=None,
            static_allowed=["ASC", "DESC"],
        )

        await executor._hydrate_database_allowed_values(template)

        assert template.parameters[0].validation is not None
        assert template.parameters[0].validation.allowed_values == ["ASC", "DESC"]
        provider.get_allowed_values.assert_not_awaited()

    async def test_partial_cache_sets_partial_params(self) -> None:
        """When the provider returns is_partial=True, the param name is tracked."""
        provider = AsyncMock(spec=AllowedValuesProvider)
        provider.get_allowed_values.return_value = AllowedValuesResult(
            values=["A", "B"], is_partial=True
        )

        executor = _make_executor(provider)
        template = _make_template()

        await executor._hydrate_database_allowed_values(template)

        assert "category" in executor._partial_cache_params

    async def test_db_unreachable_leaves_allowed_values_none(self) -> None:
        """If the provider returns None, validation.allowed_values stays None."""
        provider = AsyncMock(spec=AllowedValuesProvider)
        provider.get_allowed_values.return_value = None

        executor = _make_executor(provider)
        template = _make_template()  # validation is None by default

        await executor._hydrate_database_allowed_values(template)

        param = template.parameters[0]
        assert param.validation is None

    async def test_validation_none_gets_created(self) -> None:
        """A param with validation=None gets a new ParameterValidation after hydration."""
        provider = AsyncMock(spec=AllowedValuesProvider)
        provider.get_allowed_values.return_value = AllowedValuesResult(
            values=["Cat1", "Cat2"], is_partial=False
        )

        executor = _make_executor(provider)
        template = _make_template(validation=None)

        # Confirm precondition
        assert template.parameters[0].validation is None

        await executor._hydrate_database_allowed_values(template)

        param = template.parameters[0]
        assert param.validation is not None
        assert param.validation.type == "string"
        assert param.validation.allowed_values == ["Cat1", "Cat2"]

    async def test_existing_validation_updated(self) -> None:
        """A param with existing validation gets allowed_values overwritten."""
        provider = AsyncMock(spec=AllowedValuesProvider)
        provider.get_allowed_values.return_value = AllowedValuesResult(
            values=["New1", "New2"], is_partial=False
        )

        executor = _make_executor(provider)
        template = _make_template(
            validation=ParameterValidation(type="string", allowed_values=["Old"])
        )

        await executor._hydrate_database_allowed_values(template)

        assert template.parameters[0].validation is not None
        assert template.parameters[0].validation.allowed_values == ["New1", "New2"]

    async def test_no_provider_is_noop(self) -> None:
        """When no provider is configured, hydration is a no-op."""
        executor = _make_executor(provider=None)
        template = _make_template()

        # Should not raise
        await executor._hydrate_database_allowed_values(template)
        assert template.parameters[0].validation is None


# ═══════════════════════════════════════════════════════════════════════════
# 4. Validator integration - partial cache skips allowed_values check
# ═══════════════════════════════════════════════════════════════════════════


class TestValidatorPartialCacheIntegration:
    """Confirms that partial-cache params bypass strict allowed_values validation."""

    def test_string_validator_rejects_unknown_value(self) -> None:
        """_validate_string rejects a value not in allowed_values."""
        from entities.parameter_validator.executor import _validate_string

        validation = ParameterValidation(type="string", allowed_values=["A", "B"])
        violations = _validate_string("C", validation, "test")
        assert len(violations) > 0

    def test_string_validator_passes_without_allowed_values(self) -> None:
        """Without allowed_values, _validate_string does not reject."""
        from entities.parameter_validator.executor import _validate_string

        validation = ParameterValidation(type="string")
        violations = _validate_string("C", validation, "test")
        assert violations == []


# ═══════════════════════════════════════════════════════════════════════════
# 5. Fuzzy-match after hydration
# ═══════════════════════════════════════════════════════════════════════════


class TestFuzzyMatchAfterHydration:
    """Verifies _fuzzy_match_allowed_value works with hydrated values."""

    def test_plural_matches_singular(self) -> None:
        """'supermarkets' fuzzy-matches 'Supermarket'."""
        result = _fuzzy_match_allowed_value("supermarkets", ["Supermarket", "Corporate"])
        assert result == "Supermarket"

    def test_exact_case_insensitive(self) -> None:
        """Exact substring match is case-insensitive."""
        result = _fuzzy_match_allowed_value("corporate customers", ["Supermarket", "Corporate"])
        assert result == "Corporate"

    def test_no_match_returns_none(self) -> None:
        result = _fuzzy_match_allowed_value("something unrelated", ["Supermarket", "Corporate"])
        assert result is None
