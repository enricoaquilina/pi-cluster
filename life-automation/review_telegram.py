#!/usr/bin/env python3
"""
Telegram notifications for staged fact candidates needing review.

Sends each needs-review candidate to Telegram. Unreviewed candidates
auto-graduate after 7 days (silence = approval).

Usage:
    python3 review_telegram.py              # Send review notifications
    python3 review_telegram.py --dry-run    # Print what would be sent

Review via CLI:
    python3 review.py list
    python3 review.py graduate <id> "rationale"
    python3 review.py reject <id> "reason"
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import candidates

TELEGRAM_TOKEN = ""
TELEGRAM_CHAT_ID = ""


def _load_credentials() -> None:
    global TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    import os
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not TELEGRAM_TOKEN:
        p = Path.home() / ".telegram-bot-token"
        if p.exists():
            TELEGRAM_TOKEN = p.read_text().strip()
    if not TELEGRAM_CHAT_ID:
        p = Path.home() / ".telegram-chat-id"
        if p.exists():
            TELEGRAM_CHAT_ID = p.read_text().strip()


_load_credentials()


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


def _days_until_auto(c: dict) -> int:
    """Days until candidate auto-graduates."""
    try:
        created = datetime.fromisoformat(c["created"]).date()
    except (KeyError, ValueError):
        return candidates.AUTO_GRADUATE_DAYS
    age = (date.today() - created).days
    return max(0, candidates.AUTO_GRADUATE_DAYS - age)


def send_candidate(c: dict, *, dry_run: bool = False) -> int | None:
    """Send one candidate as Telegram notification. Returns message_id."""
    cid = c["id"]
    entity = c["entity"]
    fact = c["fact"][:200]
    cat = c.get("category", "?")
    mentions = c.get("mentions", 1)
    source = c.get("source", "")
    days_left = _days_until_auto(c)

    text = (
        f"📋 <b>Review Candidate</b>\n\n"
        f"<b>ID:</b> <code>{cid}</code>\n"
        f"<b>Entity:</b> {entity}\n"
        f"<b>Category:</b> {cat}\n"
        f"<b>Mentions:</b> {mentions}\n"
        f"<b>Source:</b> {source}\n\n"
        f"<b>Fact:</b> {fact}\n\n"
        f"⏱ Auto-graduates in {days_left} days if not rejected.\n"
        f"To reject: <code>python3 ~/life/scripts/review.py reject {cid} \"reason\"</code>"
    )

    if dry_run:
        print(f"[dry] Would send: {entity} / {fact[:60]} ({days_left}d left)")
        return None

    result = _tg_api("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    })
    if result and result.get("ok"):
        msg_id = result["result"]["message_id"]
        print(f"[review-tg] Sent {cid} (msg_id={msg_id})")
        return msg_id
    return None


def send_summary(review_items: list[dict], *, dry_run: bool = False) -> int | None:
    """Send aggregate summary after individual candidates."""
    total = len(review_items)
    auto_soon = [c for c in review_items if _days_until_auto(c) <= 2]

    lines = [
        f"📊 <b>Review Summary:</b> {total} candidates need review",
        "",
        f"⏱ {len(auto_soon)} will auto-graduate within 2 days" if auto_soon else "",
        "",
        "To review all: <code>python3 ~/life/scripts/review.py list</code>",
        "To reject: <code>python3 ~/life/scripts/review.py reject ID \"reason\"</code>",
        "To graduate early: <code>python3 ~/life/scripts/review.py graduate ID \"rationale\"</code>",
    ]
    text = "\n".join(line for line in lines if line or line == "")

    if dry_run:
        print(f"[dry] Summary: {total} candidates")
        return None

    result = _tg_api("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    })
    if result and result.get("ok"):
        return result["result"]["message_id"]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram review notifications")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent")
    args = parser.parse_args()

    review_items = candidates.needs_review_candidates()
    if not review_items:
        print("No candidates need review.")
        return

    if not args.dry_run and not TELEGRAM_TOKEN:
        print("ERROR: Telegram not configured (set TELEGRAM_BOT_TOKEN env or ~/.telegram-bot-token)", file=sys.stderr)
        sys.exit(1)

    print(f"Sending {len(review_items)} candidates for review...")

    sent = 0
    for c in review_items:
        msg_id = send_candidate(c, dry_run=args.dry_run)
        if msg_id is not None:
            sent += 1

    send_summary(review_items, dry_run=args.dry_run)
    print(f"Sent {sent} review notifications.")


if __name__ == "__main__":
    main()
