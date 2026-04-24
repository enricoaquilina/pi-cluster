#!/usr/bin/env python3
"""
Load relevant skills based on task description or project context.

Matching: trigger keywords in frontmatter → fallback to content keyword match.
Skills without triggers are still searchable via content.

Usage:
    python3 skill_loader.py "deploying new gateway version"
    python3 skill_loader.py --project pi-cluster
    python3 skill_loader.py --list
"""
import argparse
import os
import sys
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
SKILLS_DIR = LIFE_DIR / "Resources" / "skills"


def _parse_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                val = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            fm[key] = val
    fm["_path"] = str(path.relative_to(LIFE_DIR))
    fm["_content"] = text
    return fm


def load_skills() -> list[dict]:
    if not SKILLS_DIR.exists():
        return []
    skills = []
    for f in sorted(SKILLS_DIR.glob("*.md")):
        fm = _parse_frontmatter(f)
        if fm.get("type") == "skill":
            skills.append(fm)
    return skills


def match_skills(query: str, skills: list[dict] | None = None, max_results: int = 5) -> list[dict]:
    if skills is None:
        skills = load_skills()
    query_words = set(query.lower().split())
    scored = []

    for sk in skills:
        score = 0
        triggers = sk.get("triggers", [])
        if isinstance(triggers, str):
            triggers = [triggers]

        for trigger in triggers:
            trigger_lower = trigger.lower()
            if trigger_lower in query.lower():
                score += 10
            elif any(w in trigger_lower for w in query_words):
                score += 5

        if score == 0:
            content = sk.get("_content", "").lower()
            name = sk.get("name", "").lower()
            content_hits = sum(1 for w in query_words if w in content)
            name_hits = sum(1 for w in query_words if w in name)
            score = name_hits * 3 + content_hits

        if score > 0:
            scored.append((score, sk))

    scored.sort(key=lambda x: -x[0])
    return [s[1] for s in scored[:max_results]]


def format_skill(skill: dict) -> str:
    name = skill.get("name", skill.get("_path", "?"))
    path = skill.get("_path", "")
    return f"- {name} ({path})"


def main():
    parser = argparse.ArgumentParser(description="Find relevant skills")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--project", help="Match by project name")
    parser.add_argument("--list", action="store_true", help="List all skills")
    parser.add_argument("--max", type=int, default=5)
    args = parser.parse_args()

    if args.list:
        for sk in load_skills():
            print(format_skill(sk))
        return

    query = args.query
    if args.project:
        query = f"{query} {args.project}".strip()

    if not query:
        parser.print_help()
        sys.exit(1)

    results = match_skills(query, max_results=args.max)
    if results:
        print("### Relevant Skills")
        for sk in results:
            print(format_skill(sk))
    else:
        print("_No matching skills found._")


if __name__ == "__main__":
    main()
