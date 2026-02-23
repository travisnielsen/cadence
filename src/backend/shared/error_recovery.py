"""Error recovery helpers for query validation failures.

Pure functions that classify validation violations, build user-friendly error
messages, and select contextual recovery suggestions.
"""

from models import SchemaSuggestion

# ── Error classification patterns ────────────────────────────────────────

_DISALLOWED_TABLE_PATTERNS = {"disallowed table", "not in the allowed", "table not allowed"}
_SYNTAX_PATTERNS = {"syntax error", "parse error", "invalid sql", "incorrect syntax"}

# Schema area -> example recovery prompts
_RECOVERY_SUGGESTIONS: dict[str, list[SchemaSuggestion]] = {
    "sales": [
        SchemaSuggestion(title="Order summary", prompt="Show me a summary of recent orders"),
        SchemaSuggestion(title="Top customers", prompt="Who are the top customers by revenue?"),
        SchemaSuggestion(title="Invoice totals", prompt="Show invoice totals by month"),
    ],
    "purchasing": [
        SchemaSuggestion(title="Purchase orders", prompt="Show recent purchase order status"),
        SchemaSuggestion(title="Supplier list", prompt="List all suppliers and their categories"),
        SchemaSuggestion(title="PO volumes", prompt="Show purchase order volumes by supplier"),
    ],
    "warehouse": [
        SchemaSuggestion(title="Stock levels", prompt="What are the current stock levels?"),
        SchemaSuggestion(title="Low stock", prompt="Show items with low stock quantities"),
        SchemaSuggestion(title="Stock groups", prompt="List stock items by group"),
    ],
    "application": [
        SchemaSuggestion(title="People", prompt="Show people and their roles"),
        SchemaSuggestion(title="Cities", prompt="List cities and states in the system"),
        SchemaSuggestion(title="Delivery methods", prompt="Show available delivery methods"),
    ],
}

_GENERIC_SUGGESTIONS: list[SchemaSuggestion] = [
    SchemaSuggestion(title="Browse sales", prompt="Show me recent sales orders"),
    SchemaSuggestion(title="Browse inventory", prompt="What stock items are available?"),
    SchemaSuggestion(title="Browse suppliers", prompt="List all suppliers"),
]


def classify_violations(violations: list[str]) -> str:
    """Classify validation violations into a category.

    Args:
        violations: List of violation description strings.

    Returns:
        One of 'disallowed_tables', 'syntax', or 'generic'.
    """
    combined = " ".join(violations).lower()
    for pattern in _DISALLOWED_TABLE_PATTERNS:
        if pattern in combined:
            return "disallowed_tables"
    for pattern in _SYNTAX_PATTERNS:
        if pattern in combined:
            return "syntax"
    return "generic"


def detect_area_from_tables(tables: list[str]) -> str | None:
    """Detect schema area from fully-qualified table names.

    Args:
        tables: List of table names like ['Sales.Orders', 'Sales.Customers'].

    Returns:
        Lowercase schema area or None.
    """
    if not tables:
        return None
    first = tables[0]
    if "." not in first:
        return None
    area = first.split(".")[0].lower()
    return area if area in _RECOVERY_SUGGESTIONS else None


def build_error_recovery(
    violations: list[str],
    tables_used: list[str],
) -> tuple[str, list[SchemaSuggestion]]:
    """Build a user-friendly error message and recovery suggestions.

    Classifies the validation failure and selects 2-3 contextual suggestions
    based on the schema area of the tables involved in the query.

    Args:
        violations: List of query validation violation strings.
        tables_used: List of fully-qualified table names (e.g., ['Sales.Orders']).

    Returns:
        Tuple of (error_message, recovery_suggestions).
    """
    category = classify_violations(violations)
    violation_summary = "; ".join(violations)

    # Category-specific error messages
    if category == "disallowed_tables":
        message = (
            "Your request references data that isn't available in the current database. "
            "Try asking about sales, purchasing, warehouse, or application data instead."
        )
    elif category == "syntax":
        message = (
            "I had trouble constructing a valid query for your request. "
            "Could you rephrase your question or be more specific about what data you need?"
        )
    else:
        message = (
            f"I was unable to generate a valid query for your request. "
            f"Validation issues: {violation_summary}. "
            f"Please try rephrasing your question or be more specific about what data you need."
        )

    # Select recovery suggestions from the matched schema area
    schema_area = detect_area_from_tables(tables_used)
    if schema_area and schema_area in _RECOVERY_SUGGESTIONS:
        suggestions = _RECOVERY_SUGGESTIONS[schema_area][:3]
    else:
        suggestions = _GENERIC_SUGGESTIONS[:3]

    return message, suggestions
