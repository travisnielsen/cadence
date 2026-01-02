"""
Search tools for the NL2SQL agent.

This module contains tools for searching Azure AI Search indexes
to find cached queries and other search-related functionality.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from agent_framework import ai_function

from ...util import AzureSearchClient

logger = logging.getLogger(__name__)

# Index names from environment
SEARCH_INDEX_QUERIES = os.getenv("AZURE_SEARCH_INDEX_QUERIES", "queries")


@ai_function(
    name="search_cached_queries",
    description=(
        "Search for similar questions in the cached queries database using semantic search. "
        "This tool finds previously answered questions that are semantically similar to the user's question. "
        "If a high-confidence match is found, you SHOULD use the returned SQL query directly "
        "with the execute_sql tool instead of generating a new query. This improves accuracy and performance. "
        "Always call this tool FIRST when receiving a new user question about data."
    ),
)
async def search_cached_queries(
    question: Annotated[str, "The user's natural language question to search for similar cached queries."],
    min_score: Annotated[float, "Minimum vector similarity score (0-1) to consider a match high-confidence. Default is 0.85."] = 0.85,
) -> dict[str, Any]:
    """
    Search for similar questions in the queries index using semantic/hybrid search.
    
    Uses Azure AI Search with vector embeddings to find semantically similar
    questions that have been previously answered with SQL queries.
    
    The search uses hybrid (vector + keyword) for best retrieval quality, but
    calculates confidence using pure vector similarity scores (0-1 range) for
    more meaningful thresholds.
    
    Args:
        question: The user's natural language question
        min_score: Minimum vector similarity score (0-1) to consider a high-confidence match (default: 0.85)
        
    Returns:
        A dictionary containing:
        - success: bool indicating if the search executed successfully
        - matches: list of matching cached queries with scores (if successful)
        - best_match: the highest scoring match (if any meet min_score threshold)
        - has_high_confidence_match: bool indicating if best_match score >= min_score
        - error: error message (if failed)
    """
    top_k = 3  # Fixed value for simplicity
    logger.info("="*60)
    logger.info("SEARCH_CACHED_QUERIES TOOL CALLED")
    logger.info("Question: %s", question)
    logger.info("Parameters: top_k=%d, min_score=%.2f", top_k, min_score)
    logger.info("Search index: %s", SEARCH_INDEX_QUERIES)
    logger.info("="*60)
    
    try:
        async with AzureSearchClient(index_name=SEARCH_INDEX_QUERIES) as client:
            # Generate embeddings once for reuse
            embeddings = await client.get_embeddings(question)
            
            # Use hybrid search for best retrieval quality (combines vector + keyword)
            matches = await client.hybrid_search(
                query=question,
                select=["question", "query", "reasoning", "datasource"],
                top=top_k,
                embeddings=embeddings,
            )
            
            # Use pure vector search to get meaningful similarity scores (0-1 range)
            # Hybrid search uses RRF which compresses scores to ~0.01-0.03 range
            vector_matches = await client.vector_search(
                query=question,
                select=["question"],
                top=top_k,
                embeddings=embeddings,
            )
            
            # Create a lookup of vector scores by question text
            vector_scores = {m.get("question", ""): m.get("score", 0) for m in vector_matches}
        
        # Determine best match using vector similarity score for confidence
        has_high_confidence = False
        best_match_result = None
        vector_score = 0.0
        rrf_score = 0.0
        
        if matches:
            best_match_result = matches[0]
            best_question = best_match_result.get("question", "")
            
            # Get the vector similarity score for the best match
            vector_score = float(vector_scores.get(best_question, 0))
            rrf_score = float(best_match_result.get("score", 0))
            
            # Add vector score to the result for transparency
            best_match_result["vector_score"] = vector_score
            best_match_result["rrf_score"] = rrf_score
            
            # Use vector score for confidence determination (0-1 range is more meaningful)
            has_high_confidence = vector_score >= min_score
        
        # Add vector scores to all matches for logging
        for match in matches:
            match_question = match.get("question", "")
            match["vector_score"] = vector_scores.get(match_question, 0)
            match["rrf_score"] = match.get("score", 0)
        
        # Log results
        logger.info("-"*60)
        logger.info("SEARCH RESULTS:")
        logger.info("Total matches found: %d", len(matches))
        for i, match in enumerate(matches):
            logger.info("  Match %d:", i + 1)
            logger.info("    Vector Score: %.4f", match.get("vector_score", 0))
            logger.info("    RRF Score: %.4f", match.get("rrf_score", 0))
            logger.info("    Question: %s", match.get("question", "")[:100])
            logger.info("    Query: %s", match.get("query", "")[:100])
        if best_match_result:
            logger.info("Best match vector score: %.4f (threshold: %.2f)", vector_score, min_score)
            logger.info("High confidence match: %s", has_high_confidence)
        else:
            logger.info("No matches found")
        logger.info("-"*60)
        
        return {
            "success": True,
            "matches": matches,
            "best_match": best_match_result,
            "has_high_confidence_match": has_high_confidence,
            "confidence_score": vector_score,
            "question_searched": question,
        }
        
    except ValueError as e:
        # Configuration error (missing endpoint)
        logger.error("Configuration error: %s", e)
        return {
            "success": False,
            "error": str(e),
        }
    except ImportError as e:
        logger.error("Missing package for search: %s", e)
        raise RuntimeError(
            "azure-search-documents package is not installed. Install with: pip install azure-search-documents"
        ) from e
    except Exception as e:
        logger.exception("Search error: %s", e)
        # Re-raise to signal failure to the agent framework
        raise RuntimeError(
            f"Search failed: {type(e).__name__}: {str(e)}. "
            "Check Azure Search permissions (Search Index Data Reader role required)."
        ) from e

