#!/usr/bin/env python3
"""
Scan Resources/skills/ for potential duplicate skills.
Reports similar skills based on word overlap in steps.
Does NOT auto-merge — writes a report for human review.
"""
import os
import re
import sys
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
DRY_RUN = "--dry-run" in sys.argv

def extract_steps(content: str) -> list[str]:
    """Extract step lines from a skill markdown file."""
    steps = []
    in_steps = False
    for line in content.split("\n"):
        if line.strip().startswith("## Steps"):
            in_steps = True
            continue
        if in_steps and line.strip().startswith("## "):
            break
        if in_steps and re.match(r'^\d+\.', line.strip()):
            steps.append(re.sub(r'^\d+\.\s*', '', line.strip()).lower())
    return steps

def word_set(text_list: list[str]) -> set[str]:
    """Convert list of strings to a set of words."""
    words = set()
    for text in text_list:
        words.update(re.findall(r'\w+', text.lower()))
    return words

def similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two word sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)

def find_duplicates(threshold: float = 0.7) -> list[tuple[str, str, float]]:
    """Find skill pairs with similarity above threshold."""
    skills_dir = LIFE_DIR / "Resources" / "skills"
    if not skills_dir.is_dir():
        return []

    skills = {}
    for f in sorted(skills_dir.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        steps = extract_steps(content)
        if steps:
            skills[f.name] = word_set(steps)

    duplicates = []
    names = list(skills.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            sim = similarity(skills[names[i]], skills[names[j]])
            if sim >= threshold:
                duplicates.append((names[i], names[j], sim))

    return duplicates

def write_report() -> int:
    """Write dedup report and return number of duplicates found."""
    duplicates = find_duplicates()

    if not duplicates:
        print("[dedup] No duplicate skills found")
        return 0

    report = f"# Skill Dedup Report\n\nGenerated: {os.environ.get('CONSOLIDATION_DATE', 'unknown')}\n\n"
    for a, b, sim in duplicates:
        report += f"- **{a}** <-> **{b}** -- {sim:.0%} word overlap\n"
    report += f"\nTotal: {len(duplicates)} potential duplicates. Review manually before merging.\n"

    log_dir = LIFE_DIR / "logs"
    if not DRY_RUN:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "skill-dedup-report.md").write_text(report, encoding="utf-8")

    print(f"[dedup] {'(dry) ' if DRY_RUN else ''}{len(duplicates)} potential duplicates found")
    return len(duplicates)

if __name__ == "__main__":
    write_report()
