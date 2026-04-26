"""PRD lifecycle tests — CRUD, status transitions, edge cases."""

import pytest


PRD_SLUG = "test-gym-tracker"
PRD_PAYLOAD = {
    "slug": PRD_SLUG,
    "title": "Gym Tracker App",
    "task_id": "task-123",
    "content": "# PRD: Gym Tracker\n\nGoals: track workouts.\n",
    "model": "google/gemini-2.5-flash",
}


@pytest.fixture(autouse=True)
def _cleanup_prd():
    """Remove test PRDs after each test."""
    yield
    import os
    import psycopg2
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        with conn.cursor() as cur:
            cur.execute("DELETE FROM prd WHERE slug LIKE 'test-%'")
        conn.commit()
        conn.close()
    except Exception:
        pass


async def test_create_prd(client, auth_headers):
    resp = await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == PRD_SLUG
    assert data["title"] == "Gym Tracker App"
    assert data["status"] == "pending"
    assert data["content"].startswith("# PRD: Gym Tracker")
    assert data["created_at"] is not None


async def test_get_prd(client, auth_headers):
    await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)

    resp = await client.get(f"/api/prd/{PRD_SLUG}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["slug"] == PRD_SLUG


async def test_get_prd_not_found(client, auth_headers):
    resp = await client.get("/api/prd/nonexistent-slug", headers=auth_headers)
    assert resp.status_code == 404


async def test_list_prds(client, auth_headers):
    await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)
    await client.post("/api/prd", headers=auth_headers, json={
        **PRD_PAYLOAD, "slug": "test-second-prd", "title": "Second",
    })

    resp = await client.get("/api/prd", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    assert len(data["items"]) >= 2


async def test_list_prds_filter_by_status(client, auth_headers):
    await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)

    resp = await client.get("/api/prd?status=pending", headers=auth_headers)
    assert resp.status_code == 200
    assert all(i["status"] == "pending" for i in resp.json()["items"])

    resp = await client.get("/api/prd?status=approved", headers=auth_headers)
    assert resp.status_code == 200
    slugs = [i["slug"] for i in resp.json()["items"]]
    assert PRD_SLUG not in slugs


async def test_approve_prd(client, auth_headers):
    await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)

    resp = await client.post(f"/api/prd/{PRD_SLUG}/approve", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    resp = await client.get(f"/api/prd/{PRD_SLUG}", headers=auth_headers)
    assert resp.json()["status"] == "approved"


async def test_reject_prd_with_feedback(client, auth_headers):
    await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)

    resp = await client.post(
        f"/api/prd/{PRD_SLUG}/reject",
        headers=auth_headers,
        json={"feedback": "Scope too broad, split into phases"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["feedback"] == "Scope too broad, split into phases"


async def test_reject_prd_without_feedback(client, auth_headers):
    await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)

    resp = await client.post(f"/api/prd/{PRD_SLUG}/reject", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


async def test_approve_nonexistent_prd(client, auth_headers):
    resp = await client.post("/api/prd/nonexistent/approve", headers=auth_headers)
    assert resp.status_code == 404


async def test_double_approve_returns_409(client, auth_headers):
    await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)
    await client.post(f"/api/prd/{PRD_SLUG}/approve", headers=auth_headers)

    resp = await client.post(f"/api/prd/{PRD_SLUG}/approve", headers=auth_headers)
    assert resp.status_code == 409


async def test_approve_rejected_returns_409(client, auth_headers):
    await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)
    await client.post(f"/api/prd/{PRD_SLUG}/reject", headers=auth_headers)

    resp = await client.post(f"/api/prd/{PRD_SLUG}/approve", headers=auth_headers)
    assert resp.status_code == 409


async def test_upsert_resets_status_to_pending(client, auth_headers):
    """Re-creating a PRD after rejection resets status to pending."""
    await client.post("/api/prd", headers=auth_headers, json=PRD_PAYLOAD)
    await client.post(
        f"/api/prd/{PRD_SLUG}/reject",
        headers=auth_headers,
        json={"feedback": "needs rework"},
    )

    resp = await client.post("/api/prd", headers=auth_headers, json={
        **PRD_PAYLOAD, "content": "# PRD v2\n\nRevised scope.\n",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    assert "Revised scope" in data["content"]
    assert data["feedback"] is None


async def test_slug_validation(client, auth_headers):
    """Slug must match ^[a-z0-9][a-z0-9-]*$."""
    bad_payloads = [
        {**PRD_PAYLOAD, "slug": "UPPERCASE"},
        {**PRD_PAYLOAD, "slug": "-starts-with-dash"},
        {**PRD_PAYLOAD, "slug": "has spaces"},
    ]
    for payload in bad_payloads:
        resp = await client.post("/api/prd", headers=auth_headers, json=payload)
        assert resp.status_code == 422, f"Expected 422 for slug={payload['slug']}"


async def test_requires_api_key(client):
    resp = await client.post("/api/prd", json=PRD_PAYLOAD)
    assert resp.status_code in (401, 403)

    resp = await client.get(f"/api/prd/{PRD_SLUG}")
    assert resp.status_code in (401, 403)
