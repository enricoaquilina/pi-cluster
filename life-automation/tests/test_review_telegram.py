"""Tests for review_telegram.py — interactive Telegram review.

Mocking policy: only Telegram API calls are mocked (external service).
Candidate staging, graduation, rejection all use real candidates.py
against real tmp_path filesystem.
"""
from __future__ import annotations

import json
import sys
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
    # Create entity so graduation works
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
    """Stage a decision candidate (needs review)."""
    return candidates.stage_fact(
        "pi-cluster", "project", "Chose Postgres over SQLite",
        "decision", "2026-04-24",
    )


@pytest.fixture
def staged_lesson(review_life):
    """Stage a lesson candidate (needs review)."""
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

    def test_sends_with_inline_keyboard(self, review_life, staged_decision):
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
        keyboard = payload["reply_markup"]["inline_keyboard"]
        assert len(keyboard) == 1
        assert len(keyboard[0]) == 2
        assert "grad:" in keyboard[0][0]["callback_data"]
        assert "rej:" in keyboard[0][1]["callback_data"]

    def test_returns_none_on_api_failure(self, review_life, staged_decision):
        def failing_api(method, payload, **kw):
            return {"ok": False}

        with patch.object(rt, "_tg_api", failing_api), \
             patch.object(rt, "TELEGRAM_TOKEN", "fake"), \
             patch.object(rt, "TELEGRAM_CHAT_ID", "123"):
            msg_id = rt.send_candidate(staged_decision)

        assert msg_id is None


class TestPollResponses:
    def test_graduate_via_callback(self, review_life, staged_decision):
        """Callback with grad: graduates candidate using real candidates.py."""
        cid = staged_decision["id"]
        api_calls = []

        call_count = [0]

        def fake_api(method, payload, **kw):
            api_calls.append((method, payload))
            if method == "getUpdates":
                call_count[0] += 1
                if call_count[0] == 1:
                    return {
                        "ok": True,
                        "result": [{
                            "update_id": 1,
                            "callback_query": {
                                "id": "cb1",
                                "from": {"id": 999},
                                "data": f"grad:{cid}",
                            },
                        }],
                    }
                return {"ok": True, "result": []}
            return {"ok": True, "result": {"message_id": 42}}

        with patch.object(rt, "_tg_api", fake_api):
            results = rt.poll_responses({cid: 100}, timeout=5)

        assert results[cid] == "graduated"

        # Verify real graduation happened
        all_cands = candidates._load_all()
        grad = [c for c in all_cands if c["id"] == cid]
        assert grad[0]["status"] == "graduated"

        # Verify items.json updated
        items = json.loads((review_life / "Projects" / "pi-cluster" / "items.json").read_text())
        assert any("Postgres" in i.get("fact", "") for i in items)

    def test_reject_via_callback(self, review_life, staged_lesson):
        """Callback with rej: rejects candidate using real candidates.py."""
        cid = staged_lesson["id"]
        call_count = [0]

        def fake_api(method, payload, **kw):
            if method == "getUpdates":
                call_count[0] += 1
                if call_count[0] == 1:
                    return {
                        "ok": True,
                        "result": [{
                            "update_id": 1,
                            "callback_query": {
                                "id": "cb1",
                                "from": {"id": 999},
                                "data": f"rej:{cid}",
                            },
                        }],
                    }
                return {"ok": True, "result": []}
            return {"ok": True, "result": {"message_id": 42}}

        with patch.object(rt, "_tg_api", fake_api):
            results = rt.poll_responses({cid: 100}, timeout=5)

        assert results[cid] == "rejected"

        all_cands = candidates._load_all()
        rej = [c for c in all_cands if c["id"] == cid]
        assert rej[0]["status"] == "rejected"

    def test_unauthorized_user_rejected(self, review_life, staged_decision):
        """Callback from unauthorized user is denied."""
        cid = staged_decision["id"]
        call_count = [0]
        answer_texts = []

        def fake_api(method, payload, **kw):
            if method == "getUpdates":
                call_count[0] += 1
                if call_count[0] == 1:
                    return {
                        "ok": True,
                        "result": [{
                            "update_id": 1,
                            "callback_query": {
                                "id": "cb1",
                                "from": {"id": 666},
                                "data": f"grad:{cid}",
                            },
                        }],
                    }
                return {"ok": True, "result": []}
            if method == "answerCallbackQuery":
                answer_texts.append(payload.get("text", ""))
            return {"ok": True, "result": {"message_id": 42}}

        with patch.object(rt, "_tg_api", fake_api), \
             patch.object(rt, "ALLOWED_USERS", {999}):
            results = rt.poll_responses({cid: 100}, timeout=5)

        assert cid not in results
        assert any("Not authorized" in t for t in answer_texts)

        # Candidate still pending
        all_cands = candidates._load_all()
        still_pending = [c for c in all_cands if c["id"] == cid]
        assert still_pending[0]["status"] == "pending"

    def test_reply_text_used_as_rationale(self, review_life, staged_decision):
        """Text reply to candidate message becomes graduation rationale."""
        cid = staged_decision["id"]
        call_count = [0]

        def fake_api(method, payload, **kw):
            if method == "getUpdates":
                call_count[0] += 1
                if call_count[0] == 1:
                    # First: text reply with rationale
                    return {
                        "ok": True,
                        "result": [{
                            "update_id": 1,
                            "message": {
                                "message_id": 201,
                                "from": {"id": 999},
                                "text": "Verified in production logs",
                                "reply_to_message": {"message_id": 100},
                            },
                        }],
                    }
                if call_count[0] == 2:
                    # Second: graduate button
                    return {
                        "ok": True,
                        "result": [{
                            "update_id": 2,
                            "callback_query": {
                                "id": "cb1",
                                "from": {"id": 999},
                                "data": f"grad:{cid}",
                            },
                        }],
                    }
                return {"ok": True, "result": []}
            return {"ok": True, "result": {"message_id": 42}}

        with patch.object(rt, "_tg_api", fake_api):
            results = rt.poll_responses({cid: 100}, timeout=5)

        assert results[cid] == "graduated"

        all_cands = candidates._load_all()
        grad = [c for c in all_cands if c["id"] == cid]
        assert grad[0]["rationale"] == "Verified in production logs"

    def test_timeout_returns_partial(self, review_life, staged_decision, staged_lesson):
        """Timeout returns whatever was resolved before deadline."""
        cid1 = staged_decision["id"]
        cid2 = staged_lesson["id"]
        call_count = [0]

        def fake_api(method, payload, **kw):
            if method == "getUpdates":
                call_count[0] += 1
                if call_count[0] == 1:
                    return {
                        "ok": True,
                        "result": [{
                            "update_id": 1,
                            "callback_query": {
                                "id": "cb1",
                                "from": {"id": 999},
                                "data": f"grad:{cid1}",
                            },
                        }],
                    }
                return {"ok": True, "result": []}
            return {"ok": True, "result": {"message_id": 42}}

        with patch.object(rt, "_tg_api", fake_api):
            results = rt.poll_responses(
                {cid1: 100, cid2: 101}, timeout=3,
            )

        assert results[cid1] == "graduated"
        assert cid2 not in results


class TestMainFlow:
    def test_no_candidates_exits_clean(self, review_life, capsys):
        rt.main.__wrapped__ = None  # ensure no caching
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

    def test_send_only_skips_polling(self, review_life, staged_decision, capsys):
        api_calls = []

        def fake_api(method, payload, **kw):
            api_calls.append(method)
            return {"ok": True, "result": {"message_id": 42}}

        with patch("sys.argv", ["review_telegram.py", "--send-only"]), \
             patch.object(rt, "_tg_api", fake_api), \
             patch.object(rt, "TELEGRAM_TOKEN", "fake"), \
             patch.object(rt, "TELEGRAM_CHAT_ID", "123"):
            rt.main()

        assert "sendMessage" in api_calls
        assert "getUpdates" not in api_calls

    def test_send_only_saves_state(self, review_life, staged_decision):
        """--send-only persists pending IDs to state file for later --check."""
        def fake_api(method, payload, **kw):
            return {"ok": True, "result": {"message_id": 42}}

        with patch("sys.argv", ["review_telegram.py", "--send-only"]), \
             patch.object(rt, "_tg_api", fake_api), \
             patch.object(rt, "TELEGRAM_TOKEN", "fake"), \
             patch.object(rt, "TELEGRAM_CHAT_ID", "123"), \
             patch.object(rt, "PENDING_STATE", review_life / "logs" / "review-telegram-pending.json"):
            rt.main()

        state_file = review_life / "logs" / "review-telegram-pending.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert staged_decision["id"] in state


class TestCheckMode:
    """--check polls for responses to previously sent messages."""

    def test_check_processes_callback(self, review_life, staged_decision):
        """--check graduates candidate from saved state."""
        cid = staged_decision["id"]
        state_file = review_life / "logs" / "review-telegram-pending.json"
        state_file.write_text(json.dumps({cid: 100}))

        call_count = [0]

        def fake_api(method, payload, **kw):
            if method == "getUpdates":
                call_count[0] += 1
                if call_count[0] == 1:
                    return {
                        "ok": True,
                        "result": [{
                            "update_id": 1,
                            "callback_query": {
                                "id": "cb1",
                                "from": {"id": 999},
                                "data": f"grad:{cid}",
                            },
                        }],
                    }
                return {"ok": True, "result": []}
            return {"ok": True, "result": {"message_id": 42}}

        with patch.object(rt, "_tg_api", fake_api), \
             patch.object(rt, "PENDING_STATE", state_file), \
             patch.object(rt, "TELEGRAM_TOKEN", "fake"), \
             patch.object(rt, "TELEGRAM_CHAT_ID", "123"):
            rt.cmd_check(timeout=5)

        # Candidate graduated via real candidates.py
        all_cands = candidates._load_all()
        grad = [c for c in all_cands if c["id"] == cid]
        assert grad[0]["status"] == "graduated"

        # State file cleared (no more pending)
        assert not state_file.exists()

    def test_check_noop_when_no_state(self, review_life, capsys):
        """--check exits silently when no pending state."""
        state_file = review_life / "logs" / "review-telegram-pending.json"
        with patch.object(rt, "PENDING_STATE", state_file):
            rt.cmd_check(timeout=1)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_check_removes_already_resolved(self, review_life, staged_decision):
        """--check cleans up candidates that were resolved outside Telegram."""
        cid = staged_decision["id"]
        # Graduate it directly (simulating someone used review.py manually)
        candidates.graduate(cid, rationale="manual")

        state_file = review_life / "logs" / "review-telegram-pending.json"
        state_file.write_text(json.dumps({cid: 100}))

        with patch.object(rt, "PENDING_STATE", state_file):
            rt.cmd_check(timeout=1)

        # State file cleared since no pending candidates left
        assert not state_file.exists()
