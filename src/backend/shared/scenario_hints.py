"""Scenario prompt hint builders for clarification, discoverability, and drill-down.

Provides functions that construct ``PromptHint`` objects to guide users
toward valid scenario inputs, help them discover supported what-if
categories, and suggest drill-down explorations into specific groups.
"""

from models.scenario import PromptHint
from shared.scenario_constants import SUPPORTED_SCENARIO_TYPES

# ── Example prompts per scenario type (for discoverability) ──────────────

_SCENARIO_TYPE_EXAMPLES: dict[str, list[str]] = {
    "price_delta": [
        "What if we raise prices by 10%?",
        "What happens to revenue if prices drop 5%?",
    ],
    "demand_delta": [
        "What if demand increases 20% next quarter?",
        "Show me the impact of a 15% drop in order volume",
    ],
    "supplier_cost_delta": [
        "What if supplier costs go up 8%?",
        "How would a 12% reduction in purchasing costs affect profit?",
    ],
    "inventory_policy_delta": [
        "What if we increase reorder points by 25%?",
        "Show the impact of raising target stock levels by 50 units",
    ],
}

# ── Friendly names for scenario types ────────────────────────────────────

_SCENARIO_TYPE_LABELS: dict[str, str] = {
    "price_delta": "Price changes",
    "demand_delta": "Demand changes",
    "supplier_cost_delta": "Supplier cost changes",
    "inventory_policy_delta": "Inventory policy changes",
}


def build_clarification_hint(
    missing_inputs: list[str],
    scenario_type: str | None = None,
) -> PromptHint:
    """Build a clarification hint for incomplete scenario requests.

    Args:
        missing_inputs: Names of missing assumption parameters.
        scenario_type: The detected scenario type, if known.

    Returns:
        A ``PromptHint`` with kind='clarification' containing
        guidance about what information is needed.
    """
    missing_list = ", ".join(missing_inputs) if missing_inputs else "assumption details"
    type_label = _SCENARIO_TYPE_LABELS.get(scenario_type or "", scenario_type or "scenario")

    message = (
        f"Your {type_label.lower()} scenario request is missing: "
        f"{missing_list}. Please include a specific value "
        "so I can run the analysis."
    )

    examples: list[str] = []
    if scenario_type and scenario_type in _SCENARIO_TYPE_EXAMPLES:
        examples = list(_SCENARIO_TYPE_EXAMPLES[scenario_type])
    else:
        examples = ["What if prices increase by 10%?"]

    return PromptHint(
        kind="clarification",
        message=message,
        examples=examples,
        supported_types=[scenario_type] if scenario_type else [],
    )


def build_discoverability_hint() -> PromptHint:
    """Build a discoverability hint listing supported scenario types.

    Returns a hint with examples for each supported phase-1
    scenario category so users can learn what what-if questions
    are available.

    Returns:
        A ``PromptHint`` with kind='discoverability' containing
        supported types and example prompts.
    """
    examples: list[str] = []
    for st in SUPPORTED_SCENARIO_TYPES:
        type_examples = _SCENARIO_TYPE_EXAMPLES.get(st, [])
        examples.extend(type_examples[:1])

    type_labels = [_SCENARIO_TYPE_LABELS.get(st, st) for st in SUPPORTED_SCENARIO_TYPES]
    categories = ", ".join(type_labels)

    message = (
        "I can run what-if scenario analyses for these categories: "
        f"{categories}. Try one of the examples below to get started."
    )

    return PromptHint(
        kind="discoverability",
        message=message,
        examples=examples,
        supported_types=list(SUPPORTED_SCENARIO_TYPES),
    )


# ── Drill-down prompt templates per scenario type ────────────────────────

_DRILL_DOWN_TEMPLATES: dict[str, str] = {
    "price_delta": "What if we change prices by {pct}% for the {group} group?",
    "demand_delta": "What if demand changes by {pct}% for the {group} group?",
    "supplier_cost_delta": "What if supplier costs change by {pct}% for {group} suppliers?",
    "inventory_policy_delta": ("What if we adjust reorder points by {pct}% for the {group} group?"),
}


def build_drill_down_hints(
    top_groups: list[str],
    scenario_type: str,
    pct_value: float,
) -> PromptHint:
    """Build a drill-down hint suggesting deeper exploration of top groups.

    Args:
        top_groups: Dimension group names shown in the chart.
        scenario_type: The scenario category key.
        pct_value: The percentage assumption used in the analysis.

    Returns:
        A ``PromptHint`` with kind='drill_down' and example
        prompts targeting specific groups.
    """
    template = _DRILL_DOWN_TEMPLATES.get(
        scenario_type,
        "What if we change by {pct}% for {group}?",
    )
    pct_str = f"+{pct_value:g}" if pct_value > 0 else f"{pct_value:g}"
    examples = [template.format(pct=pct_str, group=group) for group in top_groups[:5]]

    type_label = _SCENARIO_TYPE_LABELS.get(scenario_type, scenario_type)
    message = f"Drill down into specific groups to see detailed {type_label.lower()} impact."

    return PromptHint(
        kind="drill_down",
        message=message,
        examples=examples,
        supported_types=[scenario_type],
    )
