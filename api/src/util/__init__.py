"""
Utility modules for the Data Agent API.

This package contains reusable utilities and clients that can be
shared across different parts of the application.
"""

from .search_client import AzureSearchClient

__all__ = [
    "AzureSearchClient",
]
