#!/usr/bin/env python3
"""
Apply Claude's extraction JSON to ~/life/ entity files.
Reads JSON from stdin (with optional markdown fence stripping).
Exits 0 on success, 1 on unrecoverable parse error.

Usage:
  LIFE_DIR=~/life CONSOLIDATION_DATE=2026-03-28 python3 apply_extraction.py < extraction.json
  python3 apply_extraction.py --dry-run < extraction.json

Cron safety: handles UTF-8 encoding explicitly, BrokenPipeError, SIGTERM.
"""
import io
import json
import os
import re
import signal
import sys
from datetime import date
from pathlib import Path

try:
    from episodic import log_event as _log_event
except ImportError:
    _log_event = None

try:
    from candidates import stage_fact as _stage_fact, check_supersedes_candidates
except ImportError:
    _stage_fact = None
    check_supersedes_candidates = None

PLATFORM = os.environ.get("LIFE_PLATFORM", "nightly-consolidate")

# Cron safety: force UTF-8 regardless of locale (cron often has POSIX/C locale)
sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", str(date.today()))
DRY_RUN = "--dry-run" in sys.argv
STAGE_MODE = "--stage" in sys.argv or os.environ.get("LIFE_STAGE_MODE") == "1"

TYPE_TO_DIR = {
    "person": "People",
    "project": "Projects",
    "company": "Companies",
    "area": "Areas",
    "resource": "Resources",
}

VALID_CATEGORIES = {
    "deployment", "decision", "configuration", "lesson",
    "preference", "event", "pending", "relationship", "contact", "financial",
}

VALID_RELATIONS = {"works-on", "owns", "provides", "uses", "reports-to", "manages", "contributes-to"}


def normalize_slug(name: str) -> str:
    """Normalize entity slug to lowercase-hyphens only."""
    slug = name.lower().replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


SUMMARY_TEMPLATE = """\
---
type: {etype}
name: {display}
created: {today}
last-updated: {today}
status: active
---

## What This Is
Auto-created from daily note {today}.

## What Matters Right Now
-

## Key Facts
-

## Open Questions / Pending
- [ ]
"""


def strip_fences(raw: str) -> str:
    """Strip markdown code fences if Claude wrapped the JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        raw = "\n".join(inner).strip()
    return raw


def entity_dir(etype: str, name: str) -> Path:
    base = TYPE_TO_DIR.get(etype, "Resources")
    return LIFE_DIR / base / name


def safe_load_json(path: Path) -> list:
    """Load items.json, returning [] if missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        print(f"[apply] WARNING: corrupt or missing {path} — starting fresh", file=sys.stderr)
        return []


def _similar(a: str, b: str) -> bool:
    """Check if two facts are semantically similar enough to reinforce.
    Uses Jaccard similarity on word sets (threshold 0.7)."""
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


def reinforce_fact(existing_items: list, new_fact: str) -> bool:
    """If a similar fact already exists, increment its mention count and upgrade confidence.
    Returns True if fact was reinforced (caller should skip adding).
    Skips superseded items — they should not be reinforced back to life."""
    for item in existing_items:
        if item.get("confidence") == "superseded":
            continue
        if _similar(item.get("fact", ""), new_fact):
            mentions = item.get("mentions", 1) + 1
            item["mentions"] = mentions
            item["last_seen"] = TODAY
            # Restore stale/archived facts on re-mention
            if item.get("confidence") in ("stale", "archived"):
                item["confidence"] = "confirmed"
            elif mentions >= 4:
                item["confidence"] = "established"
            elif mentions >= 2:
                item["confidence"] = "confirmed"
            return True
    return False



def daily_note_path() -> Path:
    """Compute daily note path using YYYY/MM/YYYY-MM-DD.md hierarchy."""
    parts = TODAY.split("-")
    return LIFE_DIR / "Daily" / parts[0] / parts[1] / f"{TODAY}.md"


def apply(data: dict) -> tuple[int, int, int]:
    created, updated, appended = 0, 0, 0

    for e in data.get("new_entities", []):
        # Validate entity type
        if e.get("type") not in TYPE_TO_DIR:
            print(f"[apply] Skip — unknown entity type: {e.get('type')}", file=sys.stderr)
            continue
        e["name"] = normalize_slug(e["name"])
        d = entity_dir(e["type"], e["name"])
        if not d.exists():
            if not DRY_RUN:
                d.mkdir(parents=True, exist_ok=True)
                (d / "summary.md").write_text(
                    SUMMARY_TEMPLATE.format(etype=e["type"], display=e["display"], today=TODAY),
                    encoding="utf-8",
                )
                (d / "items.json").write_text("[]", encoding="utf-8")
            print(f"[apply] {'(dry) ' if DRY_RUN else ''}Created: {e['type']}/{e['name']}")
            if _log_event and not DRY_RUN:
                try:
                    _log_event(PLATFORM, "entity_created", entity=e["name"],
                               detail=f"Created {e['type']}/{e['name']}", importance=6,
                               source_file=f"{TYPE_TO_DIR[e['type']]}/{e['name']}/items.json")
                except Exception:
                    pass
            created += 1

    for fu in data.get("fact_updates", []):
        fu["entity"] = normalize_slug(fu["entity"])
        d = entity_dir(fu["entity_type"], fu["entity"])
        items_path = d / "items.json"
        if not items_path.exists():
            print(f"[apply] Skip fact — entity not found: {fu['entity']}", file=sys.stderr)
            continue
        category = fu.get("category", "event")
        if category not in VALID_CATEGORIES:
            print(f"[apply] WARNING: unknown category '{category}' — defaulting to 'event'", file=sys.stderr)
            category = "event"

        if STAGE_MODE and _stage_fact and not DRY_RUN:
            items = safe_load_json(items_path)
            if reinforce_fact(items, fu["fact"]):
                items_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
                print(f"[apply] Reinforced fact: {fu['fact'][:60]}...", file=sys.stderr)
                continue
            contradicts = any(
                _similar(item.get("fact", ""), fu.get("supersedes", ""))
                for item in items if fu.get("supersedes") and item.get("confidence") != "superseded"
            )
            _stage_fact(
                fu["entity"], fu["entity_type"], fu["fact"], category, fu["date"],
                source=f"daily/{TODAY}", extracted_by=PLATFORM,
                supersedes=fu.get("supersedes"),
                contradicts_existing=contradicts,
                temporal=bool(fu.get("temporal", False)),
            )
            print(f"[apply] Staged → {fu['entity']}: {fu['fact'][:60]}...")
            if _log_event:
                try:
                    _log_event(PLATFORM, "candidate_staged", entity=fu["entity"],
                               detail=fu["fact"][:200], importance=4,
                               source_file=str(items_path.relative_to(LIFE_DIR)))
                except Exception:
                    pass
            updated += 1
            continue

        if not DRY_RUN:
            items = safe_load_json(items_path)
            if fu.get("supersedes"):
                for old_item in items:
                    if _similar(old_item.get("fact", ""), fu["supersedes"]):
                        old_item["superseded_by"] = fu["fact"]
                        old_item["superseded_date"] = TODAY
                        old_item["confidence"] = "superseded"
                        print(f"[apply] SUPERSEDED: '{old_item['fact'][:50]}...' → '{fu['fact'][:50]}...'", file=sys.stderr)
                        break
            if reinforce_fact(items, fu["fact"]):
                items_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
                print(f"[apply] Reinforced fact: {fu['fact'][:60]}...", file=sys.stderr)
                if _log_event:
                    try:
                        _log_event(PLATFORM, "fact_reinforced", entity=fu["entity"],
                                   detail=fu["fact"][:200], importance=3,
                                   source_file=str(items_path.relative_to(LIFE_DIR)))
                    except Exception:
                        pass
                continue
            new_entry = {
                "date": fu["date"],
                "fact": fu["fact"],
                "category": category,
                "source": f"daily/{TODAY}",
                "confidence": "single",
                "mentions": 1,
                "last_seen": TODAY,
                "temporal": bool(fu.get("temporal", False)),
            }
            items.append(new_entry)
            items_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
        print(f"[apply] {'(dry) ' if DRY_RUN else ''}Fact → {fu['entity']}: {fu['fact'][:60]}...")
        if _log_event and not DRY_RUN:
            try:
                _log_event(PLATFORM, "fact_added", entity=fu["entity"],
                           detail=fu["fact"][:200], importance=5,
                           source_file=str(items_path.relative_to(LIFE_DIR)))
            except Exception:
                pass
        updated += 1

    # Cross-reference propagation: if a fact mentions another known entity,
    # add a reciprocal fact to that entity (Karpathy-style cross-linking)
    if not DRY_RUN:
        try:
            rels = json.loads((LIFE_DIR / "relationships.json").read_text(encoding="utf-8"))
            known_slugs = {e.get("from") for e in rels} | {e.get("to") for e in rels}
        except (OSError, json.JSONDecodeError):
            known_slugs = set()

        for fu in data.get("fact_updates", []):
            source_entity = fu.get("entity", "")
            fact_text = fu.get("fact", "")
            for slug in known_slugs:
                if slug == source_entity or not slug:
                    continue
                if slug in fact_text.lower() or slug.replace("-", " ") in fact_text.lower():
                    # Find this entity's items.json
                    for etype in TYPE_TO_DIR.values():
                        cross_items_path = LIFE_DIR / etype / slug / "items.json"
                        if cross_items_path.exists():
                            cross_items = safe_load_json(cross_items_path)
                            cross_fact = f"Related: {fact_text[:150]} (via {source_entity})"
                            if not reinforce_fact(cross_items, cross_fact):
                                cross_items.append({
                                    "date": TODAY, "fact": cross_fact,
                                    "category": fu.get("category", "event"),
                                    "source": f"cross-ref/{source_entity}",
                                    "confidence": "single", "mentions": 1,
                                    "last_seen": TODAY, "temporal": False,
                                })
                                cross_items_path.write_text(json.dumps(cross_items, indent=2), encoding="utf-8")
                                print(f"[apply] Cross-ref → {slug}: {cross_fact[:60]}...")
                            break  # Found the entity dir, stop searching

    # Layer 5: Procedural skills
    skills_dir = LIFE_DIR / "Resources" / "skills"
    for sk in data.get("skills", []):
        skill_path = skills_dir / f"{sk['name']}.md"
        if not skill_path.exists():
            if not DRY_RUN:
                skills_dir.mkdir(parents=True, exist_ok=True)
                steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sk.get("steps", [])))
                skill_content = (
                    f"---\ntype: skill\nname: {sk['display']}\n"
                    f"created: {TODAY}\n---\n\n## Steps\n{steps}\n"
                )
                skill_path.write_text(skill_content, encoding="utf-8")
            print(f"[apply] {'(dry) ' if DRY_RUN else ''}Skill → {sk['name']}")
            if _log_event and not DRY_RUN:
                try:
                    _log_event(PLATFORM, "skill_created", entity=sk["name"],
                               detail=sk.get("display", sk["name"]), importance=4,
                               source_file=f"Resources/skills/{sk['name']}.md")
                except Exception:
                    pass
            created += 1

    tacit_dir = LIFE_DIR / "Areas" / "about-me"
    for tk in data.get("tacit_knowledge", []):
        target = tacit_dir / f"{tk['file']}.md"
        if target.exists():
            if not DRY_RUN:
                with target.open("a", encoding="utf-8") as f:
                    f.write(f"\n## {TODAY} (auto-extracted)\n{tk['entry']}\n")
            print(f"[apply] {'(dry) ' if DRY_RUN else ''}Tacit → {tk['file']}.md")
            if _log_event and not DRY_RUN:
                try:
                    _log_event(PLATFORM, "tacit_knowledge_appended", entity=tk["file"],
                               detail=tk["entry"][:200], importance=4,
                               source_file=f"Areas/about-me/{tk['file']}.md")
                except Exception:
                    pass
            appended += 1

    # Layer 6: Entity relationships
    rel_path = LIFE_DIR / "relationships.json"
    for rel in data.get("relationships", []):
        rel["from"] = normalize_slug(rel["from"])
        rel["to"] = normalize_slug(rel["to"])
        relation = rel.get("relation", "")
        if relation not in VALID_RELATIONS:
            print(f"[apply] Skip relationship — unknown relation: {relation}", file=sys.stderr)
            continue
        if not DRY_RUN:
            existing_rels = []
            if rel_path.exists():
                try:
                    existing_rels = json.loads(rel_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    existing_rels = []
            # Dedup: check if from+to+relation already exists
            found = False
            for er in existing_rels:
                if er.get("from") == rel["from"] and er.get("to") == rel["to"] and er.get("relation") == relation:
                    er["last_seen"] = TODAY
                    found = True
                    break
            if not found:
                existing_rels.append({
                    "from": rel["from"],
                    "from_type": rel.get("from_type", ""),
                    "to": rel["to"],
                    "to_type": rel.get("to_type", ""),
                    "relation": relation,
                    "first_seen": TODAY,
                    "last_seen": TODAY,
                })
            rel_path.write_text(json.dumps(existing_rels, indent=2), encoding="utf-8")
        if _log_event and not DRY_RUN:
            try:
                _log_event(PLATFORM, "relationship_added", entity=rel["from"],
                           detail=f"{rel['from']} --{relation}--> {rel['to']}", importance=4,
                           source_file="relationships.json")
            except Exception:
                pass
        print(f"[apply] {'(dry) ' if DRY_RUN else ''}Rel → {rel['from']} --{relation}--> {rel['to']}")

    note = daily_note_path()
    if note.exists() and not DRY_RUN:
        content = note.read_text(encoding="utf-8")
        if "_Not yet consolidated_" in content:
            log_block = (
                f"Consolidated: {TODAY} 23:00\n"
                f"{data.get('summary', '')}\n"
                f"- Entities created: {created}\n"
                f"- Facts added: {updated}\n"
                f"- Tacit knowledge entries: {appended}\n"
            )
            note.write_text(
                content.replace("_Not yet consolidated_", log_block),
                encoding="utf-8",
            )

    return created, updated, appended


def graceful_shutdown(signum: int, _frame: object) -> None:
    """Handle SIGTERM/SIGINT per Unix convention (exit 128+signum)."""
    sys.stdout.flush()
    sys.exit(128 + signum)


def main() -> None:
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    raw = sys.stdin.read()
    raw = strip_fences(raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[apply] JSON parse error: {e}", file=sys.stderr)
        print(f"[apply] First 200 chars: {raw[:200]}", file=sys.stderr)
        sys.exit(1)

    c, u, a = apply(data)
    print(f"[apply] Done — {c} entities created, {u} facts added, {a} tacit entries")


if __name__ == "__main__":
    try:
        main()
        sys.stdout.flush()
    except BrokenPipeError:
        # Pipe-safe: redirect stdout to devnull (don't set SIGPIPE to SIG_DFL)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
