"""
Tools for the data agent.

Provides AI-callable functions for:
- Searching cached SQL queries
- Searching query templates for parameterized queries
- Executing SQL against the database
"""

from .search import search_cached_queries
from .sql import execute_sql
from .template_search import search_query_templates

__all__ = ["search_cached_queries", "search_query_templates", "execute_sql"]
