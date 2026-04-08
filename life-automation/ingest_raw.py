#!/usr/bin/env python3
"""Karpathy-style ingest: LLM reads raw sources and compiles wiki pages.

Reads documents from ~/life/raw/, sends to Claude Haiku for extraction,
applies via apply_extraction.py, and moves processed files to raw/_processed/.

Usage:
    ingest_raw.py source.md              # Ingest a single document
    ingest_raw.py --all                  # Ingest all files in ~/life/raw/
    ingest_raw.py source.md --dry-run    # Print LLM prompt, don't call LLM

Environment:
    LIFE_DIR  — path to ~/life/ (default: ~/life)
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = os.environ.get("CONSOLIDATION_DATE", str(date.today()))
DRY_RUN = "--dry-run" in sys.argv
MAX_CHARS = 50_000
CLAUDE_BIN = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
MODEL = "claude-haiku-4-5-20251001"


def _is_text_file(path: Path) -> bool:
    """Check if file is likely text (no null bytes in first 1024 bytes)."""
    try:
        chunk = path.read_bytes()[:1024]
        return b"\x00" not in chunk
    except OSError:
        return False


def _read_source(path: Path) -> str | None:
    """Read source file with truncation. Returns None for binary/unreadable."""
    if not _is_text_file(path):
        print(f"[ingest] SKIP: {path.name} appears to be binary")
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="latin-1")
            print(f"[ingest] WARNING: {path.name} decoded as latin-1 (not UTF-8)")
        except OSError:
            print(f"[ingest] SKIP: {path.name} unreadable")
            return None
    if len(text) > MAX_CHARS:
        print(f"[ingest] WARNING: {path.name} truncated from {len(text)} to {MAX_CHARS} chars")
        text = text[:MAX_CHARS]
    return text


def _get_existing_entities() -> str:
    """List existing entity slugs (same as nightly consolidation)."""
    slugs = []
    for parent in ["Projects", "People", "Companies"]:
        parent_dir = LIFE_DIR / parent
        if not parent_dir.is_dir():
            continue
        for d in sorted(parent_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("_"):
                slugs.append(d.name)
    return "\n".join(slugs)


def _build_prompt(content: str, source_name: str) -> str:
    """Build the extraction prompt (adapted from nightly-consolidate.sh)."""
    existing = _get_existing_entities()
    return f"""You are a knowledge extraction assistant. Analyze the following source document and extract facts, entities, and relationships for a personal knowledge base.

TODAY: {TODAY}
EXISTING ENTITIES: {existing}

SOURCE DOCUMENT ({source_name}):
{content}

Output this exact JSON schema (use empty arrays if nothing qualifies):
{{
  "new_entities": [{{"type": "person|project|company", "name": "slug-lowercase-hyphens", "display": "Display Name"}}],
  "fact_updates": [{{"entity_type": "project|person|company|area|resource", "entity": "existing-slug", "date": "{TODAY}", "fact": "one sentence", "category": "deployment|decision|configuration|lesson|preference|event|pending", "temporal": false}}],
  "tacit_knowledge": [{{"file": "workflow-habits|hard-rules|communication-preferences|lessons-learned", "entry": "one sentence"}}],
  "skills": [{{"name": "slug-name", "display": "How to Do X", "steps": ["step 1", "step 2"]}}],
  "relationships": [{{"from": "slug", "from_type": "person|project|company", "to": "slug", "to_type": "person|project|company", "relation": "works-on|owns|provides|uses|reports-to|manages|contributes-to"}}],
  "summary": "one sentence"
}}

Rules:
- Entity slugs MUST be lowercase-hyphens only
- new_entities: only for entities with significant discussion (3+ mentions or ongoing relationship)
- fact_updates: only for EXISTING entities listed above
- Set "temporal": true for transient state facts, false for permanent facts
- Return empty arrays if nothing qualifies"""


def _move_file(path: Path, dest_dir: str) -> None:
    """Move file to _processed/ or _errors/ with date prefix."""
    target_dir = path.parent / dest_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{TODAY}-{path.name}"
    path.rename(target)
    print(f"[ingest] Moved {path.name} → {dest_dir}/{target.name}")


def ingest_file(path: Path) -> bool:
    """Ingest a single raw source file. Returns True on success."""
    print(f"[ingest] Processing {path.name}...")

    content = _read_source(path)
    if content is None:
        return False

    prompt = _build_prompt(content, path.name)

    if DRY_RUN:
        print(f"[ingest] DRY RUN — prompt ({len(prompt)} chars):")
        print(prompt)
        return True

    # Check Claude CLI exists
    if not Path(CLAUDE_BIN).exists() and not shutil.which("claude"):
        print("[ingest] ERROR: claude CLI not found. Install or set PATH.", file=sys.stderr)
        sys.exit(1)

    # Call Claude Haiku
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt,
             "--output-format", "text",
             "--no-session-persistence",
             "--model", MODEL],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(f"[ingest] ERROR: Claude CLI timed out for {path.name}")
        _move_file(path, "_errors")
        return False

    if result.returncode != 0:
        print(f"[ingest] ERROR: Claude CLI failed (exit {result.returncode})")
        _move_file(path, "_errors")
        return False

    # Validate JSON
    output = result.stdout.strip()
    # Strip code fences if present
    if output.startswith("```"):
        lines = output.splitlines()
        output = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    try:
        json.loads(output)
    except json.JSONDecodeError as e:
        print(f"[ingest] ERROR: Invalid JSON from LLM ({e})")
        (path.parent / "_errors").mkdir(parents=True, exist_ok=True)
        (path.parent / "_errors" / f"{TODAY}-{path.name}.llm-output").write_text(output)
        _move_file(path, "_errors")
        return False

    # Apply extraction
    apply_script = LIFE_DIR / "scripts" / "apply_extraction.py"
    apply_result = subprocess.run(
        [sys.executable, str(apply_script)],
        input=output, text=True, capture_output=True,
        env={**os.environ, "LIFE_DIR": str(LIFE_DIR), "CONSOLIDATION_DATE": TODAY},
        timeout=30,
    )

    if apply_result.returncode != 0:
        print(f"[ingest] ERROR: apply_extraction failed: {apply_result.stderr[:200]}")
        _move_file(path, "_errors")
        return False

    if apply_result.stdout:
        print(apply_result.stdout.rstrip())

    _move_file(path, "_processed")
    return True


def main() -> None:
    raw_dir = LIFE_DIR / "raw"

    if "--all" in sys.argv:
        # Process all text files in raw/ (top-level only, skip _processed/_errors)
        files = sorted(
            f for f in raw_dir.iterdir()
            if f.is_file() and not f.name.startswith(".") and f.suffix in (".md", ".txt")
        ) if raw_dir.is_dir() else []

        if not files:
            print("[ingest] No files to process in raw/")
            return

        success = 0
        for f in files:
            if ingest_file(f):
                success += 1
        print(f"[ingest] Done: {success}/{len(files)} files processed")
        return

    # Single file mode
    files = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not files:
        print("Usage: ingest_raw.py <file> [--dry-run] [--all]", file=sys.stderr)
        sys.exit(1)

    path = Path(files[0])
    if not path.exists():
        print(f"[ingest] ERROR: {path} not found", file=sys.stderr)
        sys.exit(1)

    ingest_file(path)


if __name__ == "__main__":
    main()
