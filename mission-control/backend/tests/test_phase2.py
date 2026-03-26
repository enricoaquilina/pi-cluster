"""Phase 2 verification tests: enhanced health, kiosk watchdog, CI readiness."""

import os
import subprocess

import pytest


async def test_health_returns_db_status(client):
    """Health endpoint includes DB connectivity check (2A)."""
    resp = await client.get("/health")
    data = resp.json()
    assert "db" in data, "Health response missing 'db' field"
    assert data["db"] == "ok", f"DB check failed: {data['db']}"


async def test_health_returns_uptime(client):
    """Health endpoint includes uptime in seconds (2A)."""
    resp = await client.get("/health")
    data = resp.json()
    assert "uptime_seconds" in data, "Health response missing 'uptime_seconds'"
    assert isinstance(data["uptime_seconds"], int)
    assert data["uptime_seconds"] >= 0


async def test_health_returns_sse_subscribers(client):
    """Health endpoint includes SSE subscriber count (2A)."""
    resp = await client.get("/health")
    data = resp.json()
    assert "sse_subscribers" in data, "Health response missing 'sse_subscribers'"
    assert isinstance(data["sse_subscribers"], int)
    assert data["sse_subscribers"] >= 0


async def test_health_sse_count_increases_with_subscriber(client, event_bus_instance):
    """SSE subscriber count in health reflects actual connections (2A)."""
    resp1 = await client.get("/health")
    before = resp1.json()["sse_subscribers"]

    q = event_bus_instance.subscribe()
    try:
        resp2 = await client.get("/health")
        during = resp2.json()["sse_subscribers"]
        assert during == before + 1
    finally:
        event_bus_instance.unsubscribe(q)


async def test_health_backward_compat(client):
    """Health still returns status=ok for backward compat with smoke test (2A)."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_conftest_no_real_credentials():
    """conftest.py does not contain real credentials (2D)."""
    conftest_path = os.path.join(os.path.dirname(__file__), "conftest.py")
    content = open(conftest_path).read()
    # Should not contain the real DB password or API key
    assert "O9ou9AI9E" not in content, "Real DB password found in conftest.py"
    assert "860e75126051c283" not in content, "Real API key found in conftest.py"


def test_ci_workflow_exists():
    """GitHub Actions workflow for MC tests exists (2D)."""
    # Check in the repo on master
    result = subprocess.run(
        ["ssh", "master", "test -f ~/homelab/.github/workflows/mc-tests.yml && echo EXISTS"],
        capture_output=True, text=True, timeout=10,
    )
    assert "EXISTS" in result.stdout, "mc-tests.yml workflow not found in repo"


def test_kiosk_service_file_exists():
    """Kiosk systemd service template exists in repo (2B)."""
    result = subprocess.run(
        ["ssh", "master", "test -f ~/homelab/templates/mc-kiosk.service.j2 && echo EXISTS"],
        capture_output=True, text=True, timeout=10,
    )
    assert "EXISTS" in result.stdout, "mc-kiosk.service.j2 template not found"


def test_kiosk_service_has_restart():
    """Kiosk service has Restart=always for crash recovery (2B)."""
    result = subprocess.run(
        ["ssh", "master", "grep 'Restart=always' ~/homelab/templates/mc-kiosk.service.j2"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, "Kiosk service missing Restart=always"


def test_old_desktop_autostart_removed():
    """Old .desktop autostart file removed from repo (2B)."""
    result = subprocess.run(
        ["ssh", "master", "test -f ~/homelab/files/mc-kiosk.desktop && echo EXISTS || echo GONE"],
        capture_output=True, text=True, timeout=10,
    )
    assert "GONE" in result.stdout, "Old mc-kiosk.desktop still exists in repo"
