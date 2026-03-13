"""Scenario math helpers for assumption-based what-if computations.

Provides baseline aggregation, assumption transforms, delta computation,
and safe percent-change calculation with zero-baseline handling.

All functions use deterministic arithmetic — no probabilistic or
predictive modeling in phase 1.
"""

from __future__ import annotations

from math import isclose
from typing import Any

from models.scenario import ScenarioMetricValue
from shared.scenario_constants import ZERO_BASELINE_DELTA_PCT_FALLBACK

_ZERO_TOLERANCE = 1e-12


def compute_delta_abs(baseline: float, scenario: float) -> float:
    """Compute absolute delta between scenario and baseline values.

    Args:
        baseline: Original baseline metric value.
        scenario: Adjusted scenario metric value.

    Returns:
        Absolute difference (scenario - baseline).
    """
    return scenario - baseline


def compute_delta_pct(baseline: float, delta_abs: float) -> float:
    """Compute percent change with safe zero-baseline handling.

    When baseline is zero, returns the configured fallback value
    instead of raising a division-by-zero error.

    Args:
        baseline: Original baseline metric value.
        delta_abs: Absolute difference (scenario - baseline).

    Returns:
        Percent change as ``(delta_abs / baseline) * 100``, or
        the zero-baseline fallback when baseline is zero.
    """
    if isclose(baseline, 0.0, abs_tol=_ZERO_TOLERANCE):
        return ZERO_BASELINE_DELTA_PCT_FALLBACK
    return (delta_abs / baseline) * 100.0


def apply_pct_assumption(baseline: float, pct_delta: float) -> float:
    """Apply a percentage assumption to a baseline value.

    Args:
        baseline: Original baseline metric value.
        pct_delta: Percentage change to apply (e.g. 5.0 for +5%).

    Returns:
        Adjusted scenario value.
    """
    return baseline * (1.0 + pct_delta / 100.0)


def apply_absolute_assumption(baseline: float, abs_delta: float) -> float:
    """Apply an absolute assumption to a baseline value.

    Args:
        baseline: Original baseline metric value.
        abs_delta: Absolute change to apply.

    Returns:
        Adjusted scenario value.
    """
    return baseline + abs_delta


def aggregate_baseline(
    rows: list[dict[str, Any]],
    metric_key: str,
    dimension_key: str,
) -> dict[str, float]:
    """Aggregate baseline values grouped by dimension key.

    Sums the specified metric column for each distinct dimension
    value.  Pure computation — no I/O.

    Args:
        rows: Row dicts from a baseline query.
        metric_key: Column name of the metric to aggregate.
        dimension_key: Column name of the grouping dimension.

    Returns:
        Mapping of dimension value to summed metric value.
    """
    aggregates: dict[str, float] = {}
    for row in rows:
        dim = str(row.get(dimension_key, "unknown"))
        val = float(row.get(metric_key, 0))
        aggregates[dim] = aggregates.get(dim, 0.0) + val
    return aggregates


def compute_scenario_metrics(
    baseline_aggregates: dict[str, float],
    metric_name: str,
    pct_delta: float | None = None,
    abs_delta: float | None = None,
) -> list[ScenarioMetricValue]:
    """Compute scenario metric values from baseline aggregates.

    Applies a percentage or absolute assumption to each baseline
    value and computes deltas using existing arithmetic helpers.

    Args:
        baseline_aggregates: Dimension key → summed baseline value.
        metric_name: Metric label (e.g. ``"Revenue"``).
        pct_delta: Percentage change to apply.
        abs_delta: Absolute change to apply.

    Returns:
        List of ``ScenarioMetricValue`` with computed deltas.
    """
    metrics: list[ScenarioMetricValue] = []
    for dim_key, baseline_val in baseline_aggregates.items():
        if pct_delta is not None:
            scenario_val = apply_pct_assumption(baseline_val, pct_delta)
        elif abs_delta is not None:
            scenario_val = apply_absolute_assumption(
                baseline_val,
                abs_delta,
            )
        else:
            scenario_val = baseline_val

        d_abs = compute_delta_abs(baseline_val, scenario_val)
        d_pct = compute_delta_pct(baseline_val, d_abs)

        metrics.append(
            ScenarioMetricValue(
                metric=metric_name,
                dimension_key=dim_key,
                baseline=baseline_val,
                scenario=scenario_val,
                delta_abs=d_abs,
                delta_pct=d_pct,
            )
        )
    return metrics
