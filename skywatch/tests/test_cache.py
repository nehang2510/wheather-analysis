import asyncio

from app.cache import TTLCache


async def test_single_flight_dedupes_concurrent_misses():
    cache = TTLCache()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"v": calls}

    results = await asyncio.gather(*(cache.get_or_set("k", 10, loader) for _ in range(20)))
    assert calls == 1
    assert all(r == {"v": 1} for r in results)
    assert cache.hits == 19 and cache.misses == 1


async def test_expiry_and_eviction():
    cache = TTLCache(max_entries=2)
    cache.set("a", 1, ttl_s=0)  # expires immediately
    assert cache.get("a") is None
    cache.set("b", 1, 10)
    cache.set("c", 1, 10)
    cache.set("d", 1, 10)
    assert len(cache) == 2 and cache.get("b") is None


async def test_loader_failure_does_not_poison_cache():
    cache = TTLCache()

    async def boom():
        raise RuntimeError("upstream down")

    try:
        await cache.get_or_set("k", 10, boom)
    except RuntimeError:
        pass
    assert cache.get("k") is None

    async def ok():
        return 42

    assert await cache.get_or_set("k", 10, ok) == 42
