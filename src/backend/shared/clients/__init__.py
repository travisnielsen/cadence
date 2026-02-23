"""Shared clients for Azure services."""

from .search_client import AzureSearchClient
from .sql_client import AzureSqlClient

__all__ = ["AzureSearchClient", "AzureSqlClient"]
