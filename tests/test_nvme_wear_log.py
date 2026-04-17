"""Tests for scripts/nvme_wear_log.py — parser + threshold logic."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "nvme_wear_log.py"

spec = importlib.util.spec_from_file_location("nvme_wear_log", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["nvme_wear_log"] = module
spec.loader.exec_module(module)


UTC = timezone.utc


def fake_smartctl_json(
    *,
    data_units_written: int = 988_170,
    percentage_used: int = 0,
    media_errors: int = 0,
    critical_warning: int = 0,
    passed: bool = True,
    temperature: int = 40,
) -> dict:
    return {
        "smart_status": {"passed": passed},
        "nvme_smart_health_information_log": {
            "data_units_written": data_units_written,
            "percentage_used": percentage_used,
            "media_errors": media_errors,
            "critical_warning": critical_warning,
            "temperature": temperature,
        },
    }


def snap(ts: datetime, **kwargs) -> "module.Snapshot":
    payload = fake_smartctl_json(**kwargs)
    return module.Snapshot.from_smartctl_json(payload, "/dev/nvme0n1", ts)


class TestParser:
    def test_data_units_to_bytes(self):
        s = snap(datetime.now(UTC), data_units_written=1000)
        assert s.bytes_written == 1000 * 512_000

    def test_roundtrip_through_history(self, tmp_path: Path):
        history = tmp_path / "wear.jsonl"
        original = snap(datetime(2026, 4, 17, 6, 0, tzinfo=UTC))
        module.append_history(history, original)
        rows = module.read_history(history)
        assert len(rows) == 1
        assert rows[0].data_units_written == original.data_units_written
        assert rows[0].ts == original.ts

    def test_corrupt_line_skipped(self, tmp_path: Path):
        history = tmp_path / "wear.jsonl"
        good = snap(datetime(2026, 4, 17, 6, 0, tzinfo=UTC))
        module.append_history(history, good)
        history.write_text(history.read_text() + "{not valid json\n")
        rows = module.read_history(history)
        assert len(rows) == 1


class TestEvaluate:
    def test_no_history_no_alerts(self):
        current = snap(datetime(2026, 4, 17, 6, 0, tzinfo=UTC))
        assert module.evaluate(current, []) == []

    def test_smart_failed_is_error(self):
        current = snap(datetime(2026, 4, 17, 6, 0, tzinfo=UTC), passed=False)
        alerts = module.evaluate(current, [])
        assert any(a["level"] == "ERROR" and a["reason"] == "smart_status_failed" for a in alerts)

    def test_media_errors_is_error(self):
        current = snap(datetime(2026, 4, 17, 6, 0, tzinfo=UTC), media_errors=3)
        alerts = module.evaluate(current, [])
        err = [a for a in alerts if a["reason"] == "media_errors"]
        assert err and err[0]["level"] == "ERROR" and err[0]["value"] == 3

    def test_critical_warning_is_error(self):
        current = snap(datetime(2026, 4, 17, 6, 0, tzinfo=UTC), critical_warning=1)
        alerts = module.evaluate(current, [])
        assert any(a["reason"] == "critical_warning" and a["level"] == "ERROR" for a in alerts)

    def test_pct_used_over_threshold_is_error(self):
        current = snap(datetime(2026, 4, 17, 6, 0, tzinfo=UTC), percentage_used=90)
        alerts = module.evaluate(current, [])
        err = [a for a in alerts if a["reason"] == "percentage_used"]
        assert err and err[0]["level"] == "ERROR"

    def test_pct_used_below_threshold_no_alert(self):
        current = snap(datetime(2026, 4, 17, 6, 0, tzinfo=UTC), percentage_used=50)
        assert module.evaluate(current, []) == []

    def test_write_delta_warn(self):
        # Baseline ~24h ago with 17 GB less written — normal day, no alert.
        now = datetime(2026, 4, 17, 6, 0, tzinfo=UTC)
        units_per_gib = 1024**3 // module.BYTES_PER_DATA_UNIT
        baseline_units = 988_170
        current = snap(now, data_units_written=baseline_units + 17 * units_per_gib)
        history = [snap(now - timedelta(hours=24), data_units_written=baseline_units)]
        assert module.evaluate(current, history) == []

    def test_write_delta_over_threshold_warns(self):
        # 30 GiB in 24h — over the 25 GiB WARN threshold.
        now = datetime(2026, 4, 17, 6, 0, tzinfo=UTC)
        units_per_gib = 1024**3 // module.BYTES_PER_DATA_UNIT
        baseline_units = 988_170
        current = snap(now, data_units_written=baseline_units + 30 * units_per_gib)
        history = [snap(now - timedelta(hours=24), data_units_written=baseline_units)]
        alerts = module.evaluate(current, history)
        warn = [a for a in alerts if a["reason"] == "write_delta_24h"]
        assert warn and warn[0]["level"] == "WARN"
        assert warn[0]["value_bytes"] >= module.WARN_DELTA_BYTES

    def test_recent_history_does_not_false_trigger(self):
        # History only 2h old — baseline window needs >=24h — so no delta alert.
        now = datetime(2026, 4, 17, 6, 0, tzinfo=UTC)
        units_per_gib = 1024**3 // module.BYTES_PER_DATA_UNIT
        baseline_units = 988_170
        current = snap(now, data_units_written=baseline_units + 100 * units_per_gib)
        history = [snap(now - timedelta(hours=2), data_units_written=baseline_units)]
        alerts = module.evaluate(current, history)
        assert not any(a["reason"] == "write_delta_24h" for a in alerts)


class TestMainExitCode:
    @pytest.fixture
    def patched(self, monkeypatch, tmp_path: Path):
        payload_ref = {"payload": fake_smartctl_json()}
        monkeypatch.setattr(module, "run_smartctl", lambda device: payload_ref["payload"])
        history = tmp_path / "wear.jsonl"
        return payload_ref, history

    def test_ok_exit_zero(self, patched, capsys):
        _, history = patched
        rc = module.main(["--history", str(history)])
        assert rc == 0
        event = json.loads(capsys.readouterr().out.strip())
        assert event["alerts"] == []

    def test_error_exit_two(self, patched, capsys):
        payload_ref, history = patched
        payload_ref["payload"] = fake_smartctl_json(media_errors=5)
        rc = module.main(["--history", str(history)])
        assert rc == 2
        event = json.loads(capsys.readouterr().out.strip())
        assert any(a["level"] == "ERROR" for a in event["alerts"])

    def test_dry_run_does_not_write_history(self, patched, capsys):
        _, history = patched
        rc = module.main(["--history", str(history), "--dry-run"])
        assert rc == 0
        capsys.readouterr()
        assert not history.exists()
