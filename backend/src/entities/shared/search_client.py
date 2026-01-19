"""
Shared Azure AI Search client for vector/hybrid search operations.

This module provides a reusable async client for searching Azure AI Search indexes
with vector embeddings generated via Azure OpenAI.
"""

import logging
import os
import re
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AsyncAzureOpenAI

logger = logging.getLogger(__name__)


class AzureSearchClient:
    """
    Async context manager for Azure AI Search operations with vector embeddings.

    Supports hybrid search (vector + keyword) against any configured index.

    Usage:
        async with AzureSearchClient(index_name="queries") as client:
            results = await client.hybrid_search(
                query="What are the top products?",
                select=["question", "query", "reasoning"],
                top=3,
            )
    """

    def __init__(self, index_name: str, vector_field: str = "content_vector"):
        """
        Initialize the search client.

        Args:
            index_name: Name of the Azure AI Search index to query
            vector_field: Name of the vector field in the index
        """
        self.index_name = index_name
        self.endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
        self.vector_field = vector_field
        self._credential: DefaultAzureCredential | None = None
        self._search_client: SearchClient | None = None
        self._openai_client: AsyncAzureOpenAI | None = None

        # Parse AI project endpoint for embeddings
        project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
        match = re.match(r'(https://[^/]+)', project_endpoint)
        self._ai_base_endpoint = match.group(1) if match else ""
        self._embedding_deployment = os.getenv("AZURE_AI_EMBEDDING_DEPLOYMENT", "embedding-small")

    async def __aenter__(self):
        """Set up the search client and credentials."""
        if not self.endpoint:
            raise ValueError("AZURE_SEARCH_ENDPOINT environment variable is required")

        # Use AZURE_CLIENT_ID for user-assigned managed identity in Container Apps
        client_id = os.getenv("AZURE_CLIENT_ID")
        if client_id:
            self._credential = DefaultAzureCredential(managed_identity_client_id=client_id)
        else:
            self._credential = DefaultAzureCredential()
        
        self._search_client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=self._credential,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up resources."""
        if self._search_client:
            await self._search_client.close()
        if self._openai_client:
            await self._openai_client.close()
        if self._credential:
            await self._credential.close()

    async def get_embeddings(self, text: str) -> list[float] | None:
        """
        Generate embeddings for the given text using Azure OpenAI.

        Args:
            text: The text to generate embeddings for

        Returns:
            List of embedding floats, or None if embedding generation fails
        """
        if not self._ai_base_endpoint:
            logger.warning("No AI endpoint configured for embeddings")
            return None

        try:
            assert self._credential is not None, "Client not initialized"
            token = await self._credential.get_token("https://cognitiveservices.azure.com/.default")

            if self._openai_client is None:
                self._openai_client = AsyncAzureOpenAI(
                    azure_endpoint=self._ai_base_endpoint,
                    azure_ad_token=token.token,
                    api_version="2024-06-01",
                )

            response = await self._openai_client.embeddings.create(
                model=self._embedding_deployment,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning("Failed to get embeddings: %s", e)
            return None

    async def hybrid_search(
        self,
        query: str,
        select: list[str],
        top: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Execute hybrid (vector + keyword) search.

        Args:
            query: The search query text
            select: List of fields to return in results
            top: Maximum number of results to return

        Returns:
            List of result dictionaries with selected fields and score
        """
        assert self._search_client is not None, "Client not initialized"

        embeddings = await self.get_embeddings(query)
        if embeddings is None:
            raise RuntimeError("Failed to generate embeddings")

        vector_query = VectorizedQuery(
            vector=embeddings,
            k_nearest_neighbors=top,
            fields=self.vector_field,
        )

        results = await self._search_client.search(
            search_text=query,
            vector_queries=[vector_query],
            select=select,
            top=top,
        )

        matches = []
        async for result in results:
            match = {field: result.get(field, "") for field in select}
            match["score"] = result.get("@search.score", 0)
            matches.append(match)

        return matches

    async def vector_search(
        self,
        query: str,
        select: list[str],
        top: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Execute pure vector search (no keyword matching).

        Uses cosine similarity scoring which provides more discriminative
        scores compared to hybrid RRF scoring, making it easier to detect
        ambiguous matches.

        Args:
            query: The search query text
            select: List of fields to return in results
            top: Maximum number of results to return

        Returns:
            List of result dictionaries with selected fields and score.
            Scores are cosine similarity values (0.0 to 1.0 range).
        """
        assert self._search_client is not None, "Client not initialized"

        embeddings = await self.get_embeddings(query)
        if embeddings is None:
            raise RuntimeError("Failed to generate embeddings")

        vector_query = VectorizedQuery(
            vector=embeddings,
            k_nearest_neighbors=top,
            fields=self.vector_field,
        )

        results = await self._search_client.search(
            search_text=None,
            vector_queries=[vector_query],
            select=select,
            top=top,
        )

        matches = []
        async for result in results:
            match = {field: result.get(field, "") for field in select}
            # @search.score for pure vector search is cosine similarity (0-1 range)
            match["score"] = result.get("@search.score", 0)
            matches.append(match)

        return matches
