"""
SQL execution tools for the NL2SQL agent.

This module contains tools for executing SQL queries against
Azure SQL Database using Azure AD authentication.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from agent_framework import ai_function
from azure.identity.aio import DefaultAzureCredential

logger = logging.getLogger(__name__)

# SQL Server connection settings
SQL_SERVER = os.getenv("AZURE_SQL_SERVER", "")  # e.g., "myserver.database.windows.net"
SQL_DATABASE = os.getenv("AZURE_SQL_DATABASE", "WideWorldImportersStd")


@ai_function(
    name="execute_sql",
    description="Execute a read-only SQL SELECT query against the Wide World Importers database. Returns the query results as a list of rows. Only SELECT queries are allowed for safety.",
)
async def execute_sql(
    query: Annotated[str, "The SQL SELECT query to execute. Must be a read-only query. Example: SELECT TOP 10 * FROM SalesLT.Customer"],
) -> dict[str, Any]:
    """
    Execute a SQL query against Azure SQL Database.
    
    Uses Azure AD authentication via DefaultAzureCredential.
    
    Args:
        query: The SQL query to execute. Should be a SELECT query for safety.
        
    Returns:
        A dictionary containing:
        - success: bool indicating if the query executed successfully
        - columns: list of column names (if successful)
        - rows: list of row data as dictionaries (if successful)
        - row_count: number of rows returned (if successful)
        - error: error message (if failed)
    """
    logger.info("="*60)
    logger.info("EXECUTE_SQL TOOL CALLED")
    logger.info("Server: %s", SQL_SERVER)
    logger.info("Database: %s", SQL_DATABASE)
    logger.info("Query:\n%s", query)
    logger.info("="*60)
    
    # Validate query is read-only (basic check)
    query_upper = query.strip().upper()
    if not query_upper.startswith("SELECT"):
        # Allow WITH (CTE) followed by SELECT
        if not (query_upper.startswith("WITH") and "SELECT" in query_upper):
            return {
                "success": False,
                "error": "Only SELECT queries are allowed for safety. Query must start with SELECT or WITH...SELECT.",
            }
    
    # Check for dangerous keywords
    dangerous_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "EXEC", "EXECUTE"]
    for keyword in dangerous_keywords:
        if f" {keyword} " in f" {query_upper} " or query_upper.startswith(keyword):
            return {
                "success": False,
                "error": f"Query contains forbidden keyword: {keyword}. Only read-only SELECT queries are allowed.",
            }
    
    if not SQL_SERVER:
        return {
            "success": False,
            "error": "AZURE_SQL_SERVER environment variable is not configured.",
        }
    
    credential = None
    try:
        # Import aioodbc for async database access
        import aioodbc
        
        # Get access token for Azure SQL
        credential = DefaultAzureCredential()
        token = await credential.get_token("https://database.windows.net/.default")
        
        # Build connection string with access token
        # For Azure SQL with AAD token auth
        connection_string = (
            f"Driver={{ODBC Driver 18 for SQL Server}};"
            f"Server=tcp:{SQL_SERVER},1433;"
            f"Database={SQL_DATABASE};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
        )
        
        # Connect with access token
        async with aioodbc.connect(
            dsn=connection_string,
            attrs_before={
                # SQL_COPT_SS_ACCESS_TOKEN = 1256
                1256: _create_access_token_struct(token.token)
            }
        ) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query)
                
                # Get column names
                columns = [column[0] for column in cursor.description] if cursor.description else []
                
                # Fetch all rows
                rows_raw = await cursor.fetchall()
                
                # Convert to list of dictionaries - handle non-serializable types
                rows = []
                for row in rows_raw:
                    row_dict = {}
                    for col, val in zip(columns, row):
                        # Convert non-serializable types to strings
                        if val is None:
                            row_dict[col] = None
                        elif isinstance(val, (int, float, str, bool)):
                            row_dict[col] = val
                        else:
                            # Convert datetime, decimal, bytes, etc. to string
                            row_dict[col] = str(val)
                    rows.append(row_dict)
                
                # Limit results for safety
                max_rows = 100
                if len(rows) > max_rows:
                    rows = rows[:max_rows]
                    truncated = True
                else:
                    truncated = False
                
                # Log results
                logger.info("-"*60)
                logger.info("SQL EXECUTION SUCCESSFUL")
                logger.info("Columns: %s", columns)
                logger.info("Rows returned: %d", len(rows))
                if truncated:
                    logger.info("Results truncated to %d rows", max_rows)
                if rows:
                    logger.info("First row: %s", rows[0])
                logger.info("-"*60)
                
                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": truncated,
                    "max_rows": max_rows if truncated else None,
                }
    
    except ImportError as e:
        logger.exception("Missing aioodbc package: %s", e)
        raise RuntimeError(
            "aioodbc package is not installed. Install with: pip install aioodbc"
        ) from e
    except Exception as e:
        logger.exception("SQL execution error: %s", e)
        raise RuntimeError(
            f"SQL execution failed: {type(e).__name__}: {str(e)}"
        ) from e
    finally:
        # Ensure credential is closed to avoid unclosed client session
        if credential is not None:
            await credential.close()


def _create_access_token_struct(token: str) -> bytes:
    """
    Create the access token struct required by ODBC driver for Azure AD auth.
    
    The token must be encoded as a UTF-16-LE byte array with a length prefix.
    """
    import struct
    
    # Encode token as UTF-16-LE
    token_bytes = token.encode("UTF-16-LE")
    
    # Create struct: length (4 bytes) + token bytes
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


# Note: The tool is now an AIFunction via the @ai_function decorator above.
# The function itself (execute_sql) can be passed directly to ChatAgent.tools
