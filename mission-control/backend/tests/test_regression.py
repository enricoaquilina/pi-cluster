"""Regression tests: existing REST endpoints are unchanged after SSE changes."""

import pytest
from unittest.mock import patch


async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_list_tasks(client):
    resp = await client.get("/api/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_create_read_update_delete_task(client, auth_headers):
    # Create
    resp = await client.post("/api/tasks", json={
        "title": "Regression test task",
        "status": "todo",
        "priority": "low",
    }, headers=auth_headers)
    assert resp.status_code == 201
    task = resp.json()
    task_id = task["id"]
    assert task["title"] == "Regression test task"

    # Read
    resp = await client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Regression test task"

    # Update
    resp = await client.patch(f"/api/tasks/{task_id}", json={
        "status": "in_progress",
    }, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_progress"

    # Delete
    resp = await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)
    assert resp.status_code == 204


async def test_list_nodes(client):
    resp = await client.get("/api/nodes")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_upsert_and_read_node(client, auth_headers):
    resp = await client.post("/api/nodes", json={
        "name": "regression-test-node",
        "hostname": "regtest",
        "status": "online",
        "ram_total_mb": 2048,
        "ram_used_mb": 1024,
        "cpu_percent": 25.0,
        "last_heartbeat": "2026-03-26T12:00:00Z",
    }, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["name"] == "regression-test-node"

    resp = await client.get("/api/nodes")
    assert resp.status_code == 200
    names = [n["name"] for n in resp.json()]
    assert "regression-test-node" in names


async def test_list_services(client):
    resp = await client.get("/api/services")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_stats_endpoint(client):
    resp = await client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data or isinstance(data, dict)


async def test_api_key_required_on_mutations(client):
    # POST without key
    resp = await client.post("/api/tasks", json={
        "title": "No auth test",
        "status": "todo",
        "priority": "low",
    })
    assert resp.status_code == 401

    # PATCH without key
    resp = await client.patch("/api/tasks/00000000-0000-0000-0000-000000000000", json={
        "status": "done",
    })
    assert resp.status_code == 401

    # DELETE without key
    resp = await client.delete("/api/tasks/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401


async def test_api_key_not_required_on_reads(client):
    resp = await client.get("/api/tasks")
    assert resp.status_code == 200

    resp = await client.get("/api/nodes")
    assert resp.status_code == 200

    resp = await client.get("/api/services")
    assert resp.status_code == 200

    resp = await client.get("/api/stats")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_returns_503_when_db_down(app):
    """Health endpoint must return 503 (not 200) when database is unreachable."""
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with patch("main.psycopg2.connect", side_effect=Exception("DB connection refused")):
            resp = await c.get("/health")
    assert resp.status_code == 503, f"Expected 503 but got {resp.status_code}: {resp.json()}"
    body = resp.json()
    assert body["status"] == "degraded"
    assert "error" in body["db"]
