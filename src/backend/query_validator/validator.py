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

        is_valid = (
            syntax_valid
            and allowlist_valid
            and statement_type == "SELECT"
            and is_single_statement
            and security_valid
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
