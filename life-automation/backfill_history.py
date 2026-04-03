#!/usr/bin/env python3
"""
One-time backfill: populate ~/life/ knowledge graph from historical data.
Sources: GitHub PRs, MC tasks, MC dispatch log, Claude memory files.

Safe to re-run (idempotent). Use --dry-run to preview.
"""
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
DRY_RUN = "--dry-run" in sys.argv
MC_API = os.environ.get("MC_API_URL", "http://localhost:3000/api")
MC_API_KEY = os.environ.get("MC_API_KEY", "")
REPO_DIR = Path.home() / "pi-cluster"

# Reuse existing utilities
sys.path.insert(0, str(LIFE_DIR / "scripts"))


def safe_load_json(path: Path) -> list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def is_duplicate(items: list, fact: str) -> bool:
    return any(i.get("fact") == fact for i in items)


def ensure_entity(entity_type: str, slug: str, display: str) -> Path:
    """Create entity folder if missing. Returns items.json path."""
    type_dirs = {"project": "Projects", "person": "People", "company": "Companies"}
    base = LIFE_DIR / type_dirs.get(entity_type, "Projects")
    entity_dir = base / slug
    items_path = entity_dir / "items.json"
    summary_path = entity_dir / "summary.md"

    if not entity_dir.exists() and not DRY_RUN:
        entity_dir.mkdir(parents=True, exist_ok=True)
        print(f"[backfill] Created entity dir: {entity_type}/{slug}")

    if not summary_path.exists() and not DRY_RUN:
        summary_path.write_text(
            f"---\ntype: {entity_type}\nname: {display}\ncreated: 2026-03-01\n"
            f"last-updated: {date.today()}\nstatus: active\n---\n\n"
            f"## What This Is\nBackfilled from historical data.\n\n"
            f"## Key Facts\n-\n",
            encoding="utf-8",
        )

    if not items_path.exists() and not DRY_RUN:
        items_path.write_text("[]", encoding="utf-8")

    return items_path


def add_fact(items_path: Path, fact: str, category: str, date_str: str, source: str) -> bool:
    """Add fact if not duplicate. Returns True if added."""
    if DRY_RUN:
        print(f"[backfill] (dry) Would add: {fact[:80]}...")
        return True

    items = safe_load_json(items_path)
    if is_duplicate(items, fact):
        return False

    items.append({
        "date": date_str,
        "fact": fact,
        "category": category,
        "source": source,
        "confidence": "confirmed",
        "mentions": 1,
    })
    items_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return True


def add_relationship(from_slug: str, from_type: str, to_slug: str, to_type: str,
                     relation: str, since: str, source: str) -> bool:
    """Add relationship if not duplicate."""
    rel_path = LIFE_DIR / "relationships.json"
    if DRY_RUN:
        print(f"[backfill] (dry) Rel: {from_slug} --{relation}--> {to_slug}")
        return True

    rels = safe_load_json(rel_path)
    # Check dedup
    for r in rels:
        if r.get("from") == from_slug and r.get("to") == to_slug and r.get("relation") == relation:
            return False

    rels.append({
        "from": from_slug,
        "from_type": from_type,
        "to": to_slug,
        "to_type": to_type,
        "relation": relation,
        "first_seen": since,
        "last_seen": str(date.today()),
    })
    rel_path.write_text(json.dumps(rels, indent=2), encoding="utf-8")
    return True


# -- Source 1: GitHub PRs ------------------------------------------------------


def classify_pr(title: str) -> str:
    """Classify PR by title prefix into a category."""
    title_lower = title.lower()
    if title_lower.startswith("feat"):
        return "deployment"
    if title_lower.startswith("fix"):
        return "lesson"
    if title_lower.startswith("refactor"):
        return "configuration"
    if title_lower.startswith("chore") or title_lower.startswith("ci"):
        return "configuration"
    return "event"


def backfill_github_prs() -> int:
    """Extract facts from merged GitHub PRs."""
    if not REPO_DIR.exists():
        print("[backfill] No pi-cluster repo found, skipping GitHub PRs")
        return 0

    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", "enricoaquilina/pi-cluster",
             "--state", "merged", "--limit", "100",
             "--json", "number,title,mergedAt"],
            capture_output=True, text=True, timeout=30, cwd=str(REPO_DIR),
        )
        if result.returncode != 0:
            print(f"[backfill] gh pr list failed: {result.stderr[:100]}")
            return 0
        prs = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"[backfill] GitHub PR fetch error: {e}")
        return 0

    items_path = ensure_entity("project", "pi-cluster", "Pi Cluster")
    added = 0

    for pr in prs:
        merged_at = pr.get("mergedAt", "")[:10]  # YYYY-MM-DD
        title = pr.get("title", "")
        number = pr.get("number", 0)
        category = classify_pr(title)
        fact = f"PR #{number}: {title}"

        if add_fact(items_path, fact, category, merged_at, f"backfill/github-pr-{number}"):
            added += 1

    print(f"[backfill] GitHub PRs: {added} facts added to pi-cluster")
    return added


# -- Source 2: MC Tasks --------------------------------------------------------


def backfill_mc_tasks() -> int:
    """Extract completed task facts from Mission Control."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError

    try:
        req = Request(f"{MC_API}/tasks", headers={"X-Api-Key": MC_API_KEY})
        resp = urlopen(req, timeout=10)
        tasks = json.loads(resp.read().decode())
        if isinstance(tasks, dict):
            tasks = tasks.get("items", tasks.get("tasks", []))
    except (URLError, json.JSONDecodeError, OSError) as e:
        print(f"[backfill] MC tasks fetch error: {e}")
        return 0

    added = 0

    # Group tasks by project tag if available, otherwise infer
    for task in tasks:
        if task.get("status") != "done":
            continue

        title = task.get("title", "")
        created = task.get("created_at", str(date.today()))[:10]

        # Infer project from task content
        title_lower = title.lower()
        if any(kw in title_lower for kw in ["polymarket", "trader", "backtest", "clob", "eip-712",
                                              "paper trade", "risk module", "slippage", "bot loop",
                                              "order executor", "position management"]):
            slug, display = "polymarket-bot", "Polymarket Bot"
        elif any(kw in title_lower for kw in ["mission control", "mc ", "dashboard"]):
            slug, display = "pi-cluster", "Pi Cluster"
        else:
            slug, display = "pi-cluster", "Pi Cluster"

        items_path = ensure_entity("project", slug, display)
        fact = f"Task completed: {title}"

        if add_fact(items_path, fact, "deployment", created, "backfill/mc-task"):
            added += 1

    print(f"[backfill] MC tasks: {added} facts added")
    return added


# -- Source 3: MC Dispatch Log -------------------------------------------------


def backfill_dispatch_log() -> int:
    """Extract persona-project relationships from dispatch history."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError

    try:
        req = Request(f"{MC_API}/dispatch/log?limit=100", headers={"X-Api-Key": MC_API_KEY})
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        entries = data.get("items", []) if isinstance(data, dict) else data
    except (URLError, json.JSONDecodeError, OSError) as e:
        print(f"[backfill] Dispatch log fetch error: {e}")
        return 0

    added = 0
    seen = set()

    for entry in entries:
        persona = entry.get("persona", "")
        status = entry.get("status", "")
        if status != "success" or not persona:
            continue

        persona_lower = persona.lower()
        if persona_lower in seen:
            continue
        seen.add(persona_lower)

        created = entry.get("created_at", "")[:10]
        if not created:
            continue

        if add_relationship(
            persona_lower, "person", "pi-cluster", "project",
            "works-on", created, "backfill/dispatch-log"
        ):
            added += 1

    print(f"[backfill] Dispatch log: {added} relationships added")
    return added


# -- Source 4: Unmigrated Claude Memory ----------------------------------------


def backfill_claude_memory() -> int:
    """Migrate project_ai_review_pipeline.md to ~/life/."""
    memory_dir = Path.home() / ".claude" / "projects" / "-home-enrico" / "memory"
    ai_pipeline = memory_dir / "project_ai_review_pipeline.md"

    if not ai_pipeline.exists():
        print("[backfill] No unmigrated Claude memory files found")
        return 0

    items_path = ensure_entity("project", "pi-cluster", "Pi Cluster")
    content = ai_pipeline.read_text(encoding="utf-8")

    # Extract key facts from the file
    facts = []
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("- ") and len(line) > 20:
            # Clean up the fact
            fact = line[2:].strip()
            if len(fact) > 30:  # Skip trivial lines
                facts.append(fact)

    added = 0
    for fact in facts[:20]:  # Cap at 20 facts per file
        if add_fact(items_path, fact, "lesson", "2026-03-28", "backfill/claude-memory"):
            added += 1

    print(f"[backfill] Claude memory: {added} facts added")
    return added


# -- Main ----------------------------------------------------------------------


def run_backfill() -> dict:
    """Run all backfill sources."""
    print(f"[backfill] Starting {'(DRY RUN) ' if DRY_RUN else ''}backfill at {date.today()}")

    results = {
        "github_prs": backfill_github_prs(),
        "mc_tasks": backfill_mc_tasks(),
        "dispatch_log": backfill_dispatch_log(),
        "claude_memory": backfill_claude_memory(),
    }

    total = sum(results.values())
    print(f"\n[backfill] Done — {total} total items added")
    for source, count in results.items():
        print(f"  {source}: {count}")

    return results


if __name__ == "__main__":
    result = run_backfill()
    if "--json" in sys.argv:
        json.dump(result, sys.stdout, indent=2)
