# Data Model: Assumption-Based What-If Scenarios

## Overview

Phase 1 introduces structured scenario entities that sit alongside existing NL2SQL response models. The model is intentionally assumption-driven and deterministic.

## Entities

### ScenarioIntent

Represents orchestrator classification output for a user prompt.

| Field | Type | Description |
|------|------|-------------|
| `mode` | `Literal["scenario", "standard", "conversation"]` | Selected processing mode |
| `confidence` | `float` | Confidence score for routing decision |
| `reason` | `str` | Short explanation for why mode was selected |
| `detected_patterns` | `list[str]` | Phrase/features that triggered classification |

Validation rules:
- `confidence` in [0.0, 1.0]
- `mode="scenario"` requires at least one detected pattern or explicit assumption phrase

### ScenarioAssumption

Represents one user-provided or defaulted assumption.

| Field | Type | Description |
|------|------|-------------|
| `name` | `str` | Assumption identifier (for example `price_delta_pct`) |
| `scope` | `str` | Metric or segment scope (global, category, item, supplier, etc.) |
| `value` | `float` | Assumption numeric value |
| `unit` | `Literal["pct", "absolute", "days", "count"]` | Value unit |
| `source` | `Literal["user", "default", "inferred"]` | Provenance |

Validation rules:
- `value` bounded by business-safe ranges per assumption type
- `scope` must map to a supported phase-1 scenario type

### ScenarioAssumptionSet

Collection of assumptions used to compute the scenario.

| Field | Type | Description |
|------|------|-------------|
| `scenario_type` | `str` | Supported phase-1 category |
| `assumptions` | `list[ScenarioAssumption]` | Applied assumptions |
| `missing_requirements` | `list[str]` | Missing inputs requiring hint/clarification |
| `is_complete` | `bool` | Whether computation can execute |

Validation rules:
- `is_complete=false` when required assumptions are missing
- No conflicting assumptions for same metric/scope combination

### ScenarioMetricValue

Represents one baseline/scenario metric point.

| Field | Type | Description |
|------|------|-------------|
| `metric` | `str` | Metric name (revenue, units, cost, profit, etc.) |
| `dimension_key` | `str` | Grouping key (item/category/supplier/etc.) |
| `baseline` | `float` | Baseline value |
| `scenario` | `float` | Adjusted value |
| `delta_abs` | `float` | Absolute difference |
| `delta_pct` | `float` | Percent difference |

Validation rules:
- `delta_abs = scenario - baseline`
- `delta_pct` computed safely when baseline is zero (defined fallback behavior)

### ScenarioComputationResult

Container for computed scenario outputs.

| Field | Type | Description |
|------|------|-------------|
| `request_id` | `str` | Correlation identifier |
| `scenario_type` | `str` | Executed scenario category |
| `metrics` | `list[ScenarioMetricValue]` | Computed metric rows |
| `summary_totals` | `dict[str, float]` | Aggregate totals across result set |
| `data_limitations` | `list[str]` | Data caveats shown to user |

Validation rules:
- Non-empty `metrics` for successful run
- `data_limitations` required when sparse/missing signal detected

### ScenarioVisualizationPayload

Chart-ready representation consumed by assistant-ui/tool-ui components.

| Field | Type | Description |
|------|------|-------------|
| `chart_type` | `Literal["bar", "line", "combo"]` | Preferred visual form |
| `x_key` | `str` | X-axis dimension |
| `series` | `list[dict]` | Baseline/scenario series definitions |
| `rows` | `list[dict[str, str | int | float | bool | None]]` | Renderable data rows |
| `labels` | `dict[str, str]` | Friendly labels for legend/tooltips |

Validation rules:
- Must include both baseline and scenario series
- `rows` schema must match series keys

### ScenarioNarrativeSummary

Short explanation returned with results.

| Field | Type | Description |
|------|------|-------------|
| `headline` | `str` | One-line summary |
| `key_changes` | `list[str]` | 1-3 key impact bullets |
| `confidence_note` | `str | None` | Optional caveat |

Validation rules:
- Statements must be derivable from ScenarioComputationResult
- Keep concise for chat readability

### PromptHint

Hint object for clarification/discoverability.

| Field | Type | Description |
|------|------|-------------|
| `kind` | `Literal["clarification", "discoverability"]` | Hint purpose |
| `message` | `str` | Human-readable guidance |
| `examples` | `list[str]` | Example prompts |
| `supported_types` | `list[str]` | Supported scenario categories |

Validation rules:
- `discoverability` hints should include supported categories
- `clarification` hints should include missing input guidance

## Relationships

- `ScenarioIntent` determines whether `ScenarioAssumptionSet` is built.
- `ScenarioAssumptionSet` feeds deterministic computation into `ScenarioComputationResult`.
- `ScenarioComputationResult` is transformed into `ScenarioVisualizationPayload` and `ScenarioNarrativeSummary`.
- `PromptHint` may be emitted before or alongside computation results.

## State Transitions

### Scenario Request Lifecycle

1. Prompt received
2. `ScenarioIntent` classified
3. If mode is `scenario`:
   - Build/validate `ScenarioAssumptionSet`
   - If incomplete, return `PromptHint(kind=clarification|discoverability)`
   - If complete, compute `ScenarioComputationResult`
4. Build `ScenarioVisualizationPayload` + `ScenarioNarrativeSummary`
5. Return structured scenario response

### Failure/Degradation Paths

- Insufficient data signal: return partial computation plus `data_limitations` and hints.
- Chart payload failure: return numeric metrics + narrative fallback (FR-014).
