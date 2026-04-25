#!/usr/bin/env python3
"""
Candidate staging system for ~/life/ fact curation.

Facts from nightly extraction land here before promotion to items.json.
Most auto-graduate; contradictions and decisions need human review.

Files:
    ~/life/logs/candidates.jsonl — append-only staging area
    ~/life/REVIEW_QUEUE.md       — auto-generated human-readable queue
"""
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
CANDIDATES_PATH = LIFE_DIR / "logs" / "candidates.jsonl"
REVIEW_QUEUE_PATH = LIFE_DIR / "REVIEW_QUEUE.md"
FLOCK_TIMEOUT = 2.0

REVIEW_CATEGORIES = {"decision", "lesson"}
AUTO_GRADUATE_CATEGORIES = {
    "deployment", "configuration", "event", "tool", "preference",
    "relationship", "milestone", "capability", "architecture",
}

_counter = 0


def _next_id() -> str:
    global _counter
    if _counter == 0:
        # Resume from highest existing ID to avoid collisions across process restarts
        today_prefix = f"cand-{date.today().isoformat()}-"
        for entry in _load_all():
            eid = entry.get("id", "")
            if eid.startswith(today_prefix):
                try:
                    num = int(eid[len(today_prefix):])
                    _counter = max(_counter, num)
                except ValueError:
                    pass
    _counter += 1
    return f"cand-{date.today().isoformat()}-{_counter:03d}"


def _load_all() -> list[dict]:
    if not CANDIDATES_PATH.exists():
        return []
    entries = []
    for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _save_all(entries: list[dict]) -> None:
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATES_PATH, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _similar(a: str, b: str) -> bool:
    a_lower, b_lower = a.lower().strip(), b.lower().strip()
    if a_lower == b_lower:
        return True
    words_a = set(a_lower.split())
    words_b = set(b_lower.split())
    if not words_a or not words_b:
        return False
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union) >= 0.7


def stage_fact(
    entity: str,
    entity_type: str,
    fact: str,
    category: str,
    fact_date: str,
    *,
    source: str = "",
    extracted_by: str = "nightly-consolidate",
    supersedes: str | None = None,
    contradicts_existing: bool = False,
    temporal: bool = False,
) -> dict:
    """Stage a fact as candidate. Deduplicates against pending candidates.

    Returns the candidate dict (new or existing with bumped mentions).
    """
    all_candidates = _load_all()

    for c in all_candidates:
        if c.get("status") != "pending":
            continue
        if c.get("entity") != entity:
            continue
        if _similar(c.get("fact", ""), fact):
            c["mentions"] = c.get("mentions", 1) + 1
            c["last_seen"] = fact_date
            _save_all(all_candidates)
            return c

    needs_review = (
        contradicts_existing
        or category in REVIEW_CATEGORIES
    )

    candidate = {
        "id": _next_id(),
        "entity": entity,
        "entity_type": entity_type,
        "fact": fact,
        "category": category,
        "date": fact_date,
        "status": "pending",
        "confidence": "single",
        "temporal": temporal,
        "source": source or f"daily/{fact_date}",
        "extracted_by": extracted_by,
        "created": datetime.now(timezone.utc).isoformat(),
        "mentions": 1,
        "last_seen": fact_date,
        "graduated_at": None,
        "rationale": None,
        "supersedes": supersedes,
        "needs_review": needs_review,
    }

    all_candidates.append(candidate)
    _save_all(all_candidates)
    return candidate


def pending_candidates() -> list[dict]:
    return [c for c in _load_all() if c.get("status") == "pending"]


def needs_review_candidates() -> list[dict]:
    return [c for c in pending_candidates() if c.get("needs_review")]


AUTO_GRADUATE_DAYS = 7


def auto_graduatable() -> list[dict]:
    result = []
    today = date.today()
    for c in pending_candidates():
        if c.get("needs_review"):
            # Time-based: auto-graduate after AUTO_GRADUATE_DAYS if not rejected
            try:
                created = datetime.fromisoformat(c["created"]).date()
            except (KeyError, ValueError):
                continue
            age_days = (today - created).days
            if age_days >= AUTO_GRADUATE_DAYS:
                result.append(c)
            continue
        if c.get("mentions", 1) >= 2:
            result.append(c)
        elif c.get("category") in AUTO_GRADUATE_CATEGORIES:
            result.append(c)
    return result


def graduate(candidate_id: str, rationale: str = "") -> dict | None:
    """Graduate a candidate to items.json. Returns the candidate or None."""
    all_candidates = _load_all()
    target = None
    for c in all_candidates:
        if c.get("id") == candidate_id:
            target = c
            break
    if not target or target.get("status") != "pending":
        return None

    entity = target["entity"]
    entity_type = target.get("entity_type", "project")

    type_to_dir = {
        "person": "People", "project": "Projects", "company": "Companies",
    }
    base = type_to_dir.get(entity_type, "Resources")
    items_path = LIFE_DIR / base / entity / "items.json"

    if not items_path.exists():
        return None

    items = []
    try:
        items = json.loads(items_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        items = []

    if target.get("supersedes"):
        for item in items:
            if _similar(item.get("fact", ""), target["supersedes"]):
                item["superseded_by"] = target["fact"]
                item["superseded_date"] = target["date"]
                item["confidence"] = "superseded"
                break

    for item in items:
        if item.get("confidence") == "superseded":
            continue
        if _similar(item.get("fact", ""), target["fact"]):
            item["mentions"] = item.get("mentions", 1) + target.get("mentions", 1)
            item["last_seen"] = target["date"]
            if item["mentions"] >= 4:
                item["confidence"] = "established"
            elif item["mentions"] >= 2:
                item["confidence"] = "confirmed"
            break
    else:
        items.append({
            "date": target["date"],
            "fact": target["fact"],
            "category": target["category"],
            "source": target.get("source", ""),
            "confidence": "confirmed" if target.get("mentions", 1) >= 2 else "single",
            "mentions": target.get("mentions", 1),
            "last_seen": target["date"],
            "temporal": target.get("temporal", False),
        })

    items_path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    target["status"] = "graduated"
    target["graduated_at"] = datetime.now(timezone.utc).isoformat()
    target["rationale"] = rationale
    _save_all(all_candidates)

    try:
        from episodic import log_event
        log_event(target.get("extracted_by", "nightly-consolidate"),
                  "candidate_graduated", entity=entity,
                  detail=target["fact"][:200], importance=5)
    except Exception:
        pass

    return target


def reject(candidate_id: str, rationale: str = "") -> dict | None:
    all_candidates = _load_all()
    for c in all_candidates:
        if c.get("id") == candidate_id and c.get("status") == "pending":
            c["status"] = "rejected"
            c["rationale"] = rationale
            _save_all(all_candidates)
            try:
                from episodic import log_event
                log_event("nightly-consolidate", "candidate_rejected",
                          entity=c["entity"], detail=c["fact"][:200], importance=3)
            except Exception:
                pass
            return c
    return None


def check_supersedes_candidates(fact_text: str, entity: str) -> dict | None:
    """Check if a pending candidate matches a supersedes target."""
    for c in pending_candidates():
        if c.get("entity") != entity:
            continue
        if _similar(c.get("fact", ""), fact_text):
            return c
    return None


def generate_review_queue() -> str:
    pending = pending_candidates()
    if not pending:
        return "# Review Queue\n\nNo pending candidates.\n"

    lines = ["# Review Queue", "", f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", ""]

    review = [c for c in pending if c.get("needs_review")]
    auto = [c for c in pending if not c.get("needs_review")]

    if review:
        lines.append(f"## Needs Review ({len(review)})")
        lines.append("")
        for c in review:
            lines.append(f"- **{c['id']}** [{c['entity']}] {c['fact'][:80]}")
            lines.append(f"  Category: {c['category']} | Mentions: {c.get('mentions', 1)} | Source: {c.get('source', '')}")
        lines.append("")

    if auto:
        lines.append(f"## Auto-Graduatable ({len(auto)})")
        lines.append("")
        for c in auto:
            lines.append(f"- {c['id']} [{c['entity']}] {c['fact'][:80]}")
        lines.append("")

    lines.append("## Commands")
    lines.append("```bash")
    lines.append("python3 review.py list                          # Show pending")
    lines.append('python3 review.py graduate <id> "rationale"     # Promote to items.json')
    lines.append('python3 review.py reject <id> "reason"          # Mark rejected')
    lines.append("python3 review.py auto-graduate                 # Batch graduate qualifying")
    lines.append("```")

    return "\n".join(lines) + "\n"


def write_review_queue() -> None:
    REVIEW_QUEUE_PATH.write_text(generate_review_queue(), encoding="utf-8")
