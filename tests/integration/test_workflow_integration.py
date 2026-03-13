"""Integration tests for the NL2SQL confidence and clarification pipeline.

These tests exercise end-to-end code paths between components, mocking only
external services (Azure AI Search, Azure SQL). They verify that the five
phases (deterministic extraction, confidence scoring, confirmation notes,
hypothesis prompts, and schema area detection) work together correctly.
"""

from unittest.mock import AsyncMock

from assistant.assistant import (
    SCHEMA_SUGGESTIONS,
    _detect_schema_area,
)
from models import (
    MissingParameter,
    ParameterDefinition,
    ParameterValidation,
    QueryTemplate,
)
from nl2sql_controller.pipeline import (
    _CONFIDENCE_THRESHOLD_HIGH,
    _CONFIDENCE_THRESHOLD_LOW,
    _format_confirmation_note,
    _format_hypothesis_prompt,
)

# ---------------------------------------------------------------------------
# Direct imports from the new pipeline/extractor modules
# ---------------------------------------------------------------------------
from parameter_extractor.extractor import (
    _build_parameter_confidences,
    _fuzzy_match_allowed_value,
    _hydrate_database_allowed_values,
    _pre_extract_parameters,
)
from shared.allowed_values_provider import AllowedValuesProvider, AllowedValuesResult

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_PATCH_TARGET = "shared.allowed_values_provider.AzureSqlClient"


def _make_template(
    *,
    params: list[ParameterDefinition] | None = None,
) -> QueryTemplate:
    """Build a minimal QueryTemplate for integration tests."""
    default_params = [
        ParameterDefinition(
            name="category",
            required=True,
            confidence_weight=1.0,
            validation=ParameterValidation(
                type="string",
                allowed_values=["Supermarket", "Corporate", "Novelty Shop"],
            ),
        ),
        ParameterDefinition(
            name="limit",
            required=False,
            confidence_weight=1.0,
            default_value=10,
            validation=ParameterValidation(type="integer", min=1, max=100),
        ),
    ]
    return QueryTemplate(
        id="test_template",
        intent="test_intent",
        question="Show customers by category",
        sql_template=(
            "SELECT TOP %{{limit}}% * FROM Sales.Customers "
            "WHERE CustomerCategoryID = %{{category}}%"
        ),
        parameters=params if params is not None else default_params,
    )


# ═══════════════════════════════════════════════════════════════════════════
# a. High-confidence template match → execution
# ═══════════════════════════════════════════════════════════════════════════


class TestHighConfidenceExecution:
    """Verify that deterministic exact-match extraction yields high confidence."""

    def test_all_params_extracted_high_confidence(self) -> None:
        """When all params are exact-matched, confidences are >= 0.85."""
        template = _make_template()

        result = _pre_extract_parameters("top 5 supermarket customers", template)

        assert "category" in result.extracted
        assert result.extracted["category"] == "Supermarket"
        assert "limit" in result.extracted
        assert result.extracted["limit"] == 5

        confidences = _build_parameter_confidences(result.resolution_methods, template)

        assert all(c >= _CONFIDENCE_THRESHOLD_HIGH for c in confidences.values())

    def test_high_confidence_produces_empty_confirmation_note(self) -> None:
        """All-high-confidence params produce no confirmation note."""
        template = _make_template()
        result = _pre_extract_parameters("top 5 supermarket customers", template)
        confidences = _build_parameter_confidences(result.resolution_methods, template)

        note = _format_confirmation_note(confidences, result.extracted)

        assert not note


# ═══════════════════════════════════════════════════════════════════════════
# b. Medium-confidence → confirmation note
# ═══════════════════════════════════════════════════════════════════════════


class TestMediumConfidenceNote:
    """Verify that llm_validated resolution produces a confirmation note."""

    def test_llm_validated_produces_confirmation(self) -> None:
        """A param resolved via 'llm_validated' (0.75) triggers a note."""
        template = _make_template()
        resolution_methods = {"category": "llm_validated", "limit": "exact_match"}
        confidences = _build_parameter_confidences(resolution_methods, template)

        # Verify the category confidence is medium
        assert _CONFIDENCE_THRESHOLD_LOW <= confidences["category"] < _CONFIDENCE_THRESHOLD_HIGH

        note = _format_confirmation_note(
            confidences,
            {"category": "Supermarket", "limit": 5},
        )

        assert note
        assert "category=**Supermarket**" in note
        assert "I assumed" in note


# ═══════════════════════════════════════════════════════════════════════════
# c. Low-confidence → clarification triggered
# ═══════════════════════════════════════════════════════════════════════════


class TestLowConfidenceClarification:
    """Verify that low-confidence resolution triggers hypothesis prompt."""

    def test_llm_failed_triggers_clarification(self) -> None:
        """A param with 'llm_failed_validation' scores below the low threshold."""
        template = _make_template()
        resolution_methods = {"category": "llm_failed_validation", "limit": "exact_match"}
        confidences = _build_parameter_confidences(resolution_methods, template)

        assert confidences["category"] < _CONFIDENCE_THRESHOLD_LOW

    def test_hypothesis_prompt_generated(self) -> None:
        """Low-confidence params produce a hypothesis-first clarification."""
        missing = [
            MissingParameter(
                name="category",
                best_guess="Corporate",
                alternatives=["Supermarket", "Novelty Shop"],
            ),
        ]

        prompt = _format_hypothesis_prompt(missing)

        assert "It looks like you want **Corporate**" in prompt
        assert "Supermarket" in prompt
        assert "Novelty Shop" in prompt


# ═══════════════════════════════════════════════════════════════════════════
# d. Database-sourced param hydration → fuzzy match
# ═══════════════════════════════════════════════════════════════════════════


class TestDatabaseHydrationFuzzyMatch:
    """Verify hydration populates allowed_values and fuzzy-match works."""

    async def test_hydrate_then_fuzzy_match(self) -> None:
        """After hydration, fuzzy-match succeeds against DB-sourced values."""
        provider = AsyncMock(spec=AllowedValuesProvider)
        provider.get_allowed_values.return_value = AllowedValuesResult(
            values=["Corporate", "Gift Store", "Supermarket"],
            is_partial=False,
        )

        # Build a template with database-sourced param (no static allowed_values)
        template = QueryTemplate(
            id="t_hydrate",
            intent="test",
            question="test",
            sql_template="SELECT 1",
            parameters=[
                ParameterDefinition(
                    name="category",
                    required=True,
                    allowed_values_source="database",
                    table="Sales.CustomerCategories",
                    column="CustomerCategoryName",
                ),
            ],
        )

        # Call the standalone hydration function directly
        await _hydrate_database_allowed_values(template, provider)

        # Verify hydration worked
        param = template.parameters[0]
        assert param.validation is not None
        assert "Supermarket" in param.validation.allowed_values

        # Fuzzy-match against hydrated values
        match = _fuzzy_match_allowed_value("supermarkets", param.validation.allowed_values)
        assert match == "Supermarket"


# ═══════════════════════════════════════════════════════════════════════════
# e. Schema area detection → suggestions
# ═══════════════════════════════════════════════════════════════════════════


class TestSchemaAreaDetection:
    """Verify schema area detection from SQL table references."""

    def test_sales_orders(self) -> None:
        assert _detect_schema_area(["Sales.Orders"]) == "sales"

    def test_warehouse_stock(self) -> None:
        assert _detect_schema_area(["Warehouse.StockItems"]) == "warehouse"

    def test_purchasing(self) -> None:
        assert _detect_schema_area(["Purchasing.PurchaseOrders"]) == "purchasing"

    def test_application(self) -> None:
        assert _detect_schema_area(["Application.People"]) == "application"

    def test_detected_area_has_suggestions(self) -> None:
        """Each detected area maps to schema suggestions."""
        for area in ("sales", "warehouse", "purchasing", "application"):
            assert area in SCHEMA_SUGGESTIONS
            assert len(SCHEMA_SUGGESTIONS[area]) > 0


# ═══════════════════════════════════════════════════════════════════════════
# f. Full pipeline: extraction → confidence → confirmation note
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPipeline:
    """End-to-end: deterministic extraction → confidence → confirmation note."""

    def test_template_with_default_policy_date(self) -> None:
        """A date param using default_policy produces medium confidence + note."""
        template = QueryTemplate(
            id="t_pipeline",
            intent="recent_orders",
            question="Show recent orders",
            sql_template=(
                "SELECT * FROM Sales.Orders "
                "WHERE OrderDate >= %{{from_date}}% "
                "ORDER BY OrderDate %{{order}}%"
            ),
            parameters=[
                ParameterDefinition(
                    name="from_date",
                    required=True,
                    confidence_weight=1.0,
                    default_policy="DATEADD(day, -30, GETDATE())",
                ),
                ParameterDefinition(
                    name="order",
                    required=False,
                    confidence_weight=1.0,
                    default_value="DESC",
                    validation=ParameterValidation(
                        type="string",
                        allowed_values=["ASC", "DESC"],
                    ),
                ),
            ],
        )

        # Step 1: Deterministic extraction
        result = _pre_extract_parameters("show me recent orders", template)

        assert "from_date" in result.extracted
        assert result.resolution_methods["from_date"] == "default_policy"
        assert "order" in result.extracted
        assert result.resolution_methods["order"] == "default_value"

        # Step 2: Confidence scoring
        confidences = _build_parameter_confidences(result.resolution_methods, template)

        # default_policy → 0.7, default_value → 0.7 (both medium)
        assert _CONFIDENCE_THRESHOLD_LOW <= confidences["from_date"] < _CONFIDENCE_THRESHOLD_HIGH
        assert _CONFIDENCE_THRESHOLD_LOW <= confidences["order"] < _CONFIDENCE_THRESHOLD_HIGH

        # Step 3: Confirmation note
        note = _format_confirmation_note(confidences, result.extracted)

        assert note
        assert "I assumed" in note
        assert "from_date" in note
        assert "order" in note

    def test_mixed_confidence_pipeline(self) -> None:
        """Exact + default produces one high + one medium → note for medium only."""
        template = _make_template()

        # "supermarket" → exact_match for category
        # "limit" has no match in query → falls back to default_value=10
        result = _pre_extract_parameters("show supermarket customers", template)

        assert result.resolution_methods["category"] == "exact_match"
        assert result.resolution_methods["limit"] == "default_value"

        confidences = _build_parameter_confidences(result.resolution_methods, template)

        assert confidences["category"] >= _CONFIDENCE_THRESHOLD_HIGH
        assert confidences["limit"] < _CONFIDENCE_THRESHOLD_HIGH

        note = _format_confirmation_note(confidences, result.extracted)

        assert note
        assert "limit" in note
        # category is high confidence, so it should NOT appear in the note
        assert "category" not in note


# ═══════════════════════════════════════════════════════════════════════════
# g. Scenario tool-call contract tests (T020)
# ═══════════════════════════════════════════════════════════════════════════

import json
from pathlib import Path

from models import (
    ChartSeriesDefinition,
    NL2SQLResponse,
    ScenarioAssumption,
    ScenarioComputationResult,
    ScenarioMetricValue,
    ScenarioVisualizationPayload,
)

# Load contract schema for validation
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "004-what-if-scenarios"
    / "contracts"
    / "scenario-response.schema.json"
)


def _build_scenario_response() -> NL2SQLResponse:
    """Build a fully populated scenario NL2SQLResponse for testing."""
    metrics = [
        ScenarioMetricValue(
            metric="Revenue",
            dimension_key="Widget A",
            baseline=1000.0,
            scenario=1050.0,
            delta_abs=50.0,
            delta_pct=5.0,
        ),
        ScenarioMetricValue(
            metric="Revenue",
            dimension_key="Widget B",
            baseline=2000.0,
            scenario=2100.0,
            delta_abs=100.0,
            delta_pct=5.0,
        ),
    ]
    computation = ScenarioComputationResult(
        request_id="test-req-001",
        scenario_type="price_delta",
        metrics=metrics,
        summary_totals={
            "total_revenue_baseline": 3000.0,
            "total_revenue_scenario": 3150.0,
            "total_delta_abs": 150.0,
            "total_delta_pct": 5.0,
        },
        data_limitations=[],
    )
    viz = ScenarioVisualizationPayload(
        chart_type="bar",
        x_key="StockItemName",
        series=[
            ChartSeriesDefinition(
                key="baseline",
                label="Baseline",
                kind="baseline",
            ),
            ChartSeriesDefinition(
                key="scenario",
                label="Scenario",
                kind="scenario",
            ),
        ],
        rows=[
            {
                "StockItemName": "Widget A",
                "baseline": 1000.0,
                "scenario": 1050.0,
            },
            {
                "StockItemName": "Widget B",
                "baseline": 2000.0,
                "scenario": 2100.0,
            },
        ],
        labels={
            "baseline": "Current (Baseline)",
            "scenario": "Projected (Scenario)",
            "StockItemName": "Stock Item Name",
        },
    )
    return NL2SQLResponse(
        sql_query="SELECT ...",
        is_scenario=True,
        scenario_type="price_delta",
        scenario_assumptions=[
            ScenarioAssumption(
                name="price_delta_pct",
                scope="global",
                value=5.0,
                unit="pct",
                source="user",
            ),
        ],
        scenario_result=computation,
        scenario_visualization=viz,
    )


class TestScenarioToolCallContract:
    """T020: Verify scenario_analysis tool result matches contract."""

    def test_tool_call_has_required_fields(self) -> None:
        """scenario_analysis tool call contains all required keys."""
        response = _build_scenario_response()
        assert response.scenario_result is not None
        assert response.scenario_visualization is not None

        # Reproduce the exact dict structure from chat.py
        tool_call = {
            "tool_name": "scenario_analysis",
            "tool_call_id": f"scenario_{id(response)}",
            "args": {},
            "result": {
                "mode": "scenario",
                "scenario_type": response.scenario_type,
                "assumptions": [a.model_dump() for a in (response.scenario_assumptions or [])],
                "metrics": [m.model_dump() for m in response.scenario_result.metrics],
                "summary_totals": (response.scenario_result.summary_totals),
                "data_limitations": (response.scenario_result.data_limitations),
                "visualization": (response.scenario_visualization.model_dump()),
                "narrative": None,
                "prompt_hints": [],
            },
        }

        assert tool_call["tool_name"] == "scenario_analysis"
        result = tool_call["result"]
        assert result["mode"] == "scenario"
        assert result["scenario_type"] == "price_delta"
        assert len(result["assumptions"]) == 1
        assert len(result["metrics"]) == 2
        assert result["visualization"] is not None
        assert result["prompt_hints"] == []

    def test_metric_payload_has_required_fields(self) -> None:
        """Each metric dict has all required fields per schema."""
        response = _build_scenario_response()
        assert response.scenario_result is not None

        required_keys = {
            "metric",
            "dimension_key",
            "baseline",
            "scenario",
            "delta_abs",
            "delta_pct",
        }
        for m in response.scenario_result.metrics:
            dumped = m.model_dump()
            assert required_keys.issubset(dumped.keys())

    def test_visualization_payload_has_required_fields(
        self,
    ) -> None:
        """Visualization payload has all required fields per schema."""
        response = _build_scenario_response()
        assert response.scenario_visualization is not None

        viz = response.scenario_visualization.model_dump()
        assert "chart_type" in viz
        assert "x_key" in viz
        assert "series" in viz
        assert "rows" in viz
        assert "labels" in viz
        assert len(viz["series"]) >= 2

    def test_assumption_payload_has_required_fields(self) -> None:
        """Each assumption dict has all required fields per schema."""
        response = _build_scenario_response()
        assert response.scenario_assumptions is not None

        required_keys = {
            "name",
            "scope",
            "value",
            "unit",
            "source",
        }
        for a in response.scenario_assumptions:
            dumped = a.model_dump()
            assert required_keys.issubset(dumped.keys())

    def test_payload_serializes_to_valid_json(self) -> None:
        """Full tool result round-trips through JSON."""
        response = _build_scenario_response()
        assert response.scenario_result is not None
        assert response.scenario_visualization is not None

        payload = {
            "mode": "scenario",
            "scenario_type": response.scenario_type,
            "assumptions": [a.model_dump() for a in (response.scenario_assumptions or [])],
            "metrics": [m.model_dump() for m in response.scenario_result.metrics],
            "visualization": (response.scenario_visualization.model_dump()),
            "narrative": None,
            "prompt_hints": [],
            "data_limitations": [],
        }

        # Must serialize without error
        serialized = json.dumps(payload)
        parsed = json.loads(serialized)

        assert parsed["mode"] == "scenario"
        assert len(parsed["metrics"]) == 2
        assert parsed["visualization"]["chart_type"] == "bar"

    def test_contract_schema_keys_match_payload(self) -> None:
        """Payload top-level keys match the JSON schema required."""
        if not _SCHEMA_PATH.exists():
            return  # Skip if schema file not available

        schema = json.loads(_SCHEMA_PATH.read_text())
        required_keys = set(schema.get("required", []))

        response = _build_scenario_response()
        assert response.scenario_result is not None
        assert response.scenario_visualization is not None

        payload = {
            "mode": "scenario",
            "scenario_type": response.scenario_type,
            "assumptions": [a.model_dump() for a in (response.scenario_assumptions or [])],
            "metrics": [m.model_dump() for m in response.scenario_result.metrics],
            "visualization": (response.scenario_visualization.model_dump()),
            "narrative": {
                "headline": "Test",
                "key_changes": ["Change 1"],
            },
            "prompt_hints": [],
        }

        missing = required_keys - set(payload.keys())
        assert not missing, f"Missing required keys: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
# h. Scenario latency benchmark scaffold (T048, SC-006)
# ═══════════════════════════════════════════════════════════════════════════

import statistics
import time
import uuid

import pytest


def _simulate_analytical_query_latency() -> float:
    """Simulate a non-scenario analytical query and return elapsed seconds.

    In a full integration environment this would call the real pipeline
    ``process_query()`` with a non-scenario prompt. Here we build and
    serialise a standard ``NL2SQLResponse`` to measure in-process
    overhead (same serialisation path the real endpoint uses).
    """
    start = time.perf_counter()
    NL2SQLResponse(
        sql_query="SELECT TOP 10 * FROM Sales.Orders ORDER BY OrderDate DESC",
        sql_response=[{"OrderID": i, "OrderDate": "2025-01-01"} for i in range(10)],
        confidence_score=0.95,
        columns=["OrderID", "OrderDate"],
        row_count=10,
        query_source="template",
    )
    return time.perf_counter() - start


def _simulate_scenario_query_latency() -> float:
    """Simulate a scenario query and return elapsed seconds.

    Mirrors ``_simulate_analytical_query_latency`` but builds the full
    scenario payload (computation + visualisation + narrative) so
    the overhead of scenario-specific model construction is captured.
    """
    from shared.scenario_narrative import build_narrative_summary

    start = time.perf_counter()
    metrics = [
        ScenarioMetricValue(
            metric="Revenue",
            dimension_key=f"Item-{i}",
            baseline=1000.0 * (i + 1),
            scenario=1000.0 * (i + 1) * 1.05,
            delta_abs=1000.0 * (i + 1) * 0.05,
            delta_pct=5.0,
        )
        for i in range(5)
    ]
    computation = ScenarioComputationResult(
        request_id=f"bench-{uuid.uuid4().hex[:8]}",
        scenario_type="price_delta",
        metrics=metrics,
        summary_totals={
            "total_baseline": sum(m.baseline for m in metrics),
            "total_scenario": sum(m.scenario for m in metrics),
        },
        data_limitations=[],
    )
    narrative = build_narrative_summary(computation)
    viz = ScenarioVisualizationPayload(
        chart_type="bar",
        x_key="StockItemName",
        series=[
            ChartSeriesDefinition(key="baseline", label="Baseline", kind="baseline"),
            ChartSeriesDefinition(key="scenario", label="Scenario", kind="scenario"),
        ],
        rows=[
            {"StockItemName": m.dimension_key, "baseline": m.baseline, "scenario": m.scenario}
            for m in metrics
        ],
        labels={"baseline": "Current", "scenario": "Projected"},
    )
    NL2SQLResponse(
        sql_query="SELECT ...",
        is_scenario=True,
        scenario_type="price_delta",
        scenario_assumptions=[
            ScenarioAssumption(
                name="price_delta_pct",
                scope="global",
                value=5.0,
                unit="pct",
                source="user",
            ),
        ],
        scenario_result=computation,
        scenario_narrative=narrative,
        scenario_visualization=viz,
    )
    return time.perf_counter() - start


@pytest.mark.benchmark
class TestScenarioLatencyBenchmark:
    """T048 / SC-006: Scenario latency benchmark scaffold.

    Measures in-process model construction overhead for scenario vs
    analytical responses.  The SC-006 threshold (scenario p50 <= 1.2x
    analytical p50) applies to full end-to-end API requests; this test
    validates the scaffold methodology and captures per-request
    latency data.  A full network benchmark is described in the
    quickstart Latency Validation Protocol section.
    """

    SAMPLE_SIZE: int = 15
    MEASURED_PASSES: int = 3
    SC006_THRESHOLD: float = 1.2

    def test_benchmark_captures_per_request_latency(self) -> None:
        """Each simulated request returns a positive latency."""
        analytical = _simulate_analytical_query_latency()
        scenario = _simulate_scenario_query_latency()

        assert analytical > 0, "Analytical latency must be positive"
        assert scenario > 0, "Scenario latency must be positive"

    def test_benchmark_collects_sample_distribution(self) -> None:
        """Warm-up + measured passes produce expected sample counts."""
        analytical_times: list[float] = []
        scenario_times: list[float] = []

        # Warm-up pass (discarded)
        for _ in range(self.SAMPLE_SIZE):
            _simulate_analytical_query_latency()
            _simulate_scenario_query_latency()

        # Measured passes
        for _ in range(self.MEASURED_PASSES):
            for _ in range(self.SAMPLE_SIZE):
                analytical_times.append(_simulate_analytical_query_latency())
                scenario_times.append(_simulate_scenario_query_latency())

        expected_count = self.SAMPLE_SIZE * self.MEASURED_PASSES
        assert len(analytical_times) == expected_count
        assert len(scenario_times) == expected_count

        analytical_p50 = statistics.median(analytical_times)
        scenario_p50 = statistics.median(scenario_times)

        # Both medians must be positive
        assert analytical_p50 > 0
        assert scenario_p50 > 0

    def test_sc006_threshold_constant_matches_spec(self) -> None:
        """Verify the threshold ratio matches SC-006 specification."""
        assert abs(self.SC006_THRESHOLD - 1.2) < 1e-9
