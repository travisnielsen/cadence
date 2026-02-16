"""
Shared tools for AI agents.

Provides AI-callable functions for:
- Searching cached SQL queries
- Searching query templates for parameterized queries
- Searching table metadata for dynamic query generation
- Executing SQL against the database
"""

from .search import search_cached_queries
from .sql import execute_query_parameterized, execute_sql
from .table_search import search_tables
from .template_search import search_query_templates

__all__ = [
    "execute_query_parameterized",
    "execute_sql",
    "search_cached_queries",
    "search_query_templates",
    "search_tables",
]
