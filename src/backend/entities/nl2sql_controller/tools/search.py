"""
Cached query search tool using Azure AI Search.
"""

import logging
import os
from typing import Any

from agent_framework import tool
from entities.shared.search_client import AzureSearchClient

logger = logging.getLogger(__name__)

# Confidence threshold for using cached queries
CONFIDENCE_THRESHOLD = float(os.getenv("QUERY_CONFIDENCE_THRESHOLD", "0.75"))


@tool
async def search_cached_queries(user_question: str) -> dict[str, Any]:
    """
    Search for pre-tested SQL queries that match the user's question.

    This function uses semantic search to find previously validated SQL queries
    that answer similar questions. If a high-confidence match is found,
    the cached query should be used instead of generating a new one.

    Args:
        user_question: The user's natural language question about the data

    Returns:
        A dictionary containing:
        - has_high_confidence_match: Whether a cached query above threshold was found
        - best_match: The best matching cached query (if any)
        - all_matches: All matches with their scores
    """
    logger.info("Searching cached queries for: %s", user_question[:100])

    # Emit step start event for UI progress
    step_name = "Searching cached queries..."
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
        async with AzureSearchClient(index_name="queries", vector_field="content_vector") as client:
            results = await client.hybrid_search(
                query=user_question,
                select=["question", "query", "reasoning"],
                top=3,
            )

        if not results:
            finish_step()
            return {
                "has_high_confidence_match": False,
                "best_match": None,
                "all_matches": [],
                "message": "No cached queries found",
            }

        best_match = results[0]
        has_high_confidence = best_match["score"] >= CONFIDENCE_THRESHOLD

        logger.info(
            "Found %d cached queries. Best score: %.3f (threshold: %.2f)",
            len(results),
            best_match["score"],
            CONFIDENCE_THRESHOLD,
        )

        finish_step()

    except Exception as e:
        logger.exception("Error searching cached queries")
        finish_step()
        return {
            "has_high_confidence_match": False,
            "best_match": None,
            "all_matches": [],
            "error": str(e),
        }
    else:
        return {
            "has_high_confidence_match": has_high_confidence,
            "best_match": best_match if has_high_confidence else None,
            "all_matches": results,
            "threshold": CONFIDENCE_THRESHOLD,
        }
