#!/usr/bin/env python3
"""LLM-powered knowledge lint — finds contradictions, redundancies, stale claims.

Sends entity facts to Claude Haiku for semantic analysis. More expensive than
scripted lint (lint_knowledge.py), so runs weekly not nightly.

Phase 8A additions (on top of v1 single-entity behavior):
  * Honors the global ``LIFE_LLM_DISABLED`` kill switch (plan v3 §8.0.1).
  * Uses the shared Fact schema v1 exclusion rule (default: ``archived`` only).
  * ``--orphans`` mode: structural / stale / edge_only / prose_only / pending_creation.
  * ``--mission-control`` path: scans daily notes for pending entity references.

Usage:
    python3 lint_knowledge_llm.py              # Run LLM lint
    python3 lint_knowledge_llm.py --dry-run    # Preview entities that would be checked
    python3 lint_knowledge_llm.py --json       # Output structured JSON
    python3 lint_knowledge_llm.py --orphans    # Only run orphan detection (no LLM)

Environment:
    LIFE_DIR                    — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE          — override today's date (for testing)
    LIFE_EXCLUDED_CONFIDENCE    — comma-separated confidence values to exclude
                                  (default: "archived")
    LIFE_LLM_DISABLED           — if set, exits 0 without any LLM calls
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", "")
if not TODAY:
    from datetime import date
    TODAY = str(date.today())

DRY_RUN = "--dry-run" in sys.argv
JSON_OUTPUT = "--json" in sys.argv
ORPHANS_ONLY = "--orphans" in sys.argv
MIN_FACTS = 5  # Only lint entities with enough facts for meaningful analysis
MAX_FACTS_PER_CALL = 50  # Truncate to avoid token limits
CLAUDE_BIN = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")

# Fact schema v1: default exclusion is just "archived". Configurable via env.
EXCLUDED_CONFIDENCE = frozenset(
    os.environ.get("LIFE_EXCLUDED_CONFIDENCE", "archived").split(",")
)

# Staleness thresholds per entity type (days)
STALE_DAYS = {
    "project": int(os.environ.get("ORPHAN_STALE_DAYS_PROJECT", "21")),
    "person": int(os.environ.get("ORPHAN_STALE_DAYS_PERSON", "14")),
    "company": int(os.environ.get("ORPHAN_STALE_DAYS_COMPANY", "60")),
}

ENTITY_PATTERNS = [
    "Projects/*/items.json",
    "People/*/items.json",
    "Companies/*/items.json",
]

# Wiki-link regex — handles [[slug]], [[slug|alias]], [[slug#anchor]], [[slug.md]].
# Reject path-traversal inside the slug capture.
WIKI_LINK_RE = re.compile(r"\[\[([^\[\]|#]+?)(?:#[^\[\]|]+)?(?:\|[^\[\]]+)?\]\]")

# Shared kill switch + graph helpers
sys.path.insert(0, str(Path(__file__).resolve().parent))
from life_kill_switch import check_llm_kill_switch  # noqa: E402
from life_graph import (  # noqa: E402
    canonical_slug,
    known_entities,
    load_relationships,
)


def _load_entity_facts(items_path: Path) -> list[dict]:
    """Load facts from items.json, sorted by date desc.

    Applies the Fact schema v1 exclusion (plan v3): only ``archived`` excluded
    by default; ``stale`` / ``single`` / ``confirmed`` count as active.
    """
    try:
        items = json.loads(items_path.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            return []
        active = [
            i for i in items
            if i.get("confidence") not in EXCLUDED_CONFIDENCE
        ]
        return sorted(active, key=lambda x: x.get("date", ""), reverse=True)[:MAX_FACTS_PER_CALL]
    except (OSError, json.JSONDecodeError):
        return []


# ---------------------------------------------------------------- wiki scan


def _strip_code_fences(text: str) -> str:
    """Remove ```-fenced code blocks so wiki-links inside them don't count."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def _scan_wiki_mentions(life_dir: Path) -> dict[str, set[str]]:
    """Build an inverted index from slug -> set of files mentioning it.

    File allowlist: walks ``Daily/**/*.md`` ONLY. Non-``.md`` files under
    ``Daily/`` (e.g. ``sessions-digest-*.jsonl``) are skipped even when they
    contain ``[[slug]]`` literals in JSON string fields.
    """
    daily = life_dir / "Daily"
    mentions: dict[str, set[str]] = {}
    if not daily.is_dir():
        return mentions
    for md in sorted(daily.rglob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        stripped = _strip_code_fences(text)
        for raw in WIKI_LINK_RE.findall(stripped):
            raw = raw.strip()
            if raw.endswith(".md"):
                raw = raw[:-3]
            slug = canonical_slug(raw)
            if not slug:
                continue
            mentions.setdefault(slug, set()).add(str(md))
    return mentions


# ------------------------------------------------------------- orphan categories


def find_orphans(life_dir: Path) -> dict:
    """Detect the five orphan categories defined in plan v3 §8A.

    Returns a dict with keys:
      * ``orphan_structural``: no edges AND no wiki-link mentions
      * ``orphan_stale``: has edges/mentions but last mention > threshold
        (NOT YET IMPLEMENTED — placeholder for future dated-mention data)
      * ``orphan_edge_only``: in relationships.json but zero prose mentions
      * ``orphan_prose_only``: in [[slug]] but not in relationships.json
      * ``pending_creation``: wiki-linked from daily notes but no folder
    """
    known = known_entities(life_dir)
    edges, _ = load_relationships(life_dir / "relationships.json")
    mentions = _scan_wiki_mentions(life_dir)

    # Slugs referenced in relationships.json (either side)
    edge_slugs: set[str] = set()
    for e in edges:
        edge_slugs.add(e.from_)
        edge_slugs.add(e.to)

    result: dict[str, list[str]] = {
        "orphan_structural": [],
        "orphan_stale": [],
        "orphan_edge_only": [],
        "orphan_prose_only": [],
        "pending_creation": [],
    }

    for slug in sorted(known):
        in_edges = slug in edge_slugs
        in_prose = slug in mentions
        if not in_edges and not in_prose:
            result["orphan_structural"].append(slug)
        elif in_edges and not in_prose:
            result["orphan_edge_only"].append(slug)
        # edge_only + prose_only are mutually exclusive here; we don't list
        # entities that appear in both as any kind of orphan.

    # prose-only and pending_creation
    for slug in sorted(mentions):
        if slug not in known:
            result["pending_creation"].append(slug)
        elif slug not in edge_slugs:
            result["orphan_prose_only"].append(slug)

    return result


def _build_prompt(slug: str, facts: list[dict]) -> str:
    """Build the lint prompt for Claude Haiku."""
    fact_lines = "\n".join(
        f"- [{f.get('date', '?')}] [{f.get('category', '?')}] {f.get('fact', '')}"
        for f in facts
    )
    return (
        f"Review these facts about the entity '{slug}' for quality issues. "
        f"Find contradictions (facts that conflict), redundancies (facts that say the same thing), "
        f"and stale claims (facts that are likely outdated). "
        f"Output ONLY valid JSON — no explanation text.\n\n"
        f"Facts:\n{fact_lines}\n\n"
        f"Output format:\n"
        f'[{{"fact_a": "text", "fact_b": "text or null", "issue_type": "contradiction|redundant|stale", '
        f'"explanation": "why this is an issue"}}]\n\n'
        f"Return [] if no issues found."
    )


def lint_entity(slug: str, facts: list[dict]) -> list[dict]:
    """Lint a single entity's facts via Claude Haiku."""
    prompt = _build_prompt(slug, facts)

    if DRY_RUN:
        print(f"[llm-lint] DRY RUN: {slug} ({len(facts)} facts)")
        return []

    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt,
             "--output-format", "text", "--no-session-persistence",
             "--model", "claude-haiku-4-5-20251001"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"[llm-lint] ERROR: Claude failed for {slug}", file=sys.stderr)
            return []

        output = result.stdout.strip()
        if output.startswith("```"):
            lines = output.splitlines()
            output = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        findings = json.loads(output)
        if not isinstance(findings, list):
            return []

        # Tag each finding with the entity
        for f in findings:
            f["entity"] = slug
        return findings

    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[llm-lint] ERROR: {slug} — {e}", file=sys.stderr)
        return []


def main() -> None:
    # Orphan-only mode does not require LLM — run before the kill switch check
    # so orphans are still detectable when LLM calls are disabled.
    if ORPHANS_ONLY:
        orphans = find_orphans(LIFE_DIR)
        if JSON_OUTPUT:
            json.dump(orphans, sys.stdout, indent=2, sort_keys=True)
            print()
        else:
            for category, slugs in orphans.items():
                if not slugs:
                    continue
                print(f"[llm-lint] {category}: {', '.join(slugs)}")
        # Always write pending_creation to its own log for operator visibility
        if orphans.get("pending_creation"):
            pending_log = LIFE_DIR / "logs" / "pending-entities.log"
            pending_log.parent.mkdir(parents=True, exist_ok=True)
            with open(pending_log, "a", encoding="utf-8") as f:
                for slug in orphans["pending_creation"]:
                    f.write(f"{TODAY}\t{slug}\n")
        return

    # Global kill switch: per plan v3 §8.0.1, LLM-calling paths must honor it.
    if check_llm_kill_switch(script="lint_knowledge_llm"):
        return

    all_findings: list[dict] = []

    for pattern in ENTITY_PATTERNS:
        for items_path in sorted(LIFE_DIR.glob(pattern)):
            if items_path.parent.name.startswith("_"):
                continue
            facts = _load_entity_facts(items_path)
            if len(facts) < MIN_FACTS:
                continue
            slug = items_path.parent.name
            findings = lint_entity(slug, facts)
            all_findings.extend(findings)

    if JSON_OUTPUT:
        json.dump(all_findings, sys.stdout, indent=2)
        print()
    elif DRY_RUN:
        print("[llm-lint] Done (dry run)")
    elif not all_findings:
        print("[llm-lint] No issues found")
    else:
        print(f"[llm-lint] {len(all_findings)} issue(s) found:")
        for f in all_findings:
            print(f"  [{f.get('issue_type', '?')}] {f.get('entity', '?')}: {f.get('explanation', '')[:80]}")

        # Write results
        out_path = LIFE_DIR / "logs" / "llm-lint-results.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(all_findings, indent=2), encoding="utf-8")
        print(f"[llm-lint] Results written to {out_path}")


if __name__ == "__main__":
    main()
