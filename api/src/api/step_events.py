"""
Step event queue for streaming tool-level progress from executors.

This module provides a simple asyncio.Queue-based mechanism for emitting
step events from within executors that can be consumed by the SSE streaming endpoint.

Supports timing by emitting start/end events that the streaming endpoint uses
to calculate durations.

Also provides context for passing request-scoped data (like user_id) to executors.
"""

import asyncio
import logging
import time
from contextvars import ContextVar
from typing import Optional

logger = logging.getLogger(__name__)

# Context variable to hold the current step queue for this request
_step_queue_var: ContextVar[Optional[asyncio.Queue]] = ContextVar("step_queue", default=None)

# Track step start times for duration calculation
_step_start_times: ContextVar[Optional[dict[str, float]]] = ContextVar("step_start_times", default=None)

# Context variable to hold the current user_id for this request
# This allows executors to access the authenticated user when creating threads
_user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)


def get_request_user_id() -> Optional[str]:
    """Get the current user_id for this request context."""
    return _user_id_var.get()


def set_request_user_id(user_id: Optional[str]) -> None:
    """Set the user_id for this request context."""
    _user_id_var.set(user_id)


def get_step_queue() -> Optional[asyncio.Queue]:
    """Get the current step queue for this request context."""
    return _step_queue_var.get()


def set_step_queue(queue: asyncio.Queue) -> None:
    """Set the step queue for this request context."""
    _step_queue_var.set(queue)
    _step_start_times.set({})


def clear_step_queue() -> None:
    """Clear the step queue for this request context."""
    _step_queue_var.set(None)
    _step_start_times.set(None)


def _get_start_times() -> dict[str, float]:
    """Get the step start times dict, creating if needed."""
    times = _step_start_times.get()
    if times is None:
        times = {}
        _step_start_times.set(times)
    return times


async def emit_step(step: str) -> None:
    """
    Emit a step event to the current request's queue (async version).
    
    This can be called from async code within the request context
    (tools, executors, etc.) to send progress updates to the client.
    
    Args:
        step: The step message to emit (e.g., "Searching cached queries...")
    """
    queue = get_step_queue()
    if queue:
        await queue.put({"step": step, "status": "started", "start_time": time.time()})


def emit_step_start(step: str) -> None:
    """
    Emit a step start event. Call this when a tool/operation begins.
    
    Args:
        step: The step message (e.g., "Searching cached queries...")
    """
    queue = get_step_queue()
    start_time = time.time()
    
    logger.info("emit_step_start called with '%s', queue=%s", step, queue)
    
    if queue:
        try:
            # Track start time for duration calculation
            start_times = _get_start_times()
            start_times[step] = start_time
            
            queue.put_nowait({
                "step": step,
                "status": "started",
                "start_time": start_time,
            })
            logger.info("Step start event queued: %s", step)
        except asyncio.QueueFull:
            logger.warning("Step queue full, dropping start event: %s", step)
    else:
        logger.warning("No step queue available for step start: %s", step)


def emit_step_end(step: str) -> None:
    """
    Emit a step end event with duration. Call this when a tool/operation completes.
    
    Args:
        step: The step message (must match the start event)
    """
    queue = get_step_queue()
    end_time = time.time()
    
    logger.info("emit_step_end called with '%s', queue=%s", step, queue)
    
    if queue:
        try:
            # Calculate duration from start time
            start_times = _get_start_times()
            start_time = start_times.pop(step, None)
            duration_ms = int((end_time - start_time) * 1000) if start_time else None
            
            queue.put_nowait({
                "step": step,
                "status": "completed",
                "duration_ms": duration_ms,
            })
            logger.info("Step end event queued: %s (duration: %sms)", step, duration_ms)
        except asyncio.QueueFull:
            logger.warning("Step queue full, dropping end event: %s", step)
    else:
        logger.warning("No step queue available for step end: %s", step)


def emit_step_sync(step: str) -> None:
    """
    Emit a simple step event synchronously (legacy, no timing).
    
    For timed events, use emit_step_start() and emit_step_end() instead.
    
    Args:
        step: The step message to emit
    """
    queue = get_step_queue()
    logger.info("emit_step_sync called with '%s', queue=%s", step, queue)
    if queue:
        try:
            queue.put_nowait({"step": step, "status": "started"})
            logger.info("Step event queued successfully: %s", step)
        except asyncio.QueueFull:
            logger.warning("Step queue full, dropping event: %s", step)
    else:
        logger.warning("No step queue available for step: %s", step)
