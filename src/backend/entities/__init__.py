"""
Entities package.

Each subdirectory represents an agent executor:
- orchestrator/: ConversationOrchestrator for chat sessions and intent classification
- nl2sql_controller/: NL2SQL controller for database queries
- parameter_extractor/: Extracts parameters from user queries
- parameter_validator/: Validates extracted parameter values
- query_builder/: Generates dynamic SQL from table metadata
- query_validator/: Validates SQL queries before execution
- workflow/: NL2SQL workflow combining the query processing components

Shared models are available at the package level.
"""

from models import NL2SQLResponse

__all__ = ["NL2SQLResponse"]
