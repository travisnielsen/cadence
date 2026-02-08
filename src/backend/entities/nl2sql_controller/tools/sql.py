"""
SQL execution tool for the data agent.

Provides an AI-callable function for executing read-only SQL queries.
"""

import logging
from typing import Any

from agent_framework import tool
from entities.shared import AzureSqlClient

logger = logging.getLogger(__name__)


@tool
async def execute_sql(query: str) -> dict[str, Any]:
    """
    Execute a read-only SQL SELECT query against the Wide World Importers database.

    This function connects to Azure SQL Database using Azure AD authentication
    and executes the provided query. Only SELECT queries are allowed for safety.

    Args:
        query: A SQL SELECT query to execute. Must be read-only (SELECT only).

    Returns:
        A dictionary containing:
        - success: Whether the query executed successfully
        - columns: List of column names in the result
        - rows: List of dictionaries, one per row
        - row_count: Number of rows returned
        - error: Error message if the query failed
    """
    # Emit step start event for UI progress
    step_name = "Executing SQL query..."
    emit_step_end_fn = None
    try:
        from api.step_events import emit_step_end, emit_step_start

        emit_step_start(step_name)
        emit_step_end_fn = emit_step_end
    except ImportError:
        pass  # Step events not available (e.g., running outside API context)

    def finish_step():
        if emit_step_end_fn:
            emit_step_end_fn(step_name)

    try:
        async with AzureSqlClient(read_only=True) as client:
            result = await client.execute_query(query)
            finish_step()
            return result
    except Exception as e:
        logger.error("SQL execution error: %s", e)
        finish_step()
        return {"success": False, "error": str(e), "columns": [], "rows": [], "row_count": 0}
