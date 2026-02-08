"""
Shared Azure SQL Database client for executing queries.

This module provides a reusable async client for executing SQL queries
against Azure SQL Database using Azure AD authentication.
"""

import logging
import os
import struct
from typing import Any

import aioodbc
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


def get_azure_sql_token() -> bytes:
    """
    Get an Azure AD token for SQL Database authentication.

    Returns:
        Token bytes formatted for pyodbc
    """
    # Use AZURE_CLIENT_ID for user-assigned managed identity in Container Apps
    # When running locally, DefaultAzureCredential will use CLI/VS Code credentials
    client_id = os.getenv("AZURE_CLIENT_ID")
    logger.info("Getting SQL token, AZURE_CLIENT_ID=%s", client_id)

    if client_id:
        credential = DefaultAzureCredential(managed_identity_client_id=client_id)
    else:
        credential = DefaultAzureCredential()

    token = credential.get_token("https://database.windows.net/.default")
    logger.info("Token acquired, expires_on=%s", token.expires_on)

    # Format token for SQL Server ODBC driver
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    return token_struct


class AzureSqlClient:
    """
    Async context manager for Azure SQL Database operations.

    Supports executing read-only SQL queries with Azure AD authentication.

    Usage:
        async with AzureSqlClient() as client:
            result = await client.execute_query("SELECT TOP 10 * FROM Sales.Orders")
    """

    # Keywords that are not allowed in queries for safety
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
    ]

    def __init__(
        self, server: str | None = None, database: str | None = None, read_only: bool = True
    ):
        """
        Initialize the SQL client.

        Args:
            server: Azure SQL server hostname. Defaults to AZURE_SQL_SERVER env var.
            database: Database name. Defaults to AZURE_SQL_DATABASE env var or 'WideWorldImporters'.
            read_only: If True, only SELECT queries are allowed.
        """
        self.server = server or os.getenv("AZURE_SQL_SERVER", "")
        self.database = database or os.getenv("AZURE_SQL_DATABASE", "WideWorldImporters")
        self.read_only = read_only
        self._connection: aioodbc.Connection | None = None

    async def __aenter__(self):
        """Establish the database connection."""
        if not self.server:
            raise ValueError("AZURE_SQL_SERVER environment variable is required")

        connection_string = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={self.server};"
            f"DATABASE={self.database};"
        )

        token_struct = get_azure_sql_token()

        self._connection = await aioodbc.connect(
            dsn=connection_string,
            attrs_before={
                1256: token_struct  # SQL_COPT_SS_ACCESS_TOKEN
            },
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close the database connection."""
        if self._connection:
            await self._connection.close()

    def validate_query(self, query: str) -> tuple[bool, str | None]:
        """
        Validate that a query is safe to execute.

        Args:
            query: The SQL query to validate

        Returns:
            Tuple of (is_valid, error_message). error_message is None if valid.
        """
        query_upper = query.strip().upper()

        if self.read_only and not query_upper.startswith("SELECT"):
            return False, "Only SELECT queries are allowed. Query must start with SELECT."

        if self.read_only:
            for keyword in self.DANGEROUS_KEYWORDS:
                if keyword in query_upper:
                    return (
                        False,
                        f"Query contains forbidden keyword: {keyword}. Only read-only SELECT queries are allowed.",
                    )

        return True, None

    async def execute_query(self, query: str) -> dict[str, Any]:
        """
        Execute a SQL query and return results.

        Args:
            query: The SQL query to execute

        Returns:
            A dictionary containing:
            - success: Whether the query executed successfully
            - columns: List of column names in the result
            - rows: List of dictionaries, one per row
            - row_count: Number of rows returned
            - error: Error message if the query failed
        """
        logger.info("Executing SQL query: %s", query[:200])

        # Validate query
        is_valid, error = self.validate_query(query)
        if not is_valid:
            return {"success": False, "error": error, "columns": [], "rows": [], "row_count": 0}

        try:
            if not self._connection:
                return {
                    "success": False,
                    "error": "Database connection not established. Use 'async with' context manager.",
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                }

            async with self._connection.cursor() as cursor:
                await cursor.execute(query)

                # Get column names
                columns = [column[0] for column in cursor.description] if cursor.description else []

                # Fetch all rows
                raw_rows = await cursor.fetchall()

                # Convert to list of dicts with JSON-safe values
                rows = []
                for row in raw_rows:
                    row_dict = {}
                    for i, col in enumerate(columns):
                        value = row[i]
                        # Convert non-JSON-serializable types
                        if value is None:
                            row_dict[col] = None
                        elif isinstance(value, (int, float, str, bool)):
                            row_dict[col] = value
                        else:
                            row_dict[col] = str(value)
                    rows.append(row_dict)

                logger.info("Query executed successfully. Returned %d rows.", len(rows))

                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "error": None,
                }

        except Exception as e:
            logger.error("SQL execution error: %s", e)
            return {"success": False, "error": str(e), "columns": [], "rows": [], "row_count": 0}
