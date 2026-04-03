"""Tests for ingest_dispatches.py — MC API ingestion, date filtering, error handling."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "ingest_dispatches.py"
TODAY = "2026-04-02"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    """Minimal ~/life/ tree."""
    (tmp_path / "Daily" / "2026" / "04").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    return tmp_path


def _make_dispatch(persona: str, created_at: str, status: str = "success",
                   elapsed_ms: int = 10000, prompt: str = "Task: Test Task\nContext: test") -> dict:
    return {
        "id": f"id-{persona}-{created_at[:10]}",
        "persona": persona,
        "node": "gateway",
        "delegate": "coder",
        "fallback": False,
        "original_node": None,
        "prompt_preview": prompt,
        "response_preview": "Response text here",
        "elapsed_ms": elapsed_ms,
        "status": status,
        "error_detail": None,
        "created_at": created_at,
    }


def _make_agent_run(ts: str, title: str, action: str, persona: str) -> dict:
    return {
        "timestamp": ts,
        "items_checked": 1,
        "actions": [{"title": title, "source": "mc_task", "confidence": "medium",
                      "confidence_reason": "test", "persona": persona, "action": action}],
        "nodes": {},
        "budget_spent_today": 0,
    }


class MCHandler(BaseHTTPRequestHandler):
    """Real HTTP handler serving canned MC API responses."""
    response_data = {"items": [], "total": 0, "limit": 200, "offset": 0}
    status_code = 200

    def do_GET(self):
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.response_data).encode())

    def log_message(self, *args):
        pass  # silence logs


@pytest.fixture
def mc_server():
    """Real local HTTP server for MC API tests."""
    handler = type("H", (MCHandler,), {"response_data": MCHandler.response_data, "status_code": 200})
    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{port}/api", handler
    server.shutdown()


def _run(life_dir: Path, mc_url: str = None, extra_args: list = None,
         today: str = TODAY) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = today
    if mc_url:
        env["MC_API_URL"] = mc_url
    cmd = [sys.executable, str(SCRIPT)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


def _output_file(life_dir: Path, today: str = TODAY) -> Path:
    parts = today.split("-")
    return life_dir / "Daily" / parts[0] / parts[1] / f"maxwell-{today}.md"


# ── Core Tests ───────────────────────────────────────────────────────────────


class TestBasicIngestion:
    def test_basic_dispatch_ingestion(self, life_dir, mc_server):
        """2 today + 1 yesterday → only today's in output."""
        _, url, handler = mc_server
        handler.response_data = {
            "items": [
                _make_dispatch("Pixel", f"{TODAY}T10:30:00+00:00"),
                _make_dispatch("Scout", f"{TODAY}T11:00:00+00:00"),
                _make_dispatch("Archie", "2026-04-01T15:00:00+00:00"),
            ],
            "total": 3, "limit": 200, "offset": 0,
        }
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "Pixel" in content
        assert "Scout" in content
        assert "Archie" not in content

    def test_dry_run_no_write(self, life_dir, mc_server):
        """--dry-run outputs to stdout, no file on disk."""
        _, url, handler = mc_server
        handler.response_data = {
            "items": [_make_dispatch("Pixel", f"{TODAY}T10:00:00+00:00")],
            "total": 1, "limit": 200, "offset": 0,
        }
        result = _run(life_dir, url, extra_args=["--dry-run"])
        assert result.returncode == 0
        assert "Pixel" in result.stdout
        assert not _output_file(life_dir).exists()

    def test_empty_dispatch_log(self, life_dir, mc_server):
        """Empty dispatch log → file with 'no activity' message."""
        _, url, handler = mc_server
        handler.response_data = {"items": [], "total": 0, "limit": 200, "offset": 0}
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "No dispatch activity" in content

    def test_idempotent_overwrite(self, life_dir, mc_server):
        """Running twice with same data produces identical file."""
        _, url, handler = mc_server
        handler.response_data = {
            "items": [_make_dispatch("Pixel", f"{TODAY}T10:00:00+00:00")],
            "total": 1, "limit": 200, "offset": 0,
        }
        _run(life_dir, url)
        content1 = _output_file(life_dir).read_text()
        _run(life_dir, url)
        content2 = _output_file(life_dir).read_text()
        assert content1 == content2


# ── Timezone Edge Cases ──────────────────────────────────────────────────────


class TestTimezones:
    def test_utc_midnight_boundary(self, life_dir, mc_server):
        """UTC 23:30 on Apr 1 = Apr 2 01:30 CEST → appears in Apr 2 output."""
        _, url, handler = mc_server
        handler.response_data = {
            "items": [_make_dispatch("Pixel", "2026-04-01T23:30:00+00:00")],
            "total": 1, "limit": 200, "offset": 0,
        }
        _run(life_dir, url, today="2026-04-02")
        content = _output_file(life_dir, "2026-04-02").read_text()
        assert "Pixel" in content

    def test_local_date_override(self, life_dir, mc_server):
        """CONSOLIDATION_DATE=2026-03-31 filters to that date."""
        _, url, handler = mc_server
        handler.response_data = {
            "items": [
                _make_dispatch("Pixel", "2026-03-31T12:00:00+00:00"),
                _make_dispatch("Scout", f"{TODAY}T12:00:00+00:00"),
            ],
            "total": 2, "limit": 200, "offset": 0,
        }
        (life_dir / "Daily" / "2026" / "03").mkdir(parents=True, exist_ok=True)
        _run(life_dir, url, today="2026-03-31")
        out = life_dir / "Daily" / "2026" / "03" / "maxwell-2026-03-31.md"
        content = out.read_text()
        assert "Pixel" in content
        assert "Scout" not in content


# ── Error Handling ───────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_mc_api_unreachable(self, life_dir):
        """Bad URL → exits 0, writes 'unreachable' note."""
        result = _run(life_dir, "http://127.0.0.1:19999/api")
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "unreachable" in content.lower() or "unavailable" in content.lower()

    def test_mc_api_http_500(self, life_dir, mc_server):
        """Server returns 500 → exits 0, writes error note."""
        _, url, handler = mc_server
        handler.status_code = 500
        handler.response_data = {"error": "Internal Server Error"}
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "unreachable" in content.lower() or "unavailable" in content.lower()

    def test_agent_runs_malformed_json(self, life_dir, mc_server):
        """Malformed agent-runs.json → exits 0, dispatch section still written."""
        _, url, handler = mc_server
        handler.response_data = {
            "items": [_make_dispatch("Pixel", f"{TODAY}T10:00:00+00:00")],
            "total": 1, "limit": 200, "offset": 0,
        }
        (life_dir / "logs" / "agent-runs.json").write_text("{invalid json", encoding="utf-8")
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "Pixel" in content

    def test_agent_runs_missing(self, life_dir, mc_server):
        """No agent-runs.json → exits 0, only dispatch section."""
        _, url, handler = mc_server
        handler.response_data = {
            "items": [_make_dispatch("Pixel", f"{TODAY}T10:00:00+00:00")],
            "total": 1, "limit": 200, "offset": 0,
        }
        # Don't create agent-runs.json
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "Pixel" in content
        assert "Heartbeat" not in content

    def test_agent_runs_empty_array(self, life_dir, mc_server):
        """Empty agent-runs.json → no heartbeat section."""
        _, url, handler = mc_server
        handler.response_data = {"items": [], "total": 0, "limit": 200, "offset": 0}
        (life_dir / "logs" / "agent-runs.json").write_text("[]", encoding="utf-8")
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "Heartbeat" not in content


# ── Data Integrity ───────────────────────────────────────────────────────────


class TestDataIntegrity:
    def test_unicode_in_dispatch(self, life_dir, mc_server):
        """Emoji/unicode in persona preview → written correctly."""
        _, url, handler = mc_server
        handler.response_data = {
            "items": [_make_dispatch("Pixel", f"{TODAY}T10:00:00+00:00",
                                     prompt="Task: Fix 🐛 in gateway\nContext: emoji test")],
            "total": 1, "limit": 200, "offset": 0,
        }
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text(encoding="utf-8")
        assert "🐛" in content

    def test_output_dir_created(self, tmp_path):
        """Daily/YYYY/MM/ doesn't exist → mkdir -p creates it."""
        life_dir = tmp_path / "life"
        (life_dir / "logs").mkdir(parents=True)
        # Don't create Daily/2026/04/ — script should create it
        result = _run(life_dir, "http://127.0.0.1:19999/api")
        assert result.returncode == 0
        assert (life_dir / "Daily" / "2026" / "04" / f"maxwell-{TODAY}.md").exists()


# ── API Edge Cases ───────────────────────────────────────────────────────────


class TestAPIEdgeCases:
    def test_dispatch_item_null_created_at(self, life_dir, mc_server):
        """Item with created_at=null → skipped, others processed."""
        _, url, handler = mc_server
        null_item = _make_dispatch("BadItem", f"{TODAY}T10:00:00+00:00")
        null_item["created_at"] = None
        handler.response_data = {
            "items": [
                null_item,
                _make_dispatch("Pixel", f"{TODAY}T11:00:00+00:00"),
            ],
            "total": 2, "limit": 200, "offset": 0,
        }
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "Pixel" in content
        assert "BadItem" not in content

    def test_dispatch_item_missing_persona(self, life_dir, mc_server):
        """Item without persona field → uses 'unknown' fallback."""
        _, url, handler = mc_server
        item = _make_dispatch("", f"{TODAY}T10:00:00+00:00")
        del item["persona"]
        handler.response_data = {
            "items": [item],
            "total": 1, "limit": 200, "offset": 0,
        }
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "unknown" in content.lower()

    def test_pagination_warning(self, life_dir, mc_server):
        """API returns total > limit → warning in stderr."""
        _, url, handler = mc_server
        handler.response_data = {
            "items": [_make_dispatch("Pixel", f"{TODAY}T10:00:00+00:00")],
            "total": 250, "limit": 200, "offset": 0,
        }
        result = _run(life_dir, url)
        assert result.returncode == 0
        assert "250" in result.stderr or "WARNING" in result.stderr

    def test_concurrent_agent_runs_read(self, life_dir, mc_server):
        """Partial/truncated JSON in agent-runs.json → handled gracefully."""
        _, url, handler = mc_server
        handler.response_data = {"items": [], "total": 0, "limit": 200, "offset": 0}
        # Write truncated JSON (simulating concurrent write)
        (life_dir / "logs" / "agent-runs.json").write_text('[{"timestamp": "2026-04-02T10:00', encoding="utf-8")
        result = _run(life_dir, url)
        assert result.returncode == 0

    def test_heartbeat_actions_included(self, life_dir, mc_server):
        """Agent-runs with today's actions → heartbeat section written."""
        _, url, handler = mc_server
        handler.response_data = {"items": [], "total": 0, "limit": 200, "offset": 0}
        runs = [_make_agent_run(f"{TODAY}T18:00:01", "Gym Tracker App", "awaiting_approval", "Pixel")]
        (life_dir / "logs" / "agent-runs.json").write_text(json.dumps(runs), encoding="utf-8")
        result = _run(life_dir, url)
        assert result.returncode == 0
        content = _output_file(life_dir).read_text()
        assert "Heartbeat" in content
        assert "Gym Tracker App" in content
        assert "Pixel" in content
