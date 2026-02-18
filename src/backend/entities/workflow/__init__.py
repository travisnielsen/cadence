"""
NL2SQL Workflow - For processing data queries.

The DataAssistant (in assistant/) handles user-facing chat,
intent classification, and refinements. It invokes the pipeline
for data query processing.

Entry point: ``process_query`` is a plain async function.
``PipelineClients`` / ``create_pipeline_clients`` provide dependency injection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .clients import PipelineClients, create_pipeline_clients

if TYPE_CHECKING:
    from entities.nl2sql_controller.pipeline import process_query as process_query


def __getattr__(name: str) -> object:
    """Lazily import ``process_query`` to avoid circular imports."""
    if name == "process_query":
        from entities.nl2sql_controller.pipeline import process_query  # noqa: PLC0415

        return process_query
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PipelineClients",
    "create_pipeline_clients",
    "process_query",
]
