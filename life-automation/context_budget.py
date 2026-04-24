#!/usr/bin/env python3
"""
Query-aware context assembly for ~/life/ session start.

Fills context in priority tiers until budget is exhausted.
Replaces the fixed multi-section dump in cc_start_hook.sh.

Usage:
    python3 context_budget.py --cwd /home/enrico/pi-cluster --budget 6000
    python3 context_budget.py --budget 3000
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
SCRIPT_DIR = Path(__file__).resolve().parent


def _read_file(path: Path, max_chars: int = 500) -> str:
    try:
        return path.read_text(encoding="utf-8")[:max_chars]
    except OSError:
        return ""


def _run(cmd: list[str], max_chars: int = 500) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return result.stdout.strip()[:max_chars]
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _detect_project(cwd: str) -> str:
    slugs_path = SCRIPT_DIR / "config" / "project-slugs.json"
    if not slugs_path.exists():
        return ""
    try:
        cfg = json.loads(slugs_path.read_text())
        seg_to_slug = {}
        for slug, data in cfg.get("slugs", {}).items():
            for seg in data.get("segments", [slug]):
                seg_to_slug.setdefault(seg, slug)
        for seg in reversed([p for p in cwd.split("/") if p]):
            if seg in seg_to_slug:
                return seg_to_slug[seg]
    except (json.JSONDecodeError, OSError):
        pass
    return ""


def assemble(cwd: str = "", budget: int = 6000) -> str:
    sections = []
    used = 0

    def add(title: str, content: str) -> bool:
        nonlocal used
        if not content.strip():
            return False
        block = f"## {title}\n{content.strip()}\n"
        if used + len(block) > budget:
            return False
        sections.append(block)
        used += len(block)
        return True

    from datetime import date
    today = date.today()
    year = today.strftime("%Y")
    month = today.strftime("%m")

    rules = _read_file(LIFE_DIR / "Areas" / "about-me" / "hard-rules.md", 600)
    if rules:
        add("Hard Rules", rules)

    profile = _read_file(LIFE_DIR / "Areas" / "about-me" / "profile.md", 400)
    if profile:
        add("Profile", profile)

    daily = LIFE_DIR / "Daily" / year / month / f"{today.isoformat()}.md"
    if daily.exists():
        text = _read_file(daily, 1000)
        import re
        ap = re.search(r"## Active Projects\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
        pi = re.search(r"## Pending Items\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
        daily_content = ""
        if ap:
            daily_content += f"### Active Projects\n{ap.group(1).strip()}\n"
        if pi:
            daily_content += f"### Pending Items\n{pi.group(1).strip()}\n"
        if daily_content:
            add("Today", daily_content)

    try:
        from cross_platform_summary import format_summary
        xplat = format_summary(hours=24, exclude_platforms=["claude-code"], max_lines=10)
        if xplat:
            add("Cross-Platform Activity", xplat.replace("## Cross-Platform Activity (last 24h)", "").strip())
    except ImportError:
        pass

    try:
        from candidates import pending_candidates, needs_review_candidates
        pending = pending_candidates()
        review = needs_review_candidates()
        if pending:
            add("Review Queue", f"{len(pending)} pending candidates ({len(review)} need review)")
    except ImportError:
        pass

    project = _detect_project(cwd) if cwd else ""
    if project:
        summary = _read_file(LIFE_DIR / "Projects" / project / "summary.md", 500)
        if summary:
            import re
            body = re.sub(r"^---.*?---\s*", "", summary, flags=re.DOTALL)
            first_para = body.strip().split("\n\n")[0] if body.strip() else ""
            if first_para:
                add(f"Project: {project}", first_para)

        try:
            from skill_loader import match_skills
            skills = match_skills(project, max_results=3)
            if skills:
                lines = "\n".join(f"- {s.get('name', '?')} ({s.get('_path', '')})" for s in skills)
                add("Relevant Skills", lines)
        except ImportError:
            pass

    session_search = LIFE_DIR / "scripts" / "session_search.py"
    if session_search.exists():
        out = _run(["/usr/bin/python3", str(session_search), "--recent", "3", "--json"], 800)
        if out:
            try:
                sessions = json.loads(out)
                lines = []
                for d in sessions[:3]:
                    ts = d.get("ts", "")[:10]
                    stype = d.get("session_type", "?")
                    summary = d.get("summary", "")[:100]
                    lines.append(f"- {ts} [{stype}] {summary}")
                if lines:
                    add("Recent Sessions", "\n".join(lines))
            except json.JSONDecodeError:
                pass

    maxwell = LIFE_DIR / "Daily" / year / month / f"maxwell-{today.isoformat()}.md"
    if maxwell.exists():
        content = _read_file(maxwell, 400)
        import re
        body = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL)
        if body.strip():
            add("Maxwell Activity", body.strip()[:300])

    return "\n".join(sections)


def main():
    parser = argparse.ArgumentParser(description="Tiered context assembly")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--budget", type=int, default=6000)
    args = parser.parse_args()
    print(assemble(cwd=args.cwd, budget=args.budget))


if __name__ == "__main__":
    main()
