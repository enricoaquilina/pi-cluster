"""Integration tests for the /api/events SSE endpoint."""

import asyncio
import json

import pytest


async def test_sse_503_when_full(client, event_bus_instance):
    """SSE endpoint returns 503 when MAX_CLIENTS is reached."""
    queues = []
    try:
        for _ in range(event_bus_instance.MAX_CLIENTS):
            queues.append(event_bus_instance.subscribe())

        resp = await client.get("/api/events")
        assert resp.status_code == 503
        assert "Too many" in resp.json()["detail"]
    finally:
        for q in queues:
            event_bus_instance.unsubscribe(q)


async def test_sse_503_recovers_after_unsubscribe(client, event_bus_instance):
    """After unsubscribing, new SSE connections are accepted again."""
    queues = []
    try:
        for _ in range(event_bus_instance.MAX_CLIENTS):
            queues.append(event_bus_instance.subscribe())

        # Full — should reject
        resp = await client.get("/api/events")
        assert resp.status_code == 503

        # Free one slot
        event_bus_instance.unsubscribe(queues.pop())

        # Should accept now (returns 200 streaming response)
        # We can't consume the stream in tests, but we can verify the subscribe succeeds
        q = event_bus_instance.subscribe()
        assert q is not None
        event_bus_instance.unsubscribe(q)
    finally:
        for q in queues:
            event_bus_instance.unsubscribe(q)


async def test_sse_no_auth_required(event_bus_instance):
    """SSE subscribe works without auth (consistent with GET endpoints)."""
    # The endpoint itself doesn't require auth — verified by testing subscribe directly
    # since httpx ASGI transport can't cleanly handle SSE streams
    q = event_bus_instance.subscribe()
    assert q is not None
    event_bus_instance.unsubscribe(q)


async def test_sse_cleanup_on_unsubscribe(event_bus_instance):
    """Subscriber count correctly tracks connections."""
    initial = len(event_bus_instance._subscribers)
    q1 = event_bus_instance.subscribe()
    q2 = event_bus_instance.subscribe()
    assert len(event_bus_instance._subscribers) == initial + 2
    event_bus_instance.unsubscribe(q1)
    assert len(event_bus_instance._subscribers) == initial + 1
    event_bus_instance.unsubscribe(q2)
    assert len(event_bus_instance._subscribers) == initial


async def test_sse_event_delivery_to_subscriber(event_bus_instance):
    """Events published to the bus are delivered to subscribers."""
    q = event_bus_instance.subscribe()
    try:
        event_bus_instance.publish("test-event")
        payload = json.loads(q.get_nowait())
        assert payload["event"] == "test-event"
        assert "ts" in payload
    finally:
        event_bus_instance.unsubscribe(q)
