"""
Table metadata search tool using Azure AI Search.

Searches the tables index to find relevant tables for dynamic query generation
when no matching query template is found.
"""

import logging
import os
from typing import Any

from agent_framework import tool
from entities.shared.search_client import AzureSearchClient
from models import TableColumn, TableMetadata

logger = logging.getLogger(__name__)

# Minimum score threshold for table search results
# Using a lower threshold since queries often need multiple tables
DEFAULT_TABLE_SCORE_THRESHOLD = float(os.getenv("TABLE_SEARCH_THRESHOLD", "0.03"))


def _hydrate_table_metadata(raw_result: dict[str, Any]) -> TableMetadata:
    """
    Convert a raw search result into a hydrated TableMetadata object.

    Args:
        raw_result: Dictionary from Azure AI Search

    Returns:
        TableMetadata with parsed columns
    """
    # Parse columns from the result
    raw_columns = raw_result.get("columns", [])
    columns = [
        TableColumn(name=col.get("name", ""), description=col.get("description", ""))
        for col in raw_columns
        if isinstance(col, dict)
    ]

    return TableMetadata(
        id=raw_result.get("id", ""),
        table=raw_result.get("table", ""),
        datasource=raw_result.get("datasource", ""),
        description=raw_result.get("description", ""),
        columns=columns,
        score=raw_result.get("score", 0.0),
    )


@tool
async def search_tables(user_question: str) -> dict[str, Any]:
    """
    Search for relevant database tables based on the user's question.

    This function searches the tables index using hybrid search to find
    tables whose descriptions match the user's intent. This is used for
    dynamic query generation when no pre-defined template matches.

    Args:
        user_question: The user's natural language question about the data

    Returns:
        A dictionary containing:
        - has_matches: Whether any tables met the score threshold
        - tables: List of matching TableMetadata objects (hydrated)
        - table_count: Number of tables returned
        - message: Status message explaining the result
    """
    logger.info("Searching tables for: %s", user_question[:100])

    # Emit step start event for UI progress
    step_name = "Finding relevant tables"
    emit_step_end_fn = None
    try:
        from api.step_events import emit_step_end, emit_step_start  # noqa: PLC0415

        emit_step_start(step_name)
        emit_step_end_fn = emit_step_end
    except ImportError:
        pass  # Step events not available

    def finish_step() -> None:
        if emit_step_end_fn:
            emit_step_end_fn(step_name)

    try:
        async with AzureSearchClient(index_name="tables", vector_field="content_vector") as client:
            # Use hybrid search combining vector similarity and keyword matching
            results = await client.hybrid_search(
                query=user_question,
                select=[
                    "id",
                    "table",
                    "datasource",
                    "description",
                    "columns",
                ],
                top=5,  # Return up to 5 tables for complex queries
            )

        if not results:
            finish_step()
            return {
                "has_matches": False,
                "tables": [],
                "table_count": 0,
                "message": "No tables found matching the query",
            }

        # Hydrate results into TableMetadata objects
        hydrated_tables = [_hydrate_table_metadata(r) for r in results]

        # Filter by score threshold
        matching_tables = [t for t in hydrated_tables if t.score >= DEFAULT_TABLE_SCORE_THRESHOLD]

        if not matching_tables:
            finish_step()
            return {
                "has_matches": False,
                "tables": [],
                "table_count": 0,
                "score_threshold": DEFAULT_TABLE_SCORE_THRESHOLD,
                "best_score": hydrated_tables[0].score if hydrated_tables else 0.0,
                "message": f"No tables met the score threshold ({DEFAULT_TABLE_SCORE_THRESHOLD})",
            }

        logger.info(
            "Table search: %d tables above threshold (%.3f). Tables: %s",
            len(matching_tables),
            DEFAULT_TABLE_SCORE_THRESHOLD,
            [t.table for t in matching_tables],
        )

        finish_step()
        return {
            "has_matches": True,
            "tables": [t.model_dump() for t in matching_tables],
            "table_count": len(matching_tables),
            "score_threshold": DEFAULT_TABLE_SCORE_THRESHOLD,
            "message": f"Found {len(matching_tables)} relevant table(s)",
        }

    except Exception as e:
        logger.exception("Error searching tables")
        finish_step()
        return {
            "has_matches": False,
            "tables": [],
            "table_count": 0,
            "error": str(e),
            "message": f"Error: {e}",
        }
