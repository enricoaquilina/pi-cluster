#!/usr/bin/env python3
"""LLM-powered knowledge lint — finds contradictions, redundancies, stale claims.

Sends entity facts to Claude Haiku for semantic analysis. More expensive than
scripted lint (lint_knowledge.py), so runs weekly not nightly.

Usage:
    python3 lint_knowledge_llm.py              # Run LLM lint
    python3 lint_knowledge_llm.py --dry-run    # Preview entities that would be checked
    python3 lint_knowledge_llm.py --json       # Output structured JSON

Environment:
    LIFE_DIR            — path to ~/life/ (default: ~/life)
    CONSOLIDATION_DATE  — override today's date (for testing)
"""
import json
import os
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
MIN_FACTS = 5  # Only lint entities with enough facts for meaningful analysis
MAX_FACTS_PER_CALL = 50  # Truncate to avoid token limits
CLAUDE_BIN = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")

ENTITY_PATTERNS = [
    "Projects/*/items.json",
    "People/*/items.json",
    "Companies/*/items.json",
]


def _load_entity_facts(items_path: Path) -> list[dict]:
    """Load facts from items.json, sorted by date desc."""
    try:
        items = json.loads(items_path.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            return []
        # Skip superseded/archived facts
        active = [i for i in items if i.get("confidence") not in ("superseded", "archived")]
        return sorted(active, key=lambda x: x.get("date", ""), reverse=True)[:MAX_FACTS_PER_CALL]
    except (OSError, json.JSONDecodeError):
        return []


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
            capture_output=True, text=True, timeout=60,
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
