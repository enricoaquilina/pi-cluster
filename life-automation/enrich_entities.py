#!/usr/bin/env python3
"""Proactive entity enrichment — populate empty entities with facts from context.

Uses a 3-tier fallback:
1. Daily note [[wiki-link]] mentions → extract context sentences → LLM
2. QMD semantic search for entity name → extract relevant passages
3. Relationship graph context → infer from connected entities

Usage:
    python3 enrich_entities.py              # Enrich entities with <3 facts
    python3 enrich_entities.py --dry-run    # Preview without writing
    python3 enrich_entities.py --entity slug  # Enrich a specific entity

Environment:
    LIFE_DIR            — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (for testing)
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", str(date.today()))
DRY_RUN = "--dry-run" in sys.argv
MAX_FACTS_THRESHOLD = 3  # Only enrich entities with fewer than this many facts
CLAUDE_BIN = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")

ENTITY_PATTERNS = [
    "Projects/*/items.json",
    "People/*/items.json",
    "Companies/*/items.json",
]


def _count_facts(items_path: Path) -> int:
    try:
        return len(json.loads(items_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return 0


def _find_sparse_entities() -> list[dict]:
    """Find entities with fewer than MAX_FACTS_THRESHOLD facts."""
    sparse = []
    for pattern in ENTITY_PATTERNS:
        for items_path in sorted(LIFE_DIR.glob(pattern)):
            if items_path.parent.name.startswith("_"):
                continue
            count = _count_facts(items_path)
            if count < MAX_FACTS_THRESHOLD:
                sparse.append({
                    "slug": items_path.parent.name,
                    "type": items_path.parent.parent.name,
                    "items_path": items_path,
                    "fact_count": count,
                })
    return sparse


def _gather_context_from_daily_notes(slug: str) -> str:
    """Tier 1: Find sentences mentioning [[slug]] in recent daily notes."""
    sentences = []
    cutoff = date.fromisoformat(TODAY) - timedelta(days=30)
    daily_dir = LIFE_DIR / "Daily"
    if not daily_dir.is_dir():
        return ""
    for note in daily_dir.rglob("*.md"):
        try:
            note_date = date.fromisoformat(note.stem)
        except ValueError:
            continue
        if note_date < cutoff:
            continue
        try:
            text = note.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Find lines mentioning the entity
        for line in text.splitlines():
            if f"[[{slug}]]" in line or slug in line.lower():
                clean = line.strip().lstrip("-* ").strip()
                if clean and len(clean) > 10:
                    sentences.append(clean)
    return "\n".join(sentences[:20])  # Cap at 20 sentences


def _gather_context_from_relationships(slug: str) -> str:
    """Tier 3: Use relationship graph to infer entity context."""
    try:
        rels = json.loads((LIFE_DIR / "relationships.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    context_parts = []
    for edge in rels:
        if edge.get("from") == slug:
            context_parts.append(f"{slug} {edge['relation']} {edge['to']}")
        elif edge.get("to") == slug:
            context_parts.append(f"{edge['from']} {edge['relation']} {slug}")
    return "\n".join(context_parts)


def _enrich_entity(entity: dict) -> list[dict]:
    """Enrich a single entity using the 3-tier fallback. Returns extracted facts."""
    slug = entity["slug"]
    entity_type = entity["type"]

    # Gather context from all tiers
    context = _gather_context_from_daily_notes(slug)
    if not context:
        context = _gather_context_from_relationships(slug)
    if not context:
        print(f"[enrich] SKIP: {entity_type}/{slug} — no context found")
        return []

    # Build prompt
    prompt = (
        f"Based on the following context about '{slug}', extract 1-3 factual statements. "
        f"Each fact should be a single sentence. Output ONLY a JSON array of strings.\n\n"
        f"Context:\n{context[:2000]}\n\n"
        f"Output format: [\"fact 1\", \"fact 2\"]"
    )

    if DRY_RUN:
        print(f"[enrich] DRY RUN: {entity_type}/{slug}")
        print(f"  Context ({len(context)} chars): {context[:100]}...")
        return []

    # Call Claude Haiku
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt,
             "--output-format", "text", "--no-session-persistence",
             "--model", "claude-haiku-4-5-20251001"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"[enrich] ERROR: Claude failed for {slug}")
            return []

        # Parse response
        output = result.stdout.strip()
        # Strip code fences if present
        if output.startswith("```"):
            lines = output.splitlines()
            output = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        facts = json.loads(output)
        if not isinstance(facts, list):
            return []
        return [f for f in facts if isinstance(f, str) and len(f) > 10]

    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[enrich] ERROR: {slug} — {e}")
        return []


def _apply_facts(entity: dict, facts: list[str]) -> int:
    """Apply extracted facts to entity's items.json. Returns count of new facts."""
    items_path = entity["items_path"]
    try:
        items = json.loads(items_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        items = []

    added = 0
    for fact in facts:
        # Simple dedup: check if any existing fact is >70% similar (word overlap)
        fact_words = set(fact.lower().split())
        duplicate = False
        for existing in items:
            existing_words = set(existing.get("fact", "").lower().split())
            if fact_words and existing_words:
                overlap = len(fact_words & existing_words) / max(len(fact_words), len(existing_words))
                if overlap > 0.7:
                    duplicate = True
                    break
        if duplicate:
            continue

        items.append({
            "date": TODAY,
            "fact": fact[:200],
            "category": "event",
            "source": "auto/enrich",
            "confidence": "single",
            "mentions": 1,
            "last_seen": TODAY,
            "temporal": False,
        })
        added += 1

    if added:
        items_path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    return added


def main() -> None:
    # Find target entity if specified
    target = None
    for i, arg in enumerate(sys.argv):
        if arg == "--entity" and i + 1 < len(sys.argv):
            target = sys.argv[i + 1]

    entities = _find_sparse_entities()
    if target:
        entities = [e for e in entities if e["slug"] == target]

    if not entities:
        print("[enrich] No sparse entities found")
        return

    total_added = 0
    for entity in entities:
        facts = _enrich_entity(entity)
        if facts and not DRY_RUN:
            added = _apply_facts(entity, facts)
            total_added += added
            print(f"[enrich] {entity['type']}/{entity['slug']}: +{added} facts")
        elif facts:
            print(f"[enrich] Would add {len(facts)} facts to {entity['type']}/{entity['slug']}")

    print(f"[enrich] Done — {total_added} facts added across {len(entities)} entities")


if __name__ == "__main__":
    main()
