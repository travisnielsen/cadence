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
from azure.identity.aio import DefaultAzureCredential

logger = logging.getLogger(__name__)

# Azure AI Search settings (connected to AI Foundry)
SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")  # e.g., "https://myservice.search.windows.net"
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
    min_score: Annotated[float, "Minimum RRF score to consider a match high-confidence. Default is 0.03 (good match for hybrid search)."] = 0.03,
) -> dict[str, Any]:
    """
    Search for similar questions in the queries index using semantic/hybrid search.
    
    Uses Azure AI Search with vector embeddings to find semantically similar
    questions that have been previously answered with SQL queries.
    
    Args:
        question: The user's natural language question
        min_score: Minimum semantic similarity score (0-1) to consider a match (default: 0.7)
        
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
    logger.info("Search endpoint: %s", SEARCH_ENDPOINT)
    logger.info("Search index: %s", SEARCH_INDEX_QUERIES)
    logger.info("="*60)
    
    if not SEARCH_ENDPOINT:
        return {
            "success": False,
            "error": "AZURE_SEARCH_ENDPOINT environment variable is not configured.",
        }
    
    credential = None
    try:
        from azure.search.documents.aio import SearchClient
        from azure.search.documents.models import VectorizedQuery
        
        credential = DefaultAzureCredential()
        
        # First, we need to get embeddings for the question
        # We'll use the Azure OpenAI embedding model via the AI Foundry endpoint
        embeddings = await _get_embeddings(question, credential)
        
        if embeddings is None:
            raise RuntimeError(
                "Failed to generate embeddings for semantic search. "
                "Check AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_EMBEDDING_DEPLOYMENT configuration."
            )
        
        # Create search client
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=SEARCH_INDEX_QUERIES,
            credential=credential,
        )
        
        async with search_client:
            # Create vector query for semantic search
            vector_query = VectorizedQuery(
                vector=embeddings,
                k_nearest_neighbors=top_k,
                fields="content_vector",
            )
            
            # Execute hybrid search (vector + keyword)
            results = await search_client.search(
                search_text=question,  # Keyword component
                vector_queries=[vector_query],  # Vector component
                select=["question", "query", "reasoning", "datasource"],
                top=top_k,
            )
            
            matches = []
            async for result in results:
                match = {
                    "question": result.get("question", ""),
                    "query": result.get("query", ""),
                    "reasoning": result.get("reasoning", ""),
                    "datasource": result.get("datasource", ""),
                    "score": result.get("@search.score", 0),
                }
                matches.append(match)
        
        # Determine best match
        has_high_confidence = False
        best_match_result = None
        
        if matches:
            best_match_result = matches[0]
            # Azure AI Search hybrid search uses Reciprocal Rank Fusion (RRF) scoring
            # RRF scores are typically in range 0.01-0.05, NOT 0-1
            # A score >= 0.03 indicates a strong semantic match
            score = float(best_match_result.get("score", 0))
            has_high_confidence = score >= min_score
        
        # Log results
        logger.info("-"*60)
        logger.info("SEARCH RESULTS:")
        logger.info("Total matches found: %d", len(matches))
        for i, match in enumerate(matches):
            logger.info("  Match %d:", i + 1)
            logger.info("    Score: %.4f", match.get("score", 0))
            logger.info("    Question: %s", match.get("question", "")[:100])
            logger.info("    Query: %s", match.get("query", "")[:100])
        if best_match_result:
            logger.info("Best match score: %.4f (threshold: %.2f)", score, min_score)
            logger.info("High confidence match: %s", has_high_confidence)
        else:
            logger.info("No matches found")
        logger.info("-"*60)
        
        return {
            "success": True,
            "matches": matches,
            "best_match": best_match_result,
            "has_high_confidence_match": has_high_confidence,
            "question_searched": question,
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
    finally:
        # Ensure credential is closed to avoid unclosed client session
        if credential is not None:
            await credential.close()


async def _get_embeddings(text: str, credential: DefaultAzureCredential) -> list[float] | None:
    """
    Get embeddings for text using Azure OpenAI via AI Foundry.
    
    Uses the embedding model deployed in the AI Foundry project.
    Note: Caller is responsible for closing the credential.
    """
    client = None
    try:
        from openai import AsyncAzureOpenAI
        
        # Use the AI Services endpoint for embeddings (not the project endpoint)
        # The project endpoint is for the Agents API, but embeddings use the OpenAI API
        project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
        embedding_model = os.getenv("AZURE_AI_EMBEDDING_DEPLOYMENT", "embedding-small")
        
        if not project_endpoint:
            logger.warning("AZURE_AI_PROJECT_ENDPOINT not set, cannot generate embeddings")
            return None
        
        # Extract the AI Services base endpoint from the project endpoint
        # e.g., https://aif-heinar-de2qq.services.ai.azure.com/api/projects/dataexplorer
        # becomes https://aif-heinar-de2qq.services.ai.azure.com
        import re
        match = re.match(r'(https://[^/]+)', project_endpoint)
        if not match:
            logger.warning("Could not extract base endpoint from AZURE_AI_PROJECT_ENDPOINT")
            return None
        base_endpoint = match.group(1)
        
        # Get token for Azure Cognitive Services
        token = await credential.get_token("https://cognitiveservices.azure.com/.default")
        
        # Create OpenAI client with bearer token auth
        client = AsyncAzureOpenAI(
            azure_endpoint=base_endpoint,
            azure_ad_token=token.token,
            api_version="2024-06-01",
        )
        
        response = await client.embeddings.create(
            model=embedding_model,
            input=text,
        )
        
        return response.data[0].embedding
        
    except Exception as e:
        logger.warning("Failed to get embeddings: %s", e)
        return None
    finally:
        if client is not None:
            await client.close()


async def _keyword_search(
    question: str,
    top_k: int,
    min_score: float,
    credential: DefaultAzureCredential,
) -> dict[str, Any]:
    """
    Fallback keyword search when vector search is not available.
    Note: Caller is responsible for closing the credential.
    """
    try:
        from azure.search.documents.aio import SearchClient
        
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=SEARCH_INDEX_QUERIES,
            credential=credential,
        )
        
        async with search_client:
            results = await search_client.search(
                search_text=question,
                select=["question", "query", "reasoning", "datasource"],
                top=top_k,
            )
            
            matches = []
            async for result in results:
                match = {
                    "question": result.get("question", ""),
                    "query": result.get("query", ""),
                    "reasoning": result.get("reasoning", ""),
                    "datasource": result.get("datasource", ""),
                    "score": result.get("@search.score", 0),
                }
                matches.append(match)
        
        has_high_confidence = False
        best_match_result = None
        
        if matches:
            best_match_result = matches[0]
            # For keyword search, normalize score differently
            # BM25 scores are typically 0-20+, so we normalize
            score = float(best_match_result.get("score", 0))
            normalized_score = min(score / 10.0, 1.0)
            best_match_result["normalized_score"] = normalized_score
            has_high_confidence = normalized_score >= min_score
        
        return {
            "success": True,
            "matches": matches,
            "best_match": best_match_result,
            "has_high_confidence_match": has_high_confidence,
            "question_searched": question,
            "search_type": "keyword",
        }
        
    except Exception as e:
        logger.error("Keyword search error: %s", e)
        return {
            "success": False,
            "error": f"{type(e).__name__}: {str(e)}",
        }


# Note: The tool is now an AIFunction via the @ai_function decorator above.
# The function itself (search_cached_queries) can be passed directly to ChatAgent.tools
