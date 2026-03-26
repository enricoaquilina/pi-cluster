"""Integration tests: mutating endpoints publish correct SSE events."""

import asyncio
import json
import uuid

import pytest


async def _drain_queue(q: asyncio.Queue, timeout: float = 0.5) -> list[dict]:
    """Drain all events from queue within timeout."""
    events = []
    try:
        while True:
            raw = await asyncio.wait_for(q.get(), timeout=timeout)
            events.append(json.loads(raw))
    except (asyncio.TimeoutError, asyncio.QueueEmpty):
        pass
    return events


async def _flush_queue(q: asyncio.Queue):
    """Discard any pending events."""
    while not q.empty():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            break


async def test_create_task_publishes_tasks_event(client, auth_headers, event_queue):
    await _flush_queue(event_queue)
    resp = await client.post("/api/tasks", json={
        "title": "SSE test task",
        "status": "todo",
        "priority": "low",
    }, headers=auth_headers)
    assert resp.status_code == 201
    events = await _drain_queue(event_queue)
    assert any(e["event"] == "tasks" for e in events)
    # Cleanup
    task_id = resp.json()["id"]
    await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)


async def test_update_task_publishes_tasks_event(client, auth_headers, event_queue):
    # Create a task first
    resp = await client.post("/api/tasks", json={
        "title": "Update SSE test",
        "status": "todo",
        "priority": "low",
    }, headers=auth_headers)
    task_id = resp.json()["id"]
    await _flush_queue(event_queue)

    resp = await client.patch(f"/api/tasks/{task_id}", json={
        "status": "in_progress",
    }, headers=auth_headers)
    assert resp.status_code == 200
    events = await _drain_queue(event_queue)
    assert any(e["event"] == "tasks" for e in events)
    # Cleanup
    await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)


async def test_delete_task_publishes_tasks_event(client, auth_headers, event_queue):
    resp = await client.post("/api/tasks", json={
        "title": "Delete SSE test",
        "status": "todo",
        "priority": "low",
    }, headers=auth_headers)
    task_id = resp.json()["id"]
    await _flush_queue(event_queue)

    resp = await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)
    assert resp.status_code == 204
    events = await _drain_queue(event_queue)
    assert any(e["event"] == "tasks" for e in events)


async def test_upsert_node_publishes_nodes_event(client, auth_headers, event_queue):
    await _flush_queue(event_queue)
    resp = await client.post("/api/nodes", json={
        "name": "test-sse-node",
        "hostname": "test-host",
        "status": "online",
        "ram_total_mb": 1024,
        "ram_used_mb": 512,
        "cpu_percent": 50.0,
        "last_heartbeat": "2026-03-26T12:00:00Z",
    }, headers=auth_headers)
    assert resp.status_code == 201
    events = await _drain_queue(event_queue)
    assert any(e["event"] == "nodes" for e in events)


async def test_update_node_publishes_nodes_event(client, auth_headers, event_queue):
    # Ensure node exists
    await client.post("/api/nodes", json={
        "name": "test-sse-node",
        "hostname": "test-host",
        "status": "online",
        "ram_total_mb": 1024,
        "ram_used_mb": 512,
        "cpu_percent": 50.0,
        "last_heartbeat": "2026-03-26T12:00:00Z",
    }, headers=auth_headers)
    await _flush_queue(event_queue)

    resp = await client.patch("/api/nodes/test-sse-node", json={
        "status": "healthy",
    }, headers=auth_headers)
    assert resp.status_code == 200
    events = await _drain_queue(event_queue)
    assert any(e["event"] == "nodes" for e in events)


async def test_bulk_service_check_publishes_services_event(client, auth_headers, event_queue):
    await _flush_queue(event_queue)
    resp = await client.post("/api/services/check", json={
        "checks": [{"service": "test-sse-svc", "status": "up", "response_ms": 42}],
    }, headers=auth_headers)
    assert resp.status_code == 201
    events = await _drain_queue(event_queue)
    assert any(e["event"] == "services" for e in events)


async def test_record_alert_publishes_services_event(client, auth_headers, event_queue):
    await _flush_queue(event_queue)
    resp = await client.post("/api/services/alert", json={
        "service": "test-sse-svc",
        "status": "down",
        "message": "SSE test alert",
    }, headers=auth_headers)
    assert resp.status_code == 201
    events = await _drain_queue(event_queue)
    assert any(e["event"] == "services" for e in events)


async def test_dispatch_log_publishes_dispatch_event(client, auth_headers, event_queue):
    await _flush_queue(event_queue)
    resp = await client.post("/api/dispatch/log", json={
        "persona": "test",
        "node": "test-node",
        "delegate": "test-delegate",
        "prompt_preview": "SSE test",
        "response_preview": "ok",
        "elapsed_ms": 100,
        "status": "success",
    }, headers=auth_headers)
    assert resp.status_code == 201
    events = await _drain_queue(event_queue)
    assert any(e["event"] == "dispatch" for e in events)


async def test_failed_update_does_not_publish(client, auth_headers, event_queue):
    await _flush_queue(event_queue)
    fake_id = str(uuid.uuid4())
    resp = await client.patch(f"/api/tasks/{fake_id}", json={
        "status": "done",
    }, headers=auth_headers)
    assert resp.status_code == 404
    events = await _drain_queue(event_queue)
    assert not any(e["event"] == "tasks" for e in events)


async def test_failed_delete_does_not_publish(client, auth_headers, event_queue):
    await _flush_queue(event_queue)
    fake_id = str(uuid.uuid4())
    resp = await client.delete(f"/api/tasks/{fake_id}", headers=auth_headers)
    assert resp.status_code == 404
    events = await _drain_queue(event_queue)
    assert not any(e["event"] == "tasks" for e in events)
