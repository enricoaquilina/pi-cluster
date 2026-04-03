"""Edge cases: input validation, boundary conditions, auth, response shapes, untested endpoints."""

import pytest


# ── Input validation (Pydantic 422) ──────────────────────────────────────────


@pytest.mark.parametrize("endpoint,payload", [
    ("/api/tasks", {"status": "todo", "priority": "low"}),                       # missing title
    ("/api/tasks", {"title": "x", "status": "INVALID"}),                         # bad status
    ("/api/tasks", {"title": "x", "priority": "INVALID"}),                       # bad priority
    ("/api/nodes", {"name": "test-edge", "hostname": "h", "status": "INVALID"}), # bad node status
    ("/api/services/alert", {"service": "svc", "status": "INVALID"}),            # bad alert status
    ("/api/services/check", {"checks": [{"service": "svc", "status": "INVALID"}]}),
])
async def test_invalid_payload_returns_422(client, auth_headers, endpoint, payload):
    resp = await client.post(endpoint, json=payload, headers=auth_headers)
    assert resp.status_code == 422


# ── Node boundary conditions ────────────────────────────────────────────────


async def test_node_patch_nonexistent_returns_404(client, auth_headers):
    resp = await client.patch(
        "/api/nodes/nonexistent-node-xyz",
        json={"status": "degraded"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_node_patch_empty_body_returns_400(client, auth_headers):
    await client.post("/api/nodes", json={
        "name": "test-edge-node", "hostname": "h", "status": "online",
        "ram_total_mb": 1024, "ram_used_mb": 0, "cpu_percent": 0,
    }, headers=auth_headers)
    resp = await client.patch("/api/nodes/test-edge-node", json={}, headers=auth_headers)
    assert resp.status_code == 400


async def test_node_upsert_is_idempotent(client, auth_headers):
    """POST same node twice — second updates, no duplicate."""
    payload = {
        "name": "test-edge-node", "hostname": "h", "status": "online",
        "ram_total_mb": 1024, "ram_used_mb": 0, "cpu_percent": 0,
    }
    resp1 = await client.post("/api/nodes", json=payload, headers=auth_headers)
    assert resp1.status_code == 201
    payload["hostname"] = "updated-host"
    resp2 = await client.post("/api/nodes", json=payload, headers=auth_headers)
    assert resp2.status_code == 201
    assert resp2.json()["hostname"] == "updated-host"
    resp = await client.get("/api/nodes")
    names = [n["name"] for n in resp.json()]
    assert names.count("test-edge-node") == 1


# ── Service edge cases ──────────────────────────────────────────────────────


async def test_bulk_check_empty_list(client, auth_headers):
    """Empty checks array — 201, inserted: 0."""
    resp = await client.post("/api/services/check", json={"checks": []}, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["inserted"] == 0


# ── Auth edge cases ─────────────────────────────────────────────────────────


async def test_wrong_api_key_returns_401(client):
    resp = await client.post("/api/tasks", json={
        "title": "wrong key", "status": "todo", "priority": "low",
    }, headers={"X-Api-Key": "definitely-wrong-key"})
    assert resp.status_code == 401


async def test_empty_api_key_header_returns_401(client):
    resp = await client.post("/api/tasks", json={
        "title": "empty key", "status": "todo", "priority": "low",
    }, headers={"X-Api-Key": ""})
    assert resp.status_code == 401


# ── Response shape validation ───────────────────────────────────────────────


async def test_stats_response_shape(client):
    resp = await client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("total", "todo", "in_progress", "blocked", "review", "done", "enrico", "maxwell"):
        assert key in data, f"Missing key: {key}"
        assert isinstance(data[key], int)


# ── Untested read-only endpoints ────────────────────────────────────────────


async def test_dispatch_personas_endpoint(client):
    resp = await client.get("/api/dispatch/personas")
    assert resp.status_code == 200
    data = resp.json()
    assert "Archie" in data
    assert data["Archie"]["node"] == "gateway"
    assert data["Archie"]["team"] == "Engineering"


async def test_team_endpoint(client):
    resp = await client.get("/api/team")
    assert resp.status_code == 200
    data = resp.json()
    assert "orchestrator" in data
    assert data["orchestrator"]["name"] == "Maxwell"
    assert "teams" in data
    assert len(data["teams"]) >= 3


async def test_memories_endpoint_shape(client):
    resp = await client.get("/api/memories")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data


async def test_memories_file_path_traversal_blocked(client):
    """Path traversal attempt — 403."""
    resp = await client.get("/api/memories/file?path=../../etc/passwd")
    assert resp.status_code == 403


async def test_memories_file_not_found(client):
    resp = await client.get("/api/memories/file?path=workspace/nonexistent.md")
    assert resp.status_code == 404


async def test_service_alerts_endpoint(client):
    resp = await client.get("/api/services/alerts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_service_history_returns_list(client):
    resp = await client.get("/api/services/test-svc/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_dispatch_log_pagination_limit(client, auth_headers):
    """limit=1 returns exactly 1 item when data exists."""
    await client.post("/api/dispatch/log", json={
        "persona": "test", "node": "n", "delegate": "d",
        "elapsed_ms": 0, "status": "success",
    }, headers=auth_headers)
    resp = await client.get("/api/dispatch/log?limit=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["limit"] == 1


async def test_dispatch_log_filter_by_node(client, auth_headers):
    await client.post("/api/dispatch/log", json={
        "persona": "test", "node": "test-edge-node", "delegate": "d",
        "elapsed_ms": 0, "status": "success",
    }, headers=auth_headers)
    resp = await client.get("/api/dispatch/log?node=test-edge-node")
    assert resp.status_code == 200
    assert all(item["node"] == "test-edge-node" for item in resp.json()["items"])
