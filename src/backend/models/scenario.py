"""Pydantic models for assumption-based what-if scenario analysis.

Defines the core entities for scenario intent classification, assumption
sets, computation results, visualization payloads, narrative summaries,
and prompt hints.  Follows the data model specification in
``specs/004-what-if-scenarios/data-model.md``.
"""

from __future__ import annotations

from math import isclose
from typing import Final, Literal

from pydantic import BaseModel, Field, model_validator

# Mirror constants from shared.scenario_constants to avoid circular import:
# models → shared → shared.tools → models
_MAX_KEY_CHANGES: Final[int] = 3
_ZERO_BASELINE_DELTA_PCT_FALLBACK: Final[float] = 0.0

# Tolerance thresholds for delta validation
_ABS_DELTA_TOLERANCE = 1e-9
_PCT_DELTA_TOLERANCE = 1e-6

# ── Intent Classification ────────────────────────────────────────────────


class ScenarioIntent(BaseModel):
    """Classification output indicating whether a prompt requests scenario analysis."""

    mode: Literal["scenario", "standard", "conversation"] = Field(
        description="Selected processing mode"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score for the routing decision",
    )
    reason: str = Field(description="Short explanation for why this mode was selected")
    detected_patterns: list[str] = Field(
        default_factory=list,
        description="Phrases or features that triggered classification",
    )

    @model_validator(mode="after")
    def _scenario_requires_pattern(self) -> ScenarioIntent:
        """Enforce that mode='scenario' has at least one detected pattern."""
        if self.mode == "scenario" and not self.detected_patterns:
            msg = "mode='scenario' requires at least one entry in detected_patterns"
            raise ValueError(msg)
        return self


# ── Assumptions ──────────────────────────────────────────────────────────


class ScenarioAssumption(BaseModel):
    """One user-provided or defaulted assumption for scenario computation."""

    name: str = Field(description="Assumption identifier (e.g. 'price_delta_pct')")
    scope: str = Field(
        description=("Metric or segment scope (global, category, item, supplier)"),
    )
    value: float = Field(description="Assumption numeric value")
    unit: Literal["pct", "absolute", "days", "count"] = Field(description="Value unit")
    source: Literal["user", "default", "inferred"] = Field(
        description="Provenance of this assumption"
    )


class ScenarioAssumptionSet(BaseModel):
    """Collection of assumptions used to compute a scenario."""

    scenario_type: str = Field(description="Supported phase-1 scenario category")
    assumptions: list[ScenarioAssumption] = Field(
        default_factory=list,
        description="Applied assumptions",
    )
    missing_requirements: list[str] = Field(
        default_factory=list,
        description="Missing inputs requiring hint or clarification",
    )
    is_complete: bool = Field(
        default=True,
        description="Whether computation can execute",
    )

    @model_validator(mode="after")
    def _incomplete_when_missing(self) -> ScenarioAssumptionSet:
        """Ensure is_complete=False when required assumptions are missing."""
        if self.missing_requirements and self.is_complete:
            msg = "is_complete must be False when missing_requirements is non-empty"
            raise ValueError(msg)
        return self


# ── Metric Values ────────────────────────────────────────────────────────


class ScenarioMetricValue(BaseModel):
    """One baseline/scenario metric data point."""

    metric: str = Field(description="Metric name (revenue, units, cost, profit, etc.)")
    dimension_key: str = Field(description="Grouping key (item, category, supplier, etc.)")
    baseline: float = Field(description="Baseline value")
    scenario: float = Field(description="Adjusted value")
    delta_abs: float = Field(description="Absolute difference")
    delta_pct: float = Field(description="Percent difference")

    @model_validator(mode="after")
    def _validate_delta_consistency(self) -> ScenarioMetricValue:
        """Verify delta_abs = scenario - baseline and safe delta_pct."""
        expected_abs = self.scenario - self.baseline
        if abs(self.delta_abs - expected_abs) > _ABS_DELTA_TOLERANCE:
            msg = f"delta_abs ({self.delta_abs}) must equal scenario - baseline ({expected_abs})"
            raise ValueError(msg)
        if isclose(self.baseline, 0.0, abs_tol=_ABS_DELTA_TOLERANCE):
            if abs(self.delta_pct - _ZERO_BASELINE_DELTA_PCT_FALLBACK) > _PCT_DELTA_TOLERANCE:
                msg = f"delta_pct must be {_ZERO_BASELINE_DELTA_PCT_FALLBACK} when baseline is zero"
                raise ValueError(msg)
        else:
            expected_pct = (self.delta_abs / self.baseline) * 100.0
            if abs(self.delta_pct - expected_pct) > _PCT_DELTA_TOLERANCE:
                msg = (
                    f"delta_pct ({self.delta_pct}) must equal "
                    f"(delta_abs / baseline) * 100 ({expected_pct})"
                )
                raise ValueError(msg)
        return self


# ── Computation Result ───────────────────────────────────────────────────


class ScenarioComputationResult(BaseModel):
    """Container for computed scenario outputs."""

    request_id: str = Field(description="Correlation identifier")
    scenario_type: str = Field(description="Executed scenario category")
    metrics: list[ScenarioMetricValue] = Field(description="Computed metric rows")
    summary_totals: dict[str, float] = Field(
        default_factory=dict,
        description="Aggregate totals across the result set",
    )
    data_limitations: list[str] = Field(
        default_factory=list,
        description="Data caveats shown to the user",
    )


# ── Narrative Summary ────────────────────────────────────────────────────


class ScenarioNarrativeSummary(BaseModel):
    """Short explanation returned alongside scenario results."""

    headline: str = Field(description="One-line summary")
    key_changes: list[str] = Field(
        min_length=1,
        max_length=_MAX_KEY_CHANGES,
        description="1-3 key impact bullets",
    )
    confidence_note: str | None = Field(
        default=None,
        description="Optional caveat about result confidence",
    )


# ── Visualization Payload ────────────────────────────────────────────────


class ChartSeriesDefinition(BaseModel):
    """Definition for a single chart series (baseline, scenario, or delta)."""

    key: str = Field(description="Data key in each row")
    label: str = Field(description="Friendly label for legend/tooltip")
    kind: Literal["baseline", "scenario", "delta"] | None = Field(
        default=None,
        description="Series purpose",
    )


class ScenarioVisualizationPayload(BaseModel):
    """Chart-ready payload consumed by assistant-ui/tool-ui components."""

    chart_type: Literal["bar", "line", "combo"] = Field(description="Preferred visual form")
    x_key: str = Field(description="X-axis dimension key")
    series: list[ChartSeriesDefinition] = Field(
        min_length=2,
        description="Baseline and scenario series definitions",
    )
    rows: list[dict[str, str | int | float | bool | None]] = Field(
        default_factory=list,
        description="Renderable data rows",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Friendly labels for legend and tooltips",
    )


# ── Prompt Hints ─────────────────────────────────────────────────────────


class PromptHint(BaseModel):
    """Hint object for clarification or discoverability guidance."""

    kind: Literal["clarification", "discoverability", "drill_down"] = Field(
        description="Hint purpose",
    )
    message: str = Field(description="Human-readable guidance text")
    examples: list[str] = Field(
        default_factory=list,
        description="Example prompts",
    )
    supported_types: list[str] = Field(
        default_factory=list,
        description="Supported scenario categories",
    )
