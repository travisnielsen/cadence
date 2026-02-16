"""
Dynamic allowed values provider with stale-while-revalidate caching.

Loads allowed values from the database for parameters that use
``allowed_values_source = "database"`` and caches them with a configurable TTL.
On TTL expiry, stale values are returned immediately while a background task
refreshes the cache (stale-while-revalidate pattern).
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from weakref import WeakSet

from entities.shared.clients.sql_client import AzureSqlClient

logger = logging.getLogger(__name__)

# Validation patterns for table/column names (developer-authored, not user input)
_TABLE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*$")
_COLUMN_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass
class AllowedValuesResult:
    """Result returned to callers with cached allowed values."""

    values: list[str]
    is_partial: bool


@dataclass
class _CacheEntry:
    """Internal cache entry tracking values and freshness."""

    values: list[str]
    loaded_at: float
    is_partial: bool


class AllowedValuesProvider:
    """Async provider that loads and caches allowed values from the database.

    Uses a stale-while-revalidate strategy: on first access the caller awaits
    the DB query; after that, stale entries are returned immediately while a
    background ``asyncio.create_task`` refreshes the cache.

    Args:
        server: Azure SQL server hostname (falls back to ``AZURE_SQL_SERVER``).
        database: Database name (falls back to ``AZURE_SQL_DATABASE``).
        ttl_seconds: Cache time-to-live. Also checks ``ALLOWED_VALUES_TTL_SECONDS``.
        max_values: Maximum distinct values to cache per (table, column) pair.
    """

    def __init__(
        self,
        server: str | None = None,
        database: str | None = None,
        ttl_seconds: int | None = None,
        max_values: int = 500,
    ) -> None:
        self._server = server or os.getenv("AZURE_SQL_SERVER", "")
        self._database = database or os.getenv("AZURE_SQL_DATABASE", "")

        # TTL: explicit arg > env var > default 600
        if ttl_seconds is not None:
            self._ttl_seconds = ttl_seconds
        else:
            env_ttl = os.getenv("ALLOWED_VALUES_TTL_SECONDS")
            self._ttl_seconds = int(env_ttl) if env_ttl else 600

        self._max_values = max_values

        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._background_tasks: WeakSet[asyncio.Task[None]] = WeakSet()

    async def _get_lock(self, key: tuple[str, str]) -> asyncio.Lock:
        """Get or create a per-key lock to prevent thundering-herd on first load."""
        # Fast-path (safe: asyncio is single-threaded within the event loop).
        # The double-check inside _global_lock guards against concurrent coroutines.
        if key not in self._locks:
            async with self._global_lock:
                if key not in self._locks:
                    self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get_allowed_values(self, table: str, column: str) -> AllowedValuesResult | None:
        """Return cached allowed values, refreshing in the background when stale.

        Args:
            table: Fully-qualified table name (e.g. ``Sales.CustomerCategories``).
            column: Column name to fetch distinct values from.

        Returns:
            ``AllowedValuesResult`` with the values, or ``None`` on DB error
            (caller should fall back to LLM-only validation).
        """
        if not _TABLE_PATTERN.match(table):
            logger.warning("Invalid table name pattern: %s", table)
            return None
        if not _COLUMN_PATTERN.match(column):
            logger.warning("Invalid column name pattern: %s", column)
            return None

        key = (table, column)
        entry = self._cache.get(key)

        if entry is not None:
            age = time.monotonic() - entry.loaded_at
            if age <= self._ttl_seconds:
                # Fresh — return immediately
                return AllowedValuesResult(values=entry.values, is_partial=entry.is_partial)

            # Stale — return stale data and kick off background refresh
            task = asyncio.create_task(self._refresh(key, table, column))
            self._background_tasks.add(task)
            return AllowedValuesResult(values=entry.values, is_partial=entry.is_partial)

        # Cache miss — must await the load (no stale data available)
        lock = await self._get_lock(key)
        async with lock:
            # Double-check after acquiring lock (another coroutine may have loaded it)
            entry = self._cache.get(key)
            if entry is not None:
                return AllowedValuesResult(values=entry.values, is_partial=entry.is_partial)

            return await self._load(key, table, column)

    async def _load(
        self, key: tuple[str, str], table: str, column: str
    ) -> AllowedValuesResult | None:
        """Execute the DB query and populate the cache."""
        try:
            query = (
                f"SELECT DISTINCT TOP {self._max_values + 1} [{column}] "  # noqa: S608
                f"FROM {table} ORDER BY [{column}]"
            )
            async with AzureSqlClient(
                server=self._server, database=self._database, read_only=True
            ) as client:
                result: dict[str, Any] = await client.execute_query(query)

            if not result.get("success"):
                logger.warning("DB query failed for %s.%s: %s", table, column, result.get("error"))
                return None

            rows: list[dict[str, Any]] = result.get("rows", [])
            is_partial = len(rows) > self._max_values
            values = [str(row[column]) for row in rows[: self._max_values] if row.get(column)]

            self._cache[key] = _CacheEntry(
                values=values, loaded_at=time.monotonic(), is_partial=is_partial
            )
            logger.info(
                "Loaded %d allowed values for %s.%s (partial=%s)",
                len(values),
                table,
                column,
                is_partial,
            )
            return AllowedValuesResult(values=values, is_partial=is_partial)

        except Exception:
            logger.warning("Failed to load allowed values for %s.%s", table, column, exc_info=True)
            return None

    async def _refresh(self, key: tuple[str, str], table: str, column: str) -> None:
        """Background refresh — errors are logged but never propagated."""
        lock = await self._get_lock(key)
        if lock.locked():
            return  # Another refresh is already in progress
        async with lock:
            await self._load(key, table, column)
