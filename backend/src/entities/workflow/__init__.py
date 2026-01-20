"""
NL2SQL Workflow - For processing data queries.

This module exports the NL2SQL workflow for DevUI auto-discovery.

The ConversationOrchestrator (in orchestrator/) handles user-facing chat,
intent classification, and refinements. It invokes this workflow for
data query processing.

The workflow:
1. NL2SQLController receives data questions
2. Routes to ParameterExtractor or QueryBuilder as needed
3. Validates and executes SQL
4. Returns structured results

Usage with DevUI:
    devui ./src/entities
"""

from .workflow import workflow, nl2sql_controller, nl2sql_client, create_nl2sql_workflow


def get_workflow():
    """
    Get the NL2SQL workflow.

    Returns:
        Tuple of (workflow, nl2sql_controller, nl2sql_client)
    """
    return workflow, nl2sql_controller, nl2sql_client


# Export for programmatic access and DevUI discovery
__all__ = ["workflow", "nl2sql_controller", "nl2sql_client", "get_workflow", "create_nl2sql_workflow"]

