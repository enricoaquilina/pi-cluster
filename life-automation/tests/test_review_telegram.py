"""Tests for review_telegram.py — Telegram review notifications.

Mocking policy: only Telegram API calls are mocked (external service).
Candidate staging, graduation, rejection all use real candidates.py
against real tmp_path filesystem.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import candidates
import review_telegram as rt


@pytest.fixture
def review_life(tmp_path):
    """Real filesystem with staged candidates needing review."""
    life = tmp_path / "life"
    logs = life / "logs"
    logs.mkdir(parents=True)
    proj = life / "Projects" / "pi-cluster"
    proj.mkdir(parents=True)
    (proj / "items.json").write_text("[]")
    (proj / "summary.md").write_text("---\ntype: project\n---\n")

    with patch.object(candidates, "LIFE_DIR", life), \
         patch.object(candidates, "CANDIDATES_PATH", logs / "candidates.jsonl"), \
         patch.object(candidates, "REVIEW_QUEUE_PATH", life / "REVIEW_QUEUE.md"):
        candidates._counter = 0
        yield life


@pytest.fixture
def staged_decision(review_life):
    return candidates.stage_fact(
        "pi-cluster", "project", "Chose Postgres over SQLite",
        "decision", "2026-04-24",
    )


@pytest.fixture
def staged_lesson(review_life):
    return candidates.stage_fact(
        "pi-cluster", "project", "Always verify backups before migration",
        "lesson", "2026-04-24",
    )


class TestSendCandidate:
    def test_dry_run_prints_summary(self, review_life, staged_decision, capsys):
        result = rt.send_candidate(staged_decision, dry_run=True)
        assert result is None
        captured = capsys.readouterr()
        assert "pi-cluster" in captured.out
        assert "Chose Postgres" in captured.out

    def test_sends_message_with_auto_graduate_info(self, review_life, staged_decision):
        sent_payloads = []

        def fake_api(method, payload, **kw):
            sent_payloads.append((method, payload))
            return {"ok": True, "result": {"message_id": 42}}

        with patch.object(rt, "_tg_api", fake_api), \
             patch.object(rt, "TELEGRAM_TOKEN", "fake"), \
             patch.object(rt, "TELEGRAM_CHAT_ID", "123"):
            msg_id = rt.send_candidate(staged_decision)

        assert msg_id == 42
        method, payload = sent_payloads[0]
        assert method == "sendMessage"
        assert "Review Candidate" in payload["text"]
        assert "pi-cluster" in payload["text"]
        assert "Auto-graduates in" in payload["text"]
        assert "reject" in payload["text"]

    def test_sends_inline_keyboard_buttons(self, review_life, staged_decision):
        sent_payloads = []

        def fake_api(method, payload, **kw):
            sent_payloads.append(payload)
            return {"ok": True, "result": {"message_id": 42}}

        with patch.object(rt, "_tg_api", fake_api), \
             patch.object(rt, "TELEGRAM_TOKEN", "fake"), \
             patch.object(rt, "TELEGRAM_CHAT_ID", "123"):
            rt.send_candidate(staged_decision)

        markup = sent_payloads[0]["reply_markup"]
        buttons = markup["inline_keyboard"][0]
        assert len(buttons) == 2
        assert buttons[0]["callback_data"].startswith("grad:cand-")
        assert buttons[1]["callback_data"].startswith("rej:cand-")

    def test_returns_none_on_api_failure(self, review_life, staged_decision):
        def failing_api(method, payload, **kw):
            return {"ok": False}

        with patch.object(rt, "_tg_api", failing_api), \
             patch.object(rt, "TELEGRAM_TOKEN", "fake"), \
             patch.object(rt, "TELEGRAM_CHAT_ID", "123"):
            msg_id = rt.send_candidate(staged_decision)

        assert msg_id is None


class TestSendSummary:
    def test_summary_includes_count(self, review_life, staged_decision, staged_lesson):
        sent_payloads = []

        def fake_api(method, payload, **kw):
            sent_payloads.append(payload)
            return {"ok": True, "result": {"message_id": 99}}

        items = candidates.needs_review_candidates()
        with patch.object(rt, "_tg_api", fake_api), \
             patch.object(rt, "TELEGRAM_TOKEN", "fake"), \
             patch.object(rt, "TELEGRAM_CHAT_ID", "123"):
            rt.send_summary(items)

        assert "2 candidates need review" in sent_payloads[0]["text"]

    def test_summary_dry_run(self, review_life, staged_decision, capsys):
        items = candidates.needs_review_candidates()
        rt.send_summary(items, dry_run=True)
        captured = capsys.readouterr()
        assert "Summary" in captured.out


class TestDaysUntilAuto:
    def test_fresh_candidate(self, review_life, staged_decision):
        days = rt._days_until_auto(staged_decision)
        assert days == candidates.AUTO_GRADUATE_DAYS

    def test_old_candidate(self, review_life):
        old_created = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        c = candidates.stage_fact(
            "pi-cluster", "project", "Old decision", "decision", "2026-04-20",
        )
        # Manually age the candidate
        all_cands = candidates._load_all()
        for entry in all_cands:
            if entry["id"] == c["id"]:
                entry["created"] = old_created
        candidates._save_all(all_cands)
        c["created"] = old_created

        days = rt._days_until_auto(c)
        assert days == 2

    def test_expired_candidate(self, review_life):
        old_created = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        c = candidates.stage_fact(
            "pi-cluster", "project", "Expired decision", "decision", "2026-04-15",
        )
        all_cands = candidates._load_all()
        for entry in all_cands:
            if entry["id"] == c["id"]:
                entry["created"] = old_created
        candidates._save_all(all_cands)
        c["created"] = old_created

        days = rt._days_until_auto(c)
        assert days == 0


class TestMainFlow:
    def test_no_candidates_exits_clean(self, review_life, capsys):
        with patch("sys.argv", ["review_telegram.py", "--dry-run"]):
            rt.main()
        captured = capsys.readouterr()
        assert "No candidates need review" in captured.out

    def test_dry_run_sends_nothing(self, review_life, staged_decision, staged_lesson, capsys):
        with patch("sys.argv", ["review_telegram.py", "--dry-run"]):
            rt.main()
        captured = capsys.readouterr()
        assert "[dry]" in captured.out
        assert "pi-cluster" in captured.out

    def test_sends_all_candidates(self, review_life, staged_decision, staged_lesson):
        sent_methods = []

        def fake_api(method, payload, **kw):
            sent_methods.append(method)
            return {"ok": True, "result": {"message_id": len(sent_methods)}}

        with patch("sys.argv", ["review_telegram.py"]), \
             patch.object(rt, "_tg_api", fake_api), \
             patch.object(rt, "TELEGRAM_TOKEN", "fake"), \
             patch.object(rt, "TELEGRAM_CHAT_ID", "123"):
            rt.main()

        # 2 candidates + 1 summary = 3 sendMessage calls
        assert sent_methods.count("sendMessage") == 3
