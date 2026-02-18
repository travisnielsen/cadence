"""
NL2SQL Workflow - For processing data queries.

The ConversationOrchestrator (in orchestrator/) handles user-facing chat,
intent classification, and refinements. It invokes this workflow for
data query processing.

The workflow:
1. NL2SQLController receives data questions
2. Routes to ParameterExtractor or QueryBuilder as needed
3. Validates and executes SQL
4. Returns structured results
"""

from agent_framework import Workflow
from entities.nl2sql_controller.executor import NL2SQLController

from .workflow import create_nl2sql_workflow


def get_workflow() -> tuple[Workflow, NL2SQLController]:
    """
    Get a fresh NL2SQL workflow.

    Returns:
        Tuple of (workflow, nl2sql_controller)
    """
    return create_nl2sql_workflow()


__all__ = ["create_nl2sql_workflow", "get_workflow"]
