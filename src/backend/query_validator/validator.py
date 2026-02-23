"""Pure query validation logic.

Validates SQL queries for syntax, table allowlist compliance,
statement type, and security patterns. No I/O, no framework
dependencies — suitable for direct unit testing.
"""

from __future__ import annotations

import logging
import re

from models import SQLDraft

logger = logging.getLogger(__name__)

# SQL injection patterns to detect
SQL_INJECTION_PATTERNS = [
    r";\s*--",  # Comment after semicolon
    r"'\s*OR\s+'?\d+'?\s*=\s*'?\d+'?",  # ' OR '1'='1'
    r"'\s*OR\s+''='",  # ' OR ''='
    r"UNION\s+SELECT",  # UNION injection
    r"INTO\s+OUTFILE",  # File write attempt
    r"INTO\s+DUMPFILE",  # File write attempt
    r"LOAD_FILE",  # File read attempt
    r"xp_cmdshell",  # SQL Server command execution
    r"sp_executesql",  # Dynamic SQL execution
    r"EXEC\s*\(",  # Procedure execution
    r"EXECUTE\s*\(",  # Procedure execution
    r"@@version",  # Information disclosure
    r"INFORMATION_SCHEMA",  # Schema enumeration
    r"sys\.",  # System table access
    r"WAITFOR\s+DELAY",  # Time-based injection
    r"BENCHMARK\s*\(",  # Time-based injection (MySQL)
]

# Dangerous keywords that should not appear in SELECT queries
DANGEROUS_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "REVOKE",
    "DENY",
    "BACKUP",
    "RESTORE",
    "SHUTDOWN",
    "DBCC",
]

_MIN_WRAPPED_IDENTIFIER_LENGTH = 2


def _split_select_items(select_clause: str) -> list[str]:
    """Split a SELECT projection clause into top-level comma-separated items."""
    items: list[str] = []
    current: list[str] = []
    depth = 0
    in_single_quote = False

    for char in select_clause:
        if char == "'":
            in_single_quote = not in_single_quote
            current.append(char)
            continue

        if in_single_quote:
            current.append(char)
            continue

        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1

        if char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue

        current.append(char)

    final_item = "".join(current).strip()
    if final_item:
        items.append(final_item)
    return items


def _extract_union_select_items(sql: str) -> list[list[str]]:
    """Extract top-level SELECT projection items for each UNION branch."""
    if not re.search(r"\bUNION(?:\s+ALL)?\b", sql, re.IGNORECASE):
        return []

    branches = re.split(r"\bUNION(?:\s+ALL)?\b", sql, flags=re.IGNORECASE)
    select_lists: list[list[str]] = []

    for branch in branches:
        match = re.search(r"(?is)\bSELECT\b\s*(.*?)\s*\bFROM\b", branch)
        if not match:
            return []
        select_lists.append(_split_select_items(match.group(1)))

    return select_lists


def _extract_first_select_items(sql: str) -> list[str]:
    """Extract top-level SELECT projection items for the first SELECT statement."""
    match = re.search(r"(?is)\bSELECT\b\s*(.*?)\s*\bFROM\b", sql)
    if not match:
        return []

    select_clause = match.group(1).strip()
    select_clause = re.sub(
        r"(?is)^TOP\s*\(?\s*\d+\s*\)?(?:\s+PERCENT)?(?:\s+WITH\s+TIES)?\s+",
        "",
        select_clause,
    )
    select_clause = re.sub(r"(?is)^DISTINCT\s+", "", select_clause)
    return _split_select_items(select_clause)


def _strip_identifier_wrappers(identifier: str) -> str:
    """Strip common SQL identifier wrappers from a token."""
    token = identifier.strip()
    if (
        token.startswith("[")
        and token.endswith("]")
        and len(token) >= _MIN_WRAPPED_IDENTIFIER_LENGTH
    ):
        return token[1:-1]
    if (
        token.startswith('"')
        and token.endswith('"')
        and len(token) >= _MIN_WRAPPED_IDENTIFIER_LENGTH
    ):
        return token[1:-1]
    return token


def _extract_output_column_name(select_expr: str) -> str | None:
    """Infer projected output column name from a SELECT expression."""
    expr = select_expr.strip()
    if not expr:
        return None

    alias_match = re.search(
        r"(?is)\bAS\s+(\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)\s*$",
        expr,
    )
    if alias_match:
        return _strip_identifier_wrappers(alias_match.group(1))

    token_match = re.search(r"(?is)(\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)\s*$", expr)
    if token_match:
        return _strip_identifier_wrappers(token_match.group(1))

    return None


def _check_dynamic_projection_constraints(sql: str) -> tuple[bool, list[str]]:
    """Validate dynamic query output columns for duplicates and identifier leakage."""
    violations: list[str] = []

    union_select_lists = _extract_union_select_items(sql)
    select_items = union_select_lists[0] if union_select_lists else _extract_first_select_items(sql)
    if not select_items:
        return True, violations

    seen_output_names: set[str] = set()
    duplicate_reported: set[str] = set()

    for index, item in enumerate(select_items, start=1):
        stripped_item = item.strip()
        if stripped_item == "*" or stripped_item.endswith(".*"):
            violations.append(
                "Dynamic query must not use wildcard projection ('*' or 'alias.*'); "
                "explicitly select non-ID display columns"
            )
            continue

        output_name = _extract_output_column_name(item)
        if not output_name:
            continue

        normalized_name = output_name.strip().lower()
        if not normalized_name:
            continue

        if normalized_name.endswith("id"):
            violations.append(
                f"Dynamic query must not return identifier column '{output_name}' "
                f"(column {index}); remove columns ending with ID"
            )

        if normalized_name in seen_output_names and normalized_name not in duplicate_reported:
            violations.append(
                f"Dynamic query has duplicate output column name '{output_name}'; "
                "all projected column names must be unique"
            )
            duplicate_reported.add(normalized_name)
        seen_output_names.add(normalized_name)

    return len(violations) == 0, violations


def _check_dynamic_historical_date_anchor(sql: str) -> tuple[bool, list[str]]:
    """Ensure dynamic SQL uses the historical anchor whenever GETDATE is referenced."""
    violations: list[str] = []

    if not re.search(r"(?is)\bGETDATE\s*\(", sql):
        return True, violations

    has_historical_anchor = bool(
        re.search(r"(?is)\bDATEADD\s*\(\s*YEAR\s*,\s*-10\s*,\s*GETDATE\s*\(\s*\)\s*\)", sql)
    )
    if not has_historical_anchor:
        violations.append(
            "Dynamic query uses a relative current-date window that is outside "
            "the available data timeframe; regenerate with dataset-relative date context"
        )

    return len(violations) == 0, violations


def _extract_explicit_cast_type(expr: str) -> str | None:
    """Extract explicit CAST/CONVERT target type from an expression."""
    cast_match = re.search(r"(?is)\bCAST\s*\(.*?\bAS\s+([A-Za-z0-9_]+)", expr)
    if cast_match:
        return cast_match.group(1).lower()

    convert_match = re.search(r"(?is)\bCONVERT\s*\(\s*([A-Za-z0-9_]+)\s*,", expr)
    if convert_match:
        return convert_match.group(1).lower()

    return None


def _sql_type_family(sql_type: str) -> str:
    """Normalize SQL types to broad families for compatibility checks."""
    t = sql_type.lower()
    if any(token in t for token in ("char", "text", "nchar", "nvarchar", "varchar")):
        return "string"
    if any(
        token in t
        for token in (
            "int",
            "decimal",
            "numeric",
            "float",
            "real",
            "money",
            "smallmoney",
            "bigint",
            "smallint",
            "tinyint",
        )
    ):
        return "numeric"
    if any(token in t for token in ("date", "time", "datetime", "smalldatetime")):
        return "datetime"
    if "bit" in t:
        return "boolean"
    return t


def _check_union_projection_safety(sql: str) -> tuple[bool, list[str]]:
    """Check UNION/UNION ALL projection compatibility to prevent implicit conversion errors."""
    violations: list[str] = []
    select_lists = _extract_union_select_items(sql)
    if not select_lists:
        return True, violations

    column_counts = {len(items) for items in select_lists}
    if len(column_counts) != 1:
        violations.append("UNION branches must project the same number of columns")
        return False, violations

    total_columns = len(select_lists[0])
    for index in range(total_columns):
        cast_families: set[str] = set()
        has_plain_null = False
        has_cast_expression = False
        has_non_cast_expression = False

        for branch_items in select_lists:
            expr = branch_items[index].strip()
            if re.fullmatch(r"(?is)NULL(?:\s+AS\s+\w+)?", expr):
                has_plain_null = True
                continue

            cast_type = _extract_explicit_cast_type(expr)
            if cast_type is None:
                has_non_cast_expression = True
                continue
            has_cast_expression = True
            cast_families.add(_sql_type_family(cast_type))

        col_num = index + 1
        if has_plain_null:
            violations.append(f"UNION column {col_num} uses untyped NULL; use CAST(NULL AS <type>)")
        if has_cast_expression and has_non_cast_expression:
            violations.append(
                f"UNION column {col_num} must not mix CAST/CONVERT and raw expressions"
            )
        elif has_non_cast_expression:
            violations.append(
                f"UNION column {col_num} must use explicit CAST/CONVERT in each branch"
            )
        if len(cast_families) > 1:
            families = ", ".join(sorted(cast_families))
            violations.append(
                f"UNION column {col_num} has incompatible CAST type families: {families}"
            )

    return len(violations) == 0, violations


def _check_syntax(sql: str) -> tuple[bool, list[str]]:
    """Basic syntax check for SQL query.

    This is a lightweight check — full parsing would require a SQL parser.

    Args:
        sql: The SQL query string to check.

    Returns:
        Tuple of (is_valid, list of errors).
    """
    errors: list[str] = []
    sql_stripped = sql.strip()

    if not sql_stripped:
        errors.append("Query is empty")
        return False, errors

    if sql_stripped.count("(") != sql_stripped.count(")"):
        errors.append("Unbalanced parentheses")

    single_quotes = sql_stripped.count("'")
    if single_quotes % 2 != 0:
        errors.append("Unbalanced single quotes")

    if not sql_stripped.upper().lstrip().startswith("SELECT"):
        errors.append("Query does not start with SELECT")

    return len(errors) == 0, errors


def _check_statement_type(sql: str) -> tuple[str, bool, list[str]]:
    """Check that the query is a single SELECT statement.

    Args:
        sql: The SQL query string to check.

    Returns:
        Tuple of (statement_type, is_single_statement, list of violations).
    """
    violations: list[str] = []
    sql_upper = sql.strip().upper()

    if sql_upper.startswith("SELECT"):
        statement_type = "SELECT"
    elif sql_upper.startswith("INSERT"):
        statement_type = "INSERT"
    elif sql_upper.startswith("UPDATE"):
        statement_type = "UPDATE"
    elif sql_upper.startswith("DELETE"):
        statement_type = "DELETE"
    elif sql_upper.startswith("DROP"):
        statement_type = "DROP"
    elif sql_upper.startswith("CREATE"):
        statement_type = "CREATE"
    elif sql_upper.startswith("ALTER"):
        statement_type = "ALTER"
    else:
        statement_type = "UNKNOWN"

    if statement_type != "SELECT":
        violations.append(f"Statement type is {statement_type}, must be SELECT")

    sql_trimmed = sql.strip().rstrip(";").strip()
    if ";" in sql_trimmed:
        violations.append("Multiple statements detected (semicolon found within query)")
        return statement_type, False, violations

    return statement_type, True, violations


def _check_allowlist(sql: str, allowed_tables: set[str]) -> tuple[bool, list[str], list[str]]:
    """Check that all referenced tables are in the allowlist.

    Handles table aliases properly — e.g., ``FROM Purchasing.Suppliers s``
    will recognise ``s`` as an alias, not treat ``s.SupplierName`` as a table.

    Args:
        sql: The SQL query string to check.
        allowed_tables: Set of fully-qualified table names (e.g. ``Schema.Table``).

    Returns:
        Tuple of (is_valid, violations, warnings).
    """
    violations: list[str] = []
    warnings: list[str] = []

    table_with_alias_pattern = (
        r"(?:FROM|JOIN)\s+"
        r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)"
        r"(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?"
    )

    matches = re.findall(table_with_alias_pattern, sql, re.IGNORECASE)

    aliases: set[str] = set()
    tables_found: set[str] = set()

    for table, alias in matches:
        table_normalized = table.strip()
        tables_found.add(table_normalized)

        if alias:
            aliases.add(alias.strip().upper())

    for table in tables_found:
        if "." in table:
            if not any(t.upper() == table.upper() for t in allowed_tables):
                violations.append(f"Table '{table}' is not in the allowlist")
        else:
            warnings.append(f"Table '{table}' should be fully qualified (e.g., Schema.Table)")

    return len(violations) == 0, violations, warnings


def _check_security(sql: str) -> tuple[bool, list[str]]:
    """Check for SQL injection patterns and dangerous keywords.

    Args:
        sql: The SQL query string to check.

    Returns:
        Tuple of (is_valid, list of violations).
    """
    violations: list[str] = []

    for keyword in DANGEROUS_KEYWORDS:
        pattern = r"\b" + keyword + r"\b"
        if re.search(pattern, sql, re.IGNORECASE):
            violations.append(f"Dangerous keyword detected: {keyword}")

    for pattern in SQL_INJECTION_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE):
            violations.append("Potential SQL injection pattern detected")
            break

    return len(violations) == 0, violations


def validate_query(draft: SQLDraft, allowed_tables: set[str]) -> SQLDraft:
    """Validate a SQL draft for syntax, allowlist, statement type, and security.

    Runs all validation checks and returns a new ``SQLDraft`` with
    ``query_validated=True`` and any violations/warnings populated.

    Args:
        draft: The SQL draft to validate.
        allowed_tables: Set of fully-qualified allowed table names.

    Returns:
        A new ``SQLDraft`` with validation results applied.
    """
    try:
        sql_query = draft.completed_sql or ""

        logger.info("Validating query: %s", sql_query[:200] if sql_query else "(empty)")

        all_violations: list[str] = []
        all_warnings: list[str] = []

        syntax_valid, syntax_errors = _check_syntax(sql_query)
        all_violations.extend(syntax_errors)

        statement_type, is_single_statement, statement_violations = _check_statement_type(sql_query)
        all_violations.extend(statement_violations)

        allowlist_valid, allowlist_violations, allowlist_warnings = _check_allowlist(
            sql_query, allowed_tables
        )
        all_violations.extend(allowlist_violations)
        all_warnings.extend(allowlist_warnings)

        security_valid, security_violations = _check_security(sql_query)
        all_violations.extend(security_violations)

        union_valid = True
        projection_valid = True
        historical_date_valid = True
        if draft.source == "dynamic":
            union_valid, union_violations = _check_union_projection_safety(sql_query)
            all_violations.extend(union_violations)
            projection_valid, projection_violations = _check_dynamic_projection_constraints(
                sql_query
            )
            all_violations.extend(projection_violations)
            historical_date_valid, historical_date_violations = (
                _check_dynamic_historical_date_anchor(sql_query)
            )
            all_violations.extend(historical_date_violations)

        is_valid = (
            syntax_valid
            and allowlist_valid
            and statement_type == "SELECT"
            and is_single_statement
            and security_valid
            and union_valid
            and projection_valid
            and historical_date_valid
        )

        logger.info(
            "Validation complete: valid=%s, violations=%d, warnings=%d",
            is_valid,
            len(all_violations),
            len(all_warnings),
        )

        return draft.model_copy(
            update={
                "query_validated": True,
                "query_violations": all_violations,
                "query_warnings": all_warnings,
            }
        )

    except Exception as exc:
        logger.exception("Validation error")
        return draft.model_copy(
            update={
                "query_validated": True,
                "query_violations": [f"Validation error: {exc!s}"],
                "query_warnings": [],
            }
        )
