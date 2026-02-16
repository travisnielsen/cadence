"""
Workflow Cache for HITL (Human-in-the-Loop) Clarification Flow.

Stores paused workflow instances keyed by request_id so they can be resumed
when the user provides a clarification response.

Note: This is an in-memory cache. For production deployments with multiple
instances, consider using Agent Framework's checkpointing with external storage.
"""

import logging
import os
from collections import OrderedDict
from datetime import datetime, timedelta
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_framework import Workflow

logger = logging.getLogger(__name__)

# Cache entries with timestamp for cleanup
_workflow_cache: OrderedDict[str, tuple["Workflow", datetime]] = OrderedDict()
_cache_lock = Lock()

# How long to keep paused workflows before cleanup (5 minutes)
WORKFLOW_TTL = timedelta(minutes=5)

# Maximum number of cached paused workflows (LRU eviction when exceeded)
MAX_WORKFLOWS = int(os.getenv("MAX_WORKFLOW_CACHE_SIZE", "100"))


def store_paused_workflow(request_id: str, workflow: "Workflow") -> None:
    """
    Store a paused workflow for later resumption.

    Args:
        request_id: The request_id from the RequestInfoEvent
        workflow: The paused Workflow instance
    """
    with _cache_lock:
        _workflow_cache[request_id] = (workflow, datetime.now())
        _workflow_cache.move_to_end(request_id)
        logger.info(
            "Stored paused workflow for request_id=%s (cache size: %d)",
            request_id,
            len(_workflow_cache),
        )

        # Opportunistic cleanup of expired entries
        _cleanup_expired_unlocked()

        # Evict oldest entries if over max size
        while len(_workflow_cache) > MAX_WORKFLOWS:
            evicted_rid, _ = _workflow_cache.popitem(last=False)
            logger.info("Evicted LRU workflow: request_id=%s", evicted_rid)


def get_paused_workflow(request_id: str) -> "Workflow | None":
    """
    Retrieve and remove a paused workflow by request_id.

    Args:
        request_id: The request_id to look up

    Returns:
        The Workflow instance if found and not expired, None otherwise
    """
    with _cache_lock:
        entry = _workflow_cache.pop(request_id, None)
        if entry is None:
            logger.warning("No paused workflow found for request_id=%s", request_id)
            return None

        workflow, stored_at = entry
        age = datetime.now() - stored_at

        if age > WORKFLOW_TTL:
            logger.warning("Paused workflow for request_id=%s expired (age: %s)", request_id, age)
            return None

        logger.info("Retrieved paused workflow for request_id=%s (age: %s)", request_id, age)
        return workflow


def _cleanup_expired_unlocked() -> None:
    """Remove expired entries from cache. Must hold _cache_lock."""
    now = datetime.now()
    expired = [
        rid for rid, (_, stored_at) in _workflow_cache.items() if now - stored_at > WORKFLOW_TTL
    ]
    for rid in expired:
        del _workflow_cache[rid]
    if expired:
        logger.info("Cleaned up %d expired workflow entries", len(expired))


def get_cache_size() -> int:
    """Get the current number of cached workflows."""
    with _cache_lock:
        return len(_workflow_cache)
