"""NL2SQL Controller - Orchestrates NL2SQL query processing.

The pipeline:
1. Searches for query templates matching user questions
2. Extracts parameters from natural language
3. Validates parameters and SQL
4. Executes SQL against the Wide World Importers database
5. Returns structured results
"""

from .pipeline import process_query

__all__ = ["process_query"]
