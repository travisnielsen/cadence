"""
Session management for ConversationOrchestrator instances.

Each user session (identified by thread_id) gets its own orchestrator
that maintains conversation context for refinements.
"""

import logging
import time
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.entities.orchestrator import ConversationOrchestrator

logger = logging.getLogger(__name__)

# In-memory cache for orchestrator sessions
# In production, consider Redis or similar for multi-instance deployments
_orchestrator_cache: dict[str, tuple["ConversationOrchestrator", float]] = {}
_cache_lock = Lock()

# Session TTL: 30 minutes
SESSION_TTL_SECONDS = 30 * 60


def get_orchestrator(thread_id: str | None) -> "ConversationOrchestrator | None":
    """
    Get an existing orchestrator for the given thread ID.
    
    Args:
        thread_id: The Foundry thread ID
        
    Returns:
        The cached orchestrator or None if not found/expired
    """
    if not thread_id:
        return None
    
    with _cache_lock:
        entry = _orchestrator_cache.get(thread_id)
        if entry is None:
            return None
        
        orchestrator, created_at = entry
        if time.time() - created_at > SESSION_TTL_SECONDS:
            # Expired - remove from cache
            del _orchestrator_cache[thread_id]
            logger.info("Session expired for thread_id=%s", thread_id)
            return None
        
        logger.info("Retrieved cached orchestrator for thread_id=%s", thread_id)
        return orchestrator


def store_orchestrator(thread_id: str, orchestrator: "ConversationOrchestrator") -> None:
    """
    Store an orchestrator in the session cache.
    
    Args:
        thread_id: The Foundry thread ID
        orchestrator: The orchestrator instance to cache
    """
    if not thread_id:
        return
    
    with _cache_lock:
        _orchestrator_cache[thread_id] = (orchestrator, time.time())
        logger.info("Stored orchestrator for thread_id=%s (cache size: %d)", thread_id, len(_orchestrator_cache))
        
        # Cleanup old sessions periodically
        _cleanup_expired_sessions()


def _cleanup_expired_sessions() -> None:
    """Remove expired sessions from cache (must hold lock)."""
    now = time.time()
    expired = [
        tid for tid, (_, created_at) in _orchestrator_cache.items()
        if now - created_at > SESSION_TTL_SECONDS
    ]
    for tid in expired:
        del _orchestrator_cache[tid]
    
    if expired:
        logger.info("Cleaned up %d expired sessions", len(expired))


def clear_orchestrator(thread_id: str) -> None:
    """Remove an orchestrator from the cache."""
    if not thread_id:
        return
    
    with _cache_lock:
        if thread_id in _orchestrator_cache:
            del _orchestrator_cache[thread_id]
            logger.info("Cleared orchestrator for thread_id=%s", thread_id)
