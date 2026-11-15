"""What the real Firestore code does when Firestore cannot be reached.

The Firestore client is pointed at an address where nothing is listening.
Every call must fail within a few seconds. Before the calls were given an
explicit limit, the client library kept retrying for minutes and the
request hung.

There is also a test that liveness keeps answering while every worker
thread is stuck waiting for the datastore.
"""
import asyncio
import os
import sys
import time

import httpx
import pytest

from conftest import ROOT, load_service

LIMIT_SECONDS = 4.5


def load_firestore_store(service, monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "unreachable-test")
    folder = str(ROOT / "services" / service)
    sys.modules.pop("store", None)
    sys.path.insert(0, folder)
    try:
        import store
        return store.FirestoreStore()
    finally:
        sys.path.remove(folder)
        sys.modules.pop("store", None)


def fails_fast(call):
    started = time.time()
    with pytest.raises(Exception) as caught:
        call()
    elapsed = time.time() - started
    # A TypeError or AttributeError would mean the call itself is written
    # wrongly, not that the datastore is unreachable.
    assert not isinstance(caught.value, (TypeError, AttributeError, ValueError)), caught.value
    assert elapsed < LIMIT_SECONDS, f"took {elapsed:.1f}s"


def test_links_firestore_calls_fail_fast(monkeypatch):
    store = load_firestore_store("links", monkeypatch)
    fails_fast(store.ping)
    fails_fast(lambda: store.get("abc1234"))
    fails_fast(lambda: store.create("abc1234", "https://example.com"))
    fails_fast(lambda: store.recent(25))


def test_stats_firestore_calls_fail_fast(monkeypatch):
    store = load_firestore_store("stats", monkeypatch)
    fails_fast(store.ping)
    fails_fast(lambda: store.increment("abc1234"))
    fails_fast(lambda: store.get_many(["abc1234", "xyz9876"]))


@pytest.mark.parametrize("service", ["links", "stats"])
def test_liveness_answers_while_datastore_calls_are_stuck(service):
    module = load_service(service)

    class StuckStore:
        def ping(self):
            time.sleep(2)

    module.store = StuckStore()

    async def scenario():
        transport = httpx.ASGITransport(app=module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # More stuck readiness checks than there are worker threads.
            stuck = [asyncio.create_task(client.get("/readyz")) for _ in range(60)]
            await asyncio.sleep(0.3)
            started = time.time()
            response = await client.get("/healthz")
            elapsed = time.time() - started
            for task in stuck:
                task.cancel()
            return response.status_code, elapsed

    status, elapsed = asyncio.run(scenario())
    assert status == 200
    assert elapsed < 0.5, f"liveness took {elapsed:.2f}s"
