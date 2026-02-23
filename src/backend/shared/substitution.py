"""Pure-function parameter substitution for SQL templates.

This module is intentionally free of external dependencies (Azure SDK,
agent_framework, etc.) so that it can be unit-tested without mocking.
"""

import re
from dataclasses import dataclass, field
from typing import Any

# Tokens that are safe to inline because they are validated upstream or are SQL keywords
_SQL_KEYWORDS: frozenset[str] = frozenset({"ASC", "DESC", "NULL"})
_SQL_FUNC_RE: re.Pattern[str] = re.compile(r"[A-Z_]+\s*\(", re.IGNORECASE)

# SQL Server requires parentheses around parameterized TOP values: TOP (?) not TOP ?
_TOP_PARAM_RE: re.Pattern[str] = re.compile(r"\bTOP\s+\?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParameterizedQuery:
    """Result of parameter substitution separating display SQL from execution SQL.

    Attributes:
        display_sql: SQL with literal values inlined (for logging and UI display).
        exec_sql: SQL with ``?`` placeholders for parameterized execution.
        exec_params: Ordered values matching the ``?`` placeholders in *exec_sql*.
    """

    display_sql: str
    exec_sql: str
    exec_params: list[Any] = field(default_factory=list)


def substitute_parameters(sql_template: str, params: dict[str, Any]) -> ParameterizedQuery:
    """Substitute parameter tokens, using ``?`` placeholders where safe.

    SQL keywords (ASC / DESC / NULL) and SQL expressions containing function
    calls (e.g. ``DATEADD(...)``) are inlined directly — they cannot be
    represented as bind parameters.  All other literal values use ``?``
    placeholders for parameterized execution.

    Args:
        sql_template: The SQL template with ``%{{param}}%`` tokens.
        params: Dictionary of parameter name → value.

    Returns:
        A ``ParameterizedQuery`` with display SQL, execution SQL, and params.
    """
    display = sql_template
    executed = sql_template
    ordered_params: list[Any] = []

    for name, value in params.items():
        token = f"%{{{{{name}}}}}%"
        if token not in display:
            continue

        if value is None:
            display = display.replace(token, "NULL")
            executed = executed.replace(token, "NULL")
        elif isinstance(value, bool):
            int_val = 1 if value else 0
            display = display.replace(token, str(int_val))
            executed = executed.replace(token, "?")
            ordered_params.append(int_val)
        elif isinstance(value, str) and value.upper() in _SQL_KEYWORDS:
            # SQL keyword — safe to inline (validated upstream)
            upper = value.upper()
            display = display.replace(token, upper)
            executed = executed.replace(token, upper)
        elif isinstance(value, str) and _SQL_FUNC_RE.search(value):
            # SQL expression (e.g. DATEADD(...)) — must inline
            display = display.replace(token, value)
            executed = executed.replace(token, value)
        elif isinstance(value, (int, float)):
            display = display.replace(token, str(value))
            executed = executed.replace(token, "?")
            ordered_params.append(value)
        elif isinstance(value, str):
            # Handle tokens wrapped in quotes: '%{{name}}%' → ?
            quoted_token = f"'{token}'"
            if quoted_token in executed:
                display = display.replace(quoted_token, f"'{value}'")
                executed = executed.replace(quoted_token, "?")
            else:
                display = display.replace(token, value)
                executed = executed.replace(token, "?")
            ordered_params.append(value)
        else:
            display = display.replace(token, str(value))
            executed = executed.replace(token, "?")
            ordered_params.append(value)

    # SQL Server requires parentheses around parameterized TOP: TOP (?) not TOP ?
    executed = _TOP_PARAM_RE.sub("TOP (?)", executed)

    return ParameterizedQuery(display_sql=display, exec_sql=executed, exec_params=ordered_params)
