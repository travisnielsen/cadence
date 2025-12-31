"""
Tools package for the Data Agent API.

This package contains callable tools that agents can use to interact
with databases, search services, and other external services.

Tools are decorated with @ai_function and can be passed directly
to ChatAgent.tools as callable functions.
"""

from .search import search_cached_queries
from .sql import execute_sql

__all__ = [
    # Search tools
    "search_cached_queries",
    # SQL tools
    "execute_sql",
]
