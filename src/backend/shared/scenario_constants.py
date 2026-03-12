"""Scenario feature constants for assumption-based what-if analysis.

Defines supported scenario types, default assumption ranges, threshold
constants, feature labels, and telemetry keys used across the scenario
workflow.
"""

from typing import Final

# ── Supported phase-1 scenario types ─────────────────────────────────────

SCENARIO_TYPE_PRICE: Final = "price_delta"
SCENARIO_TYPE_DEMAND: Final = "demand_delta"
SCENARIO_TYPE_SUPPLIER_COST: Final = "supplier_cost_delta"
SCENARIO_TYPE_INVENTORY_POLICY: Final = "inventory_policy_delta"

SUPPORTED_SCENARIO_TYPES: Final[list[str]] = [
    SCENARIO_TYPE_PRICE,
    SCENARIO_TYPE_DEMAND,
    SCENARIO_TYPE_SUPPLIER_COST,
    SCENARIO_TYPE_INVENTORY_POLICY,
]

# ── Assumption units ─────────────────────────────────────────────────────

ASSUMPTION_UNIT_PCT: Final = "pct"
ASSUMPTION_UNIT_ABSOLUTE: Final = "absolute"
ASSUMPTION_UNIT_DAYS: Final = "days"
ASSUMPTION_UNIT_COUNT: Final = "count"

SUPPORTED_ASSUMPTION_UNITS: Final[list[str]] = [
    ASSUMPTION_UNIT_PCT,
    ASSUMPTION_UNIT_ABSOLUTE,
    ASSUMPTION_UNIT_DAYS,
    ASSUMPTION_UNIT_COUNT,
]

# ── Assumption provenance ────────────────────────────────────────────────

ASSUMPTION_SOURCE_USER: Final = "user"
ASSUMPTION_SOURCE_DEFAULT: Final = "default"
ASSUMPTION_SOURCE_INFERRED: Final = "inferred"

# ── Default assumption ranges (min, max) per scenario type ───────────────
# Used for clamping and validation of user-supplied values.

DEFAULT_ASSUMPTION_RANGES: Final[dict[str, tuple[float, float]]] = {
    SCENARIO_TYPE_PRICE: (-50.0, 100.0),
    SCENARIO_TYPE_DEMAND: (-80.0, 200.0),
    SCENARIO_TYPE_SUPPLIER_COST: (-50.0, 100.0),
    SCENARIO_TYPE_INVENTORY_POLICY: (-90.0, 500.0),
}

# ── Supported scopes ────────────────────────────────────────────────────

SUPPORTED_SCOPES: Final[list[str]] = [
    "global",
    "category",
    "item",
    "supplier",
]

# ── Sparse-signal thresholds (FR-010) ────────────────────────────────────

MIN_BASELINE_ROWS: Final[int] = 2
"""Minimum baseline rows for scenario analysis.

Set to 2 because baseline queries aggregate by category (GROUP BY
StockGroupName / SupplierCategoryName), so 4-10 rows is normal.
At least 2 groups are needed for a meaningful comparison.
"""

MIN_DISTINCT_WEEKLY_PERIODS: Final[int] = 8
"""Minimum distinct weekly periods in the analysis window."""

# ── Confidence thresholds ────────────────────────────────────────────────

SCENARIO_ROUTING_CONFIDENCE_THRESHOLD: Final[float] = 0.6
"""Minimum confidence to route a prompt to scenario processing."""

# ── Chart types ──────────────────────────────────────────────────────────

CHART_TYPE_BAR: Final = "bar"
CHART_TYPE_LINE: Final = "line"
CHART_TYPE_COMBO: Final = "combo"

# ── Hint kinds ───────────────────────────────────────────────────────────

HINT_KIND_CLARIFICATION: Final = "clarification"
HINT_KIND_DISCOVERABILITY: Final = "discoverability"
HINT_KIND_DRILL_DOWN: Final = "drill_down"

# ── Chart aggregation ────────────────────────────────────────────────────

MAX_SCENARIO_CHART_ITEMS: Final[int] = 10
"""Maximum groups shown in a scenario chart; remainder bucketed as 'Other'."""

# ── Narrative constraints ────────────────────────────────────────────────

MAX_KEY_CHANGES: Final[int] = 3
"""Maximum number of key-change bullets in a narrative summary."""

# ── Zero-baseline fallback ───────────────────────────────────────────────

ZERO_BASELINE_DELTA_PCT_FALLBACK: Final[float] = 0.0
"""Percent-change value returned when baseline is zero."""

# ── Feature labels (T002) ────────────────────────────────────────────────

FEATURE_LABEL: Final = "what_if_scenario"
FEATURE_DISPLAY_NAME: Final = "What-If Scenario Analysis"

# ── Telemetry event names (FR-012) ───────────────────────────────────────

TELEMETRY_EVENT_SCENARIO_ROUTED: Final = "scenario.routed"
TELEMETRY_EVENT_SCENARIO_COMPLETED: Final = "scenario.completed"
TELEMETRY_EVENT_SCENARIO_FAILED: Final = "scenario.failed"
TELEMETRY_EVENT_SCENARIO_SPARSE_SIGNAL: Final = "scenario.sparse_signal"
TELEMETRY_EVENT_HINT_EMITTED: Final = "scenario.hint_emitted"

# ── Telemetry metric keys (FR-012) ───────────────────────────────────────

TELEMETRY_METRIC_ROUTING_CONFIDENCE: Final = "scenario.routing_confidence"
TELEMETRY_METRIC_COMPUTATION_DURATION_MS: Final = "scenario.computation_duration_ms"
TELEMETRY_METRIC_BASELINE_ROW_COUNT: Final = "scenario.baseline_row_count"
TELEMETRY_METRIC_ASSUMPTION_COUNT: Final = "scenario.assumption_count"
TELEMETRY_METRIC_METRIC_COUNT: Final = "scenario.metric_count"
