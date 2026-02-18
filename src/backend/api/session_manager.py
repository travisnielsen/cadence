"""Session management for DataAssistant instances."""

import logging
import time
from collections import OrderedDict
from threading import Lock
from typing import TYPE_CHECKING

from config.settings import get_settings

if TYPE_CHECKING:
    from entities.assistant import DataAssistant

logger = logging.getLogger(__name__)

_assistant_cache: OrderedDict[str, tuple["DataAssistant", float]] = OrderedDict()
_cache_lock = Lock()

SESSION_TTL_SECONDS = 30 * 60


def get_assistant(thread_id: str | None) -> "DataAssistant | None":
    """Get an existing DataAssistant for the given thread ID."""
    if not thread_id:
        return None
    with _cache_lock:
        entry = _assistant_cache.get(thread_id)
        if entry is None:
            return None
        assistant, created_at = entry
        if time.time() - created_at > SESSION_TTL_SECONDS:
            del _assistant_cache[thread_id]
            logger.info("Session expired for thread_id=%s", thread_id)
            return None
        _assistant_cache.move_to_end(thread_id)
        logger.info("Retrieved cached assistant for thread_id=%s", thread_id)
        return assistant


def store_assistant(
    thread_id: str,
    assistant: "DataAssistant",
) -> None:
    """Store a DataAssistant in the session cache."""
    if not thread_id:
        return
    settings = get_settings()
    max_sessions = settings.max_session_cache_size
    with _cache_lock:
        _assistant_cache[thread_id] = (assistant, time.time())
        _assistant_cache.move_to_end(thread_id)
        logger.info(
            "Stored assistant for thread_id=%s (cache size: %d)",
            thread_id,
            len(_assistant_cache),
        )
        _cleanup_expired_sessions()
        while len(_assistant_cache) > max_sessions:
            evicted_tid, _ = _assistant_cache.popitem(last=False)
            logger.info("Evicted LRU session: thread_id=%s", evicted_tid)


def _cleanup_expired_sessions() -> None:
    """Remove expired sessions from cache (must hold lock)."""
    now = time.time()
    expired = [
        tid
        for tid, (_, created_at) in _assistant_cache.items()
        if now - created_at > SESSION_TTL_SECONDS
    ]
    for tid in expired:
        del _assistant_cache[tid]
    if expired:
        logger.info("Cleaned up %d expired sessions", len(expired))


def clear_assistant(thread_id: str) -> None:
    """Remove a DataAssistant from the cache."""
    if not thread_id:
        return
    with _cache_lock:
        if thread_id in _assistant_cache:
            del _assistant_cache[thread_id]
            logger.info("Cleared assistant for thread_id=%s", thread_id)
