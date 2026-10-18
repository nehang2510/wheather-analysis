import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any


class TTLCache:
    """In-process TTL cache with single-flight: concurrent misses on one key share a single upstream call.

    Good for one Cloud Run instance. For several instances or a multi-worker deployment swap this for
    Redis so the upstream quota is shared; the interface is deliberately small to make that a drop-in.
    """

    def __init__(self, max_entries: int = 2_000) -> None:
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._max = max_entries
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        self._store[key] = (time.monotonic() + ttl_s, value)
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    async def get_or_set(self, key: str, ttl_s: float, loader: Callable[[], Awaitable[Any]]) -> Any:
        value = self.get(key)
        if value is not None:
            self.hits += 1
            return value
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            value = self.get(key)  # another coroutine may have filled it while we waited
            if value is not None:
                self.hits += 1
                return value
            self.misses += 1
            try:
                value = await loader()
            finally:
                self._locks.pop(key, None)
            self.set(key, value, ttl_s)
            return value

    def __len__(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()
        self._locks.clear()
