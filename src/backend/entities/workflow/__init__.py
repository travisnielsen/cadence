"""
NL2SQL Workflow - For processing data queries.

The ConversationOrchestrator (in orchestrator/) handles user-facing chat,
intent classification, and refinements. It invokes this workflow for
data query processing.

Two entry points are available:

* **Legacy (MAF workflow):** ``create_nl2sql_workflow`` / ``get_workflow``
  build a full Microsoft Agent Framework workflow graph.  These will be
  removed in Phase 6.
* **Pipeline (new):** ``process_query`` is a plain async function that
  replaces the MAF executor graph.  ``PipelineClients`` /
  ``create_pipeline_clients`` provide dependency injection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_framework import Workflow
from entities.nl2sql_controller.executor import NL2SQLController

from .clients import PipelineClients, create_pipeline_clients
from .workflow import create_nl2sql_workflow

if TYPE_CHECKING:
    from entities.nl2sql_controller.pipeline import process_query as process_query


def get_workflow() -> tuple[Workflow, NL2SQLController]:
    """Get a fresh NL2SQL workflow.

    Returns:
        Tuple of (workflow, nl2sql_controller).
    """
    return create_nl2sql_workflow()


def __getattr__(name: str) -> object:
    """Lazily import ``process_query`` to avoid circular imports."""
    if name == "process_query":
        from entities.nl2sql_controller.pipeline import process_query  # noqa: PLC0415

        return process_query
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PipelineClients",
    "create_nl2sql_workflow",
    "create_pipeline_clients",
    "get_workflow",
    "process_query",
]
