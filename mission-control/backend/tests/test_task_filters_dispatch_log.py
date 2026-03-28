"""Tests for task filtering, 404 handling, and dispatch log CRUD."""

import uuid

import pytest


# ── Task filtering ──────────────────────────────────────────────────────────


async def test_filter_tasks_by_status(client, auth_headers):
    resp = await client.post("/api/tasks", json={
        "title": "Filter status test",
        "status": "todo",
        "priority": "low",
        "assignee": "maxwell",
    }, headers=auth_headers)
    assert resp.status_code == 201
    task_id = resp.json()["id"]

    try:
        resp = await client.get("/api/tasks?status=todo")
        assert resp.status_code == 200
        tasks = resp.json()
        assert all(t["status"] == "todo" for t in tasks)
        assert any(t["id"] == task_id for t in tasks)
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)


async def test_filter_tasks_by_assignee(client, auth_headers):
    resp = await client.post("/api/tasks", json={
        "title": "Assignee filter test",
        "status": "todo",
        "priority": "low",
        "assignee": "maxwell",
    }, headers=auth_headers)
    assert resp.status_code == 201
    task_id = resp.json()["id"]

    try:
        resp = await client.get("/api/tasks?assignee=maxwell")
        assert resp.status_code == 200
        tasks = resp.json()
        assert all(t["assignee"] == "maxwell" for t in tasks)
        assert any(t["id"] == task_id for t in tasks)
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)


async def test_filter_tasks_by_project(client, auth_headers):
    resp = await client.post("/api/tasks", json={
        "title": "Project filter test",
        "status": "todo",
        "priority": "low",
        "project": "test-filter-project",
    }, headers=auth_headers)
    assert resp.status_code == 201
    task_id = resp.json()["id"]

    try:
        resp = await client.get("/api/tasks?project=test-filter-project")
        assert resp.status_code == 200
        tasks = resp.json()
        assert all(t["project"] == "test-filter-project" for t in tasks)
        assert any(t["id"] == task_id for t in tasks)
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)


async def test_filter_tasks_combined_status_and_assignee(client, auth_headers):
    resp = await client.post("/api/tasks", json={
        "title": "Combined filter test",
        "status": "in_progress",
        "priority": "medium",
        "assignee": "maxwell",
    }, headers=auth_headers)
    assert resp.status_code == 201
    task_id = resp.json()["id"]

    try:
        resp = await client.get("/api/tasks?status=in_progress&assignee=maxwell")
        assert resp.status_code == 200
        tasks = resp.json()
        assert all(t["status"] == "in_progress" and t["assignee"] == "maxwell" for t in tasks)
        assert any(t["id"] == task_id for t in tasks)
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)


# ── Task with all optional fields ──────────────────────────────────────────


async def test_task_all_optional_fields_round_trip(client, auth_headers):
    payload = {
        "title": "Full fields test",
        "description": "A thorough description",
        "status": "review",
        "priority": "high",
        "assignee": "maxwell",
        "project": "test-full-fields",
        "tags": ["tagA", "tagB"],
        "due_date": "2026-04-01T00:00:00Z",
    }
    resp = await client.post("/api/tasks", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    task = resp.json()
    task_id = task["id"]

    try:
        assert task["description"] == "A thorough description"
        assert task["project"] == "test-full-fields"
        assert set(task["tags"]) == {"tagA", "tagB"}
        assert task["due_date"] is not None

        resp = await client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        fetched = resp.json()
        assert fetched["description"] == "A thorough description"
        assert set(fetched["tags"]) == {"tagA", "tagB"}
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)


# ── Task 404 handling ───────────────────────────────────────────────────────


async def test_get_nonexistent_task_returns_404(client):
    resp = await client.get(f"/api/tasks/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_patch_nonexistent_task_returns_404(client, auth_headers):
    resp = await client.patch(
        f"/api/tasks/{uuid.uuid4()}",
        json={"status": "done"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_delete_nonexistent_task_returns_404(client, auth_headers):
    resp = await client.delete(f"/api/tasks/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


# ── PATCH empty body returns 400 ────────────────────────────────────────────


async def test_patch_task_empty_body_returns_400(client, auth_headers):
    resp = await client.post("/api/tasks", json={
        "title": "Patch empty body test",
        "status": "todo",
        "priority": "low",
    }, headers=auth_headers)
    assert resp.status_code == 201
    task_id = resp.json()["id"]

    try:
        resp = await client.patch(f"/api/tasks/{task_id}", json={}, headers=auth_headers)
        assert resp.status_code == 400
        assert "No fields" in resp.json()["detail"]
    finally:
        await client.delete(f"/api/tasks/{task_id}", headers=auth_headers)


# ── Dispatch log CRUD ──────────────────────────────────────────────────────


async def test_post_dispatch_log_requires_auth(client):
    resp = await client.post("/api/dispatch/log", json={
        "persona": "test",
        "node": "slave0",
        "delegate": "Archie",
        "prompt_preview": "hello",
        "response_preview": "world",
        "elapsed_ms": 100,
        "status": "success",
    })
    assert resp.status_code == 401


async def test_post_dispatch_log_creates_entry(client, auth_headers):
    resp = await client.post("/api/dispatch/log", json={
        "persona": "test",
        "node": "slave0",
        "delegate": "Archie",
        "prompt_preview": "test prompt",
        "response_preview": "test response",
        "elapsed_ms": 250,
        "status": "success",
    }, headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert "created_at" in body


async def test_get_dispatch_log_returns_paginated_shape(client):
    resp = await client.get("/api/dispatch/log")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert "limit" in body
    assert "offset" in body
    assert isinstance(body["items"], list)
    assert body["limit"] == 50
    assert body["offset"] == 0


async def test_get_dispatch_log_filter_by_persona(client, auth_headers):
    await client.post("/api/dispatch/log", json={
        "persona": "test",
        "node": "slave0",
        "delegate": "Archie",
        "prompt_preview": "filter test",
        "response_preview": "",
        "elapsed_ms": 0,
        "status": "success",
    }, headers=auth_headers)

    resp = await client.get("/api/dispatch/log?persona=test")
    assert resp.status_code == 200
    body = resp.json()
    assert all(item["persona"] == "test" for item in body["items"])
    assert body["total"] >= 1


async def test_get_dispatch_log_filter_by_status(client, auth_headers):
    await client.post("/api/dispatch/log", json={
        "persona": "test",
        "node": "slave0",
        "delegate": "Archie",
        "prompt_preview": "status filter",
        "response_preview": "",
        "elapsed_ms": 0,
        "status": "success",
    }, headers=auth_headers)

    resp = await client.get("/api/dispatch/log?status=success")
    assert resp.status_code == 200
    body = resp.json()
    assert all(item["status"] == "success" for item in body["items"])
