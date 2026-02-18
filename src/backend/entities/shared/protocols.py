"""Protocol interfaces for I/O boundaries.

These protocols enable dependency injection for testability.
Production implementations wrap Azure clients; test fakes
return canned data with zero network or filesystem access.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TemplateSearchService(Protocol):
    """Searches query templates in Azure AI Search.

    Returns a dict with keys: ``has_high_confidence_match``,
    ``is_ambiguous``, ``best_match``, ``confidence_score``,
    ``all_matches``, ``message``, etc.
    """

    async def search(self, user_question: str) -> dict[str, Any]:
        """Search for matching query templates.

        Args:
            user_question: Natural-language question from the user.

        Returns:
            Search result dict describing the best match and metadata.
        """
        ...


@runtime_checkable
class TableSearchService(Protocol):
    """Searches table metadata in Azure AI Search.

    Returns a dict with keys: ``has_matches``, ``tables``,
    ``table_count``, ``message``.
    """

    async def search(self, user_question: str) -> dict[str, Any]:
        """Search for relevant database tables.

        Args:
            user_question: Natural-language question from the user.

        Returns:
            Search result dict describing matching tables.
        """
        ...


@runtime_checkable
class SqlExecutor(Protocol):
    """Executes parameterised SQL against the database.

    Returns a dict with keys: ``success``, ``columns``, ``rows``,
    ``row_count``, ``error``.
    """

    async def execute(
        self,
        query: str,
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a SQL query.

        Args:
            query: SQL statement, optionally with parameter placeholders.
            params: Bind-parameter values (or ``None``).

        Returns:
            Execution result dict with rows, columns, and status.
        """
        ...


@runtime_checkable
class ProgressReporter(Protocol):
    """Reports step-level progress for streaming UI updates."""

    def step_start(self, step: str) -> None:
        """Signal that a named step has started.

        Args:
            step: Human-readable step label.
        """
        ...

    def step_end(self, step: str) -> None:
        """Signal that a named step has completed.

        Args:
            step: Human-readable step label (must match a prior start).
        """
        ...


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------


class NoOpReporter:
    """ProgressReporter that silently discards all events.

    Useful in tests and non-SSE contexts where no streaming UI exists.
    """

    def step_start(self, step: str) -> None:
        """No-op."""

    def step_end(self, step: str) -> None:
        """No-op."""


class QueueReporter:
    """ProgressReporter that pushes events onto an ``asyncio.Queue``.

    Mirrors the existing ``emit_step_start`` / ``emit_step_end`` logic
    in ``api.step_events`` so the SSE streaming endpoint can consume
    them without any ContextVar coupling.

    Args:
        queue: The asyncio queue to push step dicts onto.
    """

    def __init__(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queue = queue
        self._start_times: dict[str, float] = {}

    def step_start(self, step: str) -> None:
        """Record start time and enqueue a *started* event.

        Args:
            step: Human-readable step label.
        """
        start_time = time.time()
        self._start_times[step] = start_time
        self._queue.put_nowait({
            "step": step,
            "status": "started",
            "start_time": start_time,
        })

    def step_end(self, step: str) -> None:
        """Calculate duration and enqueue a *completed* event.

        Args:
            step: Human-readable step label (must match a prior start).
        """
        end_time = time.time()
        start_time = self._start_times.pop(step, None)
        duration_ms = int((end_time - start_time) * 1000) if start_time else None
        self._queue.put_nowait({
            "step": step,
            "status": "completed",
            "duration_ms": duration_ms,
        })
