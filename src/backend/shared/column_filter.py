"""Pure-function column refinement for dynamic query results.

This module is intentionally free of external dependencies (Azure SDK,
agent_framework, etc.) so that it can be unit-tested without mocking.

Follows the same pattern as ``substitution.py``: a frozen dataclass
result type and a pure function that transforms data.
"""

import re
from dataclasses import dataclass, field
from operator import itemgetter

# Default maximum number of visible columns before capping
DEFAULT_MAX_DISPLAY_COLUMNS = 8

# Minimum stem length for suffix-stripped column name matching
_MIN_STEM_LENGTH = 3

# Regex patterns for SQL clause detection
_GROUP_BY_RE = re.compile(
    r"\bGROUP\s+BY\b(.+?)(?:\bHAVING\b|\bORDER\b|\bLIMIT\b|$)", re.IGNORECASE | re.DOTALL
)
_ORDER_BY_RE = re.compile(
    r"\bORDER\s+BY\b(.+?)(?:\bLIMIT\b|\bOFFSET\b|$)", re.IGNORECASE | re.DOTALL
)
_AGGREGATE_RE = re.compile(r"\b(?:COUNT|SUM|AVG|MIN|MAX)\s*\(", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ColumnRefinementResult:
    """Result of column refinement after stripping empty columns and capping.

    Attributes:
        columns: Visible column names (after stripping + capping).
        hidden_columns: Column names hidden by the cap.
        rows: Original row data (all columns preserved for client-side expansion).
    """

    columns: list[str] = field(default_factory=list)
    hidden_columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)


def _is_empty_column(rows: list[dict], col: str) -> bool:
    """Check if a column is empty (all NULL or empty string) across all rows.

    Args:
        rows: List of row dictionaries.
        col: Column name to check.

    Returns:
        True if every row value for this column is None or empty string.
    """
    return all(row.get(col) is None or row.get(col) == "" for row in rows)  # noqa: PLC1901


def _rank_columns(
    columns: list[str],
    user_query: str,
    sql: str,
) -> list[str]:
    """Rank columns by relevance for display capping.

    Ranking tiers (lower score = higher priority):
    1. Mentioned in user question (string match)
    2. Referenced in GROUP BY / ORDER BY / aggregate clauses
    3. Primary key or name-like columns (heuristic)
    4. Positional order in SELECT (stable tiebreaker)

    Args:
        columns: Column names to rank.
        user_query: The user's original question.
        sql: The generated SQL query.

    Returns:
        Columns sorted by relevance (most relevant first).
    """
    user_query_lower = user_query.lower()

    # Extract GROUP BY and ORDER BY column references
    clause_refs: set[str] = set()
    for pattern in (_GROUP_BY_RE, _ORDER_BY_RE):
        match = pattern.search(sql)
        if match:
            clause_text = match.group(1).upper()
            for col in columns:
                if col.upper() in clause_text:
                    clause_refs.add(col)

    # Check for aggregate usage
    has_aggregates = bool(_AGGREGATE_RE.search(sql))

    def _score(col: str, position: int) -> tuple[int, int]:
        """Score a column — lower tuple is higher priority."""
        col_lower = col.lower()
        # Strip table alias prefix for matching (e.g., "c.CustomerName" -> "customername")
        bare_col = col_lower.split(".")[-1] if "." in col_lower else col_lower

        # Tier 1: Mentioned in user question
        if bare_col in user_query_lower or _word_match(bare_col, user_query_lower):
            return (0, position)

        # Tier 2: In GROUP BY / ORDER BY / aggregate context
        if col in clause_refs:
            return (1, position)
        # Computed/aliased columns from aggregates get tier 2
        if has_aggregates and col_lower not in {c.lower() for c in clause_refs}:
            col_upper = col.upper()
            if any(kw in col_upper for kw in ("TOTAL", "COUNT", "SUM", "AVG", "MIN", "MAX")):
                return (1, position)

        # Tier 3: PK-like or name-like columns
        if bare_col.endswith("id") or bare_col.endswith("name") or bare_col == "name":
            return (2, position)

        # Tier 4: Positional order
        return (3, position)

    scored = [(col, _score(col, i)) for i, col in enumerate(columns)]
    scored.sort(key=itemgetter(1))
    return [col for col, _ in scored]


def _word_match(needle: str, haystack: str) -> bool:
    """Check if needle appears as a word-like substring in haystack.

    Handles common patterns like "customername" matching "customer name"
    or "customer" matching in "top customers by order count".

    Args:
        needle: The column name (lowercase, no alias prefix).
        haystack: The user query (lowercase).

    Returns:
        True if there's a meaningful match.
    """
    # Direct substring
    if needle in haystack:
        return True

    # Remove common suffixes and check again
    for suffix in ("name", "id", "date", "count", "number", "code"):
        if needle.endswith(suffix) and len(needle) > len(suffix):
            stem = needle[: -len(suffix)]
            if len(stem) >= _MIN_STEM_LENGTH and stem in haystack:
                return True

    return False


def refine_columns(
    columns: list[str],
    rows: list[dict],
    user_query: str = "",
    sql: str = "",
    max_cols: int = DEFAULT_MAX_DISPLAY_COLUMNS,
) -> ColumnRefinementResult:
    """Strip empty columns and cap visible columns with relevance ranking.

    Processing steps:
    1. Remove columns where every row value is NULL or empty string.
    2. If remaining columns exceed ``max_cols``, rank by relevance and cap.
    3. Return visible columns, hidden columns, and original rows.

    Args:
        columns: Column names from the query result.
        rows: Row dictionaries from the query result.
        user_query: The user's original question (for relevance ranking).
        sql: The generated SQL query (for clause-based ranking).
        max_cols: Maximum number of visible columns.

    Returns:
        A ``ColumnRefinementResult`` with visible/hidden column lists and rows.
    """
    if not columns or not rows:
        return ColumnRefinementResult(columns=list(columns), hidden_columns=[], rows=list(rows))

    # Step 1: Strip empty columns (preserve original order)
    non_empty = [col for col in columns if not _is_empty_column(rows, col)]

    # If all columns are empty, keep the originals for display context
    if not non_empty:
        return ColumnRefinementResult(columns=list(columns), hidden_columns=[], rows=list(rows))

    # Step 2: Cap if needed
    if len(non_empty) <= max_cols:
        return ColumnRefinementResult(columns=non_empty, hidden_columns=[], rows=list(rows))

    # Rank by relevance and split into visible/hidden
    ranked = _rank_columns(non_empty, user_query, sql)
    visible = ranked[:max_cols]
    hidden = ranked[max_cols:]

    return ColumnRefinementResult(columns=visible, hidden_columns=hidden, rows=list(rows))
