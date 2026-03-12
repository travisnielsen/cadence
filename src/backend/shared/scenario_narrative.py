"""Deterministic narrative summary builder for scenario results.

Builds concise, numerically grounded narrative summaries from
``ScenarioComputationResult`` data.  All text is derived from
computed values — no LLM-generated or free-form content (FR-007, R5).
"""

from __future__ import annotations

from models.scenario import (
    ScenarioComputationResult,
    ScenarioMetricValue,
    ScenarioNarrativeSummary,
)
from shared.scenario_constants import MAX_KEY_CHANGES

# ── Thresholds ───────────────────────────────────────────────────────────

LOW_IMPACT_PCT_THRESHOLD: float = 1.0
"""Deltas below this percentage are considered near-zero (T034)."""


def _direction_word(delta: float) -> str:
    """Return human-readable direction word for a delta value."""
    if delta > 0:
        return "increases"
    if delta < 0:
        return "decreases"
    return "remains unchanged"


def _format_pct(value: float) -> str:
    """Format a percentage value for display, stripping trailing zeros."""
    formatted = f"{abs(value):.1f}"
    formatted = formatted.removesuffix(".0")
    return f"{formatted}%"


def build_narrative_summary(
    result: ScenarioComputationResult,
) -> ScenarioNarrativeSummary:
    """Build a deterministic narrative summary from computed results.

    Derives all text from ``ScenarioComputationResult`` values.
    Handles minimal-impact cases where all deltas are near zero
    (T034).

    Args:
        result: Computed scenario metrics and totals.

    Returns:
        ``ScenarioNarrativeSummary`` with headline, key_changes,
        and optional confidence_note.
    """
    metrics = result.metrics
    if not metrics:
        return ScenarioNarrativeSummary(
            headline="No metrics available for analysis",
            key_changes=["No data to summarize"],
            confidence_note=None,
        )

    # Sort by absolute delta magnitude (largest first)
    ranked = sorted(
        metrics,
        key=lambda m: abs(m.delta_abs),
        reverse=True,
    )

    # Check if all deltas are near zero (T034)
    all_near_zero = all(abs(m.delta_pct) < LOW_IMPACT_PCT_THRESHOLD for m in metrics)

    headline = _build_headline(
        ranked[0],
        result.scenario_type,
        all_near_zero=all_near_zero,
    )
    key_changes = _build_key_changes(ranked, all_near_zero=all_near_zero)

    # confidence_note is no longer populated here — data limitations
    # are shown by the dedicated DataLimitations UI component to
    # avoid duplicating the same information.
    return ScenarioNarrativeSummary(
        headline=headline,
        key_changes=key_changes,
        confidence_note=None,
    )


def _build_headline(
    top_metric: ScenarioMetricValue,
    scenario_type: str,
    *,
    all_near_zero: bool,
) -> str:
    """Build headline from the largest absolute delta metric.

    Args:
        top_metric: Metric with the largest absolute delta.
        scenario_type: Scenario category for context.
        all_near_zero: Whether all deltas are near zero.

    Returns:
        One-line headline string.
    """
    friendly_type = scenario_type.replace("_", " ")

    if all_near_zero:
        return f"{top_metric.metric} shows minimal impact under {friendly_type} scenario"

    direction = _direction_word(top_metric.delta_abs)
    pct = _format_pct(top_metric.delta_pct)

    return f"{top_metric.metric} {direction} {pct} under {friendly_type} scenario"


def _build_key_changes(
    ranked_metrics: list[ScenarioMetricValue],
    *,
    all_near_zero: bool,
) -> list[str]:
    """Build key change bullets from top metrics by delta magnitude.

    Args:
        ranked_metrics: Metrics sorted by absolute delta (descending).
        all_near_zero: Whether all deltas are near zero.

    Returns:
        1 to MAX_KEY_CHANGES bullet strings.
    """
    top = ranked_metrics[:MAX_KEY_CHANGES]
    changes: list[str] = []

    for m in top:
        if all_near_zero:
            changes.append(
                f"{m.metric} ({m.dimension_key}): "
                f"near-zero change ({_format_pct(m.delta_pct)}), "
                f"baseline {m.baseline:,.2f} → scenario {m.scenario:,.2f}"
            )
        else:
            direction = _direction_word(m.delta_abs)
            changes.append(
                f"{m.metric} ({m.dimension_key}) {direction} "
                f"by {_format_pct(m.delta_pct)} "
                f"({m.delta_abs:+,.2f})"
            )

    return changes
