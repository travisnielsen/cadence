"""
NL2SQL Controller - Orchestrates NL2SQL query processing.

The controller:
1. Searches for query templates matching user questions
2. Extracts parameters from natural language
3. Executes SQL against the Wide World Importers database
4. Returns structured results
"""

from .executor import NL2SQLController

__all__ = ["NL2SQLController"]
