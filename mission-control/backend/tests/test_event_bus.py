"""Unit tests for the EventBus class."""

import asyncio
import json
import threading

import pytest


@pytest.fixture
def bus():
    """Fresh EventBus instance for isolation."""
    from main import EventBus
    return EventBus()


def test_subscribe_creates_queue(bus):
    q = bus.subscribe()
    assert isinstance(q, asyncio.Queue)
    assert len(bus._subscribers) == 1


def test_unsubscribe_removes_queue(bus):
    q = bus.subscribe()
    assert len(bus._subscribers) == 1
    bus.unsubscribe(q)
    assert len(bus._subscribers) == 0


def test_unsubscribe_idempotent(bus):
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.unsubscribe(q)  # Should not raise
    assert len(bus._subscribers) == 0


def test_publish_delivers_to_all_subscribers(bus):
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    q3 = bus.subscribe()
    bus.publish("test")
    assert not q1.empty()
    assert not q2.empty()
    assert not q3.empty()


def test_publish_no_subscribers(bus):
    bus.publish("test")  # Should not raise


def test_publish_event_format(bus):
    q = bus.subscribe()
    bus.publish("nodes")
    payload = json.loads(q.get_nowait())
    assert payload["event"] == "nodes"
    assert "ts" in payload
    assert isinstance(payload["ts"], float)


def test_max_clients_enforced(bus):
    queues = []
    for _ in range(bus.MAX_CLIENTS):
        queues.append(bus.subscribe())
    with pytest.raises(RuntimeError, match="Too many SSE clients"):
        bus.subscribe()


def test_max_clients_recovers_after_unsubscribe(bus):
    queues = []
    for _ in range(bus.MAX_CLIENTS):
        queues.append(bus.subscribe())
    bus.unsubscribe(queues.pop())
    q = bus.subscribe()  # Should succeed
    assert isinstance(q, asyncio.Queue)


def test_queue_full_drops_event(bus):
    q = bus.subscribe()
    # Fill the queue to maxsize (64)
    for i in range(64):
        q.put_nowait(f"filler-{i}")
    assert q.full()
    # This should not raise — event is silently dropped
    bus.publish("overflow")
    # Queue should still be at maxsize
    assert q.qsize() == 64


def test_thread_safety(bus):
    """Publish from multiple threads concurrently while subscribing/unsubscribing."""
    errors = []

    def publisher():
        try:
            for _ in range(100):
                bus.publish("thread-test")
        except Exception as e:
            errors.append(e)

    def subscriber_churn():
        try:
            for _ in range(50):
                q = bus.subscribe()
                bus.unsubscribe(q)
        except RuntimeError:
            pass  # Max clients is expected
        except Exception as e:
            errors.append(e)

    # Pre-subscribe a queue to catch events
    q = bus.subscribe()

    threads = []
    for _ in range(5):
        threads.append(threading.Thread(target=publisher))
        threads.append(threading.Thread(target=subscriber_churn))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread safety errors: {errors}"
    # Queue should have received some events (not all due to timing)
    assert q.qsize() > 0
