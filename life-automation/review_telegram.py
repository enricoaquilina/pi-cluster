#!/usr/bin/env python3
"""
Interactive Telegram review for staged fact candidates.

Sends each needs-review candidate as a Telegram message with inline
keyboard buttons (Graduate / Reject). Polls getUpdates for responses.

Usage:
    python3 review_telegram.py              # Interactive: send + poll (300s)
    python3 review_telegram.py --send-only  # Send messages, save state for later polling
    python3 review_telegram.py --check      # Poll for responses to previously sent messages
    python3 review_telegram.py --dry-run    # Print what would be sent
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import candidates

PENDING_STATE = candidates.LIFE_DIR / "logs" / "review-telegram-pending.json"

TELEGRAM_TOKEN = ""
TELEGRAM_CHAT_ID = ""
ALLOWED_USERS: set[int] = set()


def _load_credentials() -> None:
    global TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ALLOWED_USERS
    # Env vars first (set by .env.cluster in shell scripts)
    import os
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    # Fall back to credential files (used by heartbeat_agent)
    if not TELEGRAM_TOKEN:
        p = Path.home() / ".telegram-bot-token"
        if p.exists():
            TELEGRAM_TOKEN = p.read_text().strip()
    if not TELEGRAM_CHAT_ID:
        p = Path.home() / ".telegram-chat-id"
        if p.exists():
            TELEGRAM_CHAT_ID = p.read_text().strip()
    allowed_path = Path.home() / ".telegram-allowed-users"
    if allowed_path.exists():
        for line in allowed_path.read_text().splitlines():
            line = line.strip()
            if line and line.isdigit():
                ALLOWED_USERS.add(int(line))


_load_credentials()

POLL_TIMEOUT = 300
POLL_INTERVAL = 2


def _tg_api(method: str, payload: dict, *, timeout: int = 10) -> dict | None:
    if not TELEGRAM_TOKEN:
        return None
    try:
        data = json.dumps(payload).encode()
        req = Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except (URLError, OSError, json.JSONDecodeError) as e:
        print(f"[review-tg] API error ({method}): {e}", file=sys.stderr)
        return None


def send_candidate(c: dict, *, dry_run: bool = False) -> int | None:
    """Send one candidate as Telegram message with inline keyboard. Returns message_id."""
    cid = c["id"]
    entity = c["entity"]
    fact = c["fact"][:200]
    cat = c.get("category", "?")
    mentions = c.get("mentions", 1)
    source = c.get("source", "")

    text = (
        f"📋 <b>Review Candidate</b>\n\n"
        f"<b>ID:</b> <code>{cid}</code>\n"
        f"<b>Entity:</b> {entity}\n"
        f"<b>Category:</b> {cat}\n"
        f"<b>Mentions:</b> {mentions}\n"
        f"<b>Source:</b> {source}\n\n"
        f"<b>Fact:</b> {fact}\n\n"
        f"Reply to this message with rationale, or tap a button:"
    )

    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Graduate", "callback_data": f"grad:{cid}"},
                {"text": "❌ Reject", "callback_data": f"rej:{cid}"},
            ]
        ]
    }

    if dry_run:
        print(f"[dry] Would send: {entity} / {fact[:60]}")
        return None

    result = _tg_api("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": markup,
    })
    if result and result.get("ok"):
        msg_id = result["result"]["message_id"]
        print(f"[review-tg] Sent {cid} (msg_id={msg_id})")
        return msg_id
    return None


def _answer_callback(callback_query_id: str, text: str) -> None:
    _tg_api("answerCallbackQuery", {
        "callback_query_id": callback_query_id,
        "text": text,
    })


def _edit_message(msg_id: int, new_text: str) -> None:
    _tg_api("editMessageText", {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": msg_id,
        "text": new_text,
        "parse_mode": "HTML",
    })


def _is_authorized(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


def poll_responses(pending_ids: dict[str, int], *, timeout: int = POLL_TIMEOUT) -> dict[str, str]:
    """Poll Telegram for callback responses. Returns {candidate_id: action}."""
    results: dict[str, str] = {}
    reply_rationales: dict[int, str] = {}
    offset = 0
    deadline = time.time() + timeout

    # Track which message_ids map to which candidate_ids
    msg_to_cand = {msg_id: cid for cid, msg_id in pending_ids.items()}

    while pending_ids and time.time() < deadline:
        resp = _tg_api("getUpdates", {
            "offset": offset,
            "timeout": min(30, max(1, int(deadline - time.time()))),
            "allowed_updates": ["callback_query", "message"],
        }, timeout=35)

        if not resp or not resp.get("ok"):
            time.sleep(POLL_INTERVAL)
            continue

        for update in resp.get("result", []):
            offset = update["update_id"] + 1

            # Handle inline keyboard callbacks
            cb = update.get("callback_query")
            if cb:
                user_id = cb.get("from", {}).get("id", 0)
                if not _is_authorized(user_id):
                    _answer_callback(cb["id"], "⛔ Not authorized")
                    continue

                data = cb.get("data", "")
                if ":" not in data:
                    continue

                action, cid = data.split(":", 1)
                if cid not in pending_ids:
                    _answer_callback(cb["id"], "Already processed")
                    continue

                msg_id = pending_ids[cid]
                rationale = reply_rationales.get(msg_id, "")

                if action == "grad":
                    result = candidates.graduate(cid, rationale=rationale or "approved via Telegram")
                    if result:
                        _answer_callback(cb["id"], "✅ Graduated!")
                        _edit_message(msg_id, f"✅ <b>Graduated:</b> {result['fact'][:100]}\n<i>Rationale: {rationale or 'approved via Telegram'}</i>")
                        results[cid] = "graduated"
                    else:
                        _answer_callback(cb["id"], "❌ Failed — entity missing?")
                elif action == "rej":
                    result = candidates.reject(cid, rationale=rationale or "rejected via Telegram")
                    if result:
                        _answer_callback(cb["id"], "❌ Rejected")
                        _edit_message(msg_id, f"❌ <b>Rejected:</b> {result['fact'][:100]}\n<i>Rationale: {rationale or 'rejected via Telegram'}</i>")
                        results[cid] = "rejected"
                    else:
                        _answer_callback(cb["id"], "❌ Failed")

                    pending_ids.pop(cid, None)

                if action == "grad":
                    pending_ids.pop(cid, None)

            # Handle text replies (rationale for next button press)
            msg = update.get("message")
            if msg and msg.get("reply_to_message"):
                reply_to_id = msg["reply_to_message"].get("message_id")
                if reply_to_id in msg_to_cand:
                    reply_rationales[reply_to_id] = msg.get("text", "")[:500]

        if not resp.get("result"):
            time.sleep(POLL_INTERVAL)

    return results


def _save_pending(pending_ids: dict[str, int]) -> None:
    """Save pending {candidate_id: message_id} to state file for later polling."""
    PENDING_STATE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_STATE.write_text(json.dumps(pending_ids), encoding="utf-8")


def _load_pending() -> dict[str, int]:
    """Load pending state. Returns empty dict if no state."""
    if not PENDING_STATE.exists():
        return {}
    try:
        data = json.loads(PENDING_STATE.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError):
        return {}


def _clear_pending() -> None:
    if PENDING_STATE.exists():
        PENDING_STATE.unlink()


def cmd_check(timeout: int = 15) -> None:
    """Check for responses to previously sent review messages."""
    _load_credentials()
    pending_ids = _load_pending()
    if not pending_ids:
        return

    # Filter out candidates that are no longer pending
    still_pending = {}
    for cid, msg_id in pending_ids.items():
        all_cands = candidates._load_all()
        for c in all_cands:
            if c.get("id") == cid and c.get("status") == "pending":
                still_pending[cid] = msg_id
                break

    if not still_pending:
        _clear_pending()
        return

    results = poll_responses(still_pending, timeout=timeout)

    remaining = {k: v for k, v in still_pending.items() if k not in results}
    if remaining:
        _save_pending(remaining)
    else:
        _clear_pending()

    if results:
        candidates.write_review_queue()
        graduated = sum(1 for v in results.values() if v == "graduated")
        rejected = sum(1 for v in results.values() if v == "rejected")
        print(f"[review-tg] Processed {len(results)} responses: {graduated} graduated, {rejected} rejected, {len(remaining)} still pending")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive Telegram review for candidates")
    parser.add_argument("--send-only", action="store_true", help="Send messages, save state for later --check")
    parser.add_argument("--check", action="store_true", help="Poll for responses to previously sent messages")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent")
    parser.add_argument("--timeout", type=int, default=POLL_TIMEOUT, help="Poll timeout in seconds")
    args = parser.parse_args()

    if args.check:
        cmd_check(timeout=min(args.timeout, 15))
        return

    review_items = candidates.needs_review_candidates()
    if not review_items:
        print("No candidates need review.")
        return

    if not args.dry_run and not TELEGRAM_TOKEN:
        print("ERROR: Telegram not configured (set TELEGRAM_BOT_TOKEN env or ~/.telegram-bot-token)", file=sys.stderr)
        sys.exit(1)

    print(f"Sending {len(review_items)} candidates for review...")

    pending_ids: dict[str, int] = {}
    for c in review_items:
        msg_id = send_candidate(c, dry_run=args.dry_run)
        if msg_id is not None:
            pending_ids[c["id"]] = msg_id

    if args.send_only or args.dry_run:
        if pending_ids:
            _save_pending(pending_ids)
        print(f"Sent {len(pending_ids)} messages. Use Telegram buttons to review.")
        return

    if not pending_ids:
        print("No messages sent successfully.")
        return

    print(f"Polling for responses ({args.timeout}s timeout)...")
    results = poll_responses(pending_ids, timeout=args.timeout)

    graduated = sum(1 for v in results.values() if v == "graduated")
    rejected = sum(1 for v in results.values() if v == "rejected")
    remaining = len(review_items) - len(results)

    _clear_pending()
    print(f"\nResults: {graduated} graduated, {rejected} rejected, {remaining} still pending")

    if results:
        candidates.write_review_queue()
        print(f"Review queue updated: {candidates.REVIEW_QUEUE_PATH}")


if __name__ == "__main__":
    main()
