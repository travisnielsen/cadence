"""
Clarification Context Cache for HITL (Human-in-the-Loop) clarification flow.

Stores ClarificationRequest data keyed by request_id so process_query()
can be called with the right context when the user responds.
"""

import logging
import os
from collections import OrderedDict
from datetime import datetime, timedelta
from threading import Lock

from models import ClarificationRequest

logger = logging.getLogger(__name__)

_context_cache: OrderedDict[str, tuple[ClarificationRequest, datetime]] = OrderedDict()
_cache_lock = Lock()

CONTEXT_TTL = timedelta(minutes=5)
MAX_CONTEXTS = int(os.getenv("MAX_WORKFLOW_CACHE_SIZE", "100"))


def store_clarification_context(
    request_id: str,
    context: ClarificationRequest,
) -> None:
    """Store clarification context for later resumption."""
    with _cache_lock:
        _context_cache[request_id] = (context, datetime.now())
        _context_cache.move_to_end(request_id)
        logger.info(
            "Stored clarification context request_id=%s (size: %d)",
            request_id,
            len(_context_cache),
        )
        _cleanup_expired_unlocked()
        while len(_context_cache) > MAX_CONTEXTS:
            evicted_rid, _ = _context_cache.popitem(last=False)
            logger.info(
                "Evicted LRU clarification context: %s",
                evicted_rid,
            )


def get_clarification_context(
    request_id: str,
) -> ClarificationRequest | None:
    """Retrieve and remove a clarification context by request_id."""
    with _cache_lock:
        entry = _context_cache.pop(request_id, None)
        if entry is None:
            logger.warning(
                "No clarification context for request_id=%s",
                request_id,
            )
            return None
        context, stored_at = entry
        age = datetime.now() - stored_at
        if age > CONTEXT_TTL:
            logger.warning(
                "Clarification context expired request_id=%s (age=%s)",
                request_id,
                age,
            )
            return None
        logger.info(
            "Retrieved clarification context request_id=%s (age=%s)",
            request_id,
            age,
        )
        return context


def _cleanup_expired_unlocked() -> None:
    """Remove expired entries from cache. Must hold _cache_lock."""
    now = datetime.now()
    expired = [
        rid for rid, (_, stored_at) in _context_cache.items() if now - stored_at > CONTEXT_TTL
    ]
    for rid in expired:
        del _context_cache[rid]
    if expired:
        logger.info(
            "Cleaned up %d expired clarification contexts",
            len(expired),
        )


def get_cache_size() -> int:
    """Get the current number of cached clarification contexts."""
    with _cache_lock:
        return len(_context_cache)
