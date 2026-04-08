#!/usr/bin/env python3
"""FTS5 search over session digests.

Usage:
    session_search.py --query "polymarket bot"    # Full-text search
    session_search.py --recent 5                  # Last N sessions
    session_search.py --type coding --recent 10   # Filter by type
    session_search.py --backfill                  # Ingest all JSONL files
    session_search.py --rebuild                   # Delete DB + backfill
    session_search.py --index-stdin               # Index one digest from stdin
    session_search.py --json                      # Structured JSON output

Environment:
    LIFE_DIR  — path to ~/life/ (default: ~/life)
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
DB_PATH = Path(os.environ.get("SESSION_DB", LIFE_DIR / "sessions.db"))

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS sessions USING fts5(
    session_id, ts, summary, session_type, decisions,
    files_touched, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS sessions_meta (
    session_id TEXT PRIMARY KEY,
    ts TEXT,
    session_type TEXT,
    msg_count INTEGER,
    tool_counts TEXT
);
"""


def ensure_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Create DB and tables if needed. Returns connection."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    for stmt in SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    return conn


def index_session(conn: sqlite3.Connection, digest: dict, force: bool = False) -> bool:
    """Index a single session digest. Returns True if new/updated, False if skipped.

    Args:
        force: If True, delete and re-insert existing records (for maxwell upserts).
    """
    sid = digest.get("session_id", "")
    if not sid:
        return False

    # Check for duplicate
    row = conn.execute(
        "SELECT 1 FROM sessions_meta WHERE session_id = ?", (sid,)
    ).fetchone()
    if row and not force:
        return False
    if row:
        # FTS5 requires DELETE then INSERT (no UPDATE)
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM sessions_meta WHERE session_id = ?", (sid,))
        conn.commit()

    # Prepare fields with defaults for schema evolution
    ts = digest.get("ts", "")
    summary = digest.get("summary", "")
    session_type = digest.get("session_type", "unknown")
    decisions = "; ".join(digest.get("decisions", []))
    files_touched = "; ".join(digest.get("files_touched", []))
    msg_count = digest.get("msg_count", 0)
    tool_counts = json.dumps(digest.get("tool_counts", {}))

    conn.execute(
        "INSERT INTO sessions(session_id, ts, summary, session_type, decisions, files_touched) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, ts, summary, session_type, decisions, files_touched),
    )
    conn.execute(
        "INSERT OR IGNORE INTO sessions_meta(session_id, ts, session_type, msg_count, tool_counts) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, ts, session_type, msg_count, tool_counts),
    )
    conn.commit()
    return True


def search(conn: sqlite3.Connection, query: str, limit: int = 10,
           session_type: str | None = None) -> list[dict]:
    """Search sessions by FTS5 query."""
    try:
        if session_type:
            rows = conn.execute(
                "SELECT s.session_id, s.ts, s.summary, s.session_type, "
                "s.decisions, s.files_touched, rank "
                "FROM sessions s WHERE sessions MATCH ? AND s.session_type = ? "
                "ORDER BY rank LIMIT ?",
                (query, session_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT session_id, ts, summary, session_type, "
                "decisions, files_touched, rank "
                "FROM sessions WHERE sessions MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"[session-search] Query error: {e}", file=sys.stderr)
        print("[session-search] Tip: escape special chars or use simpler terms", file=sys.stderr)
        return []

    return [
        {"session_id": r[0], "ts": r[1], "summary": r[2], "session_type": r[3],
         "decisions": r[4], "files_touched": r[5], "rank": r[6]}
        for r in rows
    ]


def recent(conn: sqlite3.Connection, limit: int = 10,
           session_type: str | None = None) -> list[dict]:
    """Get most recent sessions with summaries (queries FTS5 table directly)."""
    if session_type:
        rows = conn.execute(
            "SELECT session_id, ts, summary, session_type "
            "FROM sessions WHERE session_type = ? ORDER BY ts DESC LIMIT ?",
            (session_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT session_id, ts, summary, session_type "
            "FROM sessions ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"session_id": r[0], "ts": r[1], "summary": r[2], "session_type": r[3]}
        for r in rows
    ]


def backfill(conn: sqlite3.Connection, life_dir: Path | None = None) -> int:
    """Ingest all session digest JSONL files. Returns count of new entries."""
    ld = life_dir or LIFE_DIR
    count = 0
    for jsonl in sorted(ld.rglob("sessions-digest-*.jsonl")):
        try:
            text = jsonl.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                digest = json.loads(line)
                if index_session(conn, digest):
                    count += 1
            except (json.JSONDecodeError, KeyError):
                continue
    return count


def _parse_maxwell(path: Path) -> dict | None:
    """Parse a maxwell-YYYY-MM-DD.md file into a searchable digest."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Extract date from filename: maxwell-YYYY-MM-DD
    date_str = path.stem.replace("maxwell-", "")

    dispatches: list[str] = []
    heartbeat_counts: dict[str, int] = {}
    section = ""
    in_frontmatter = False

    for line in text.splitlines():
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if line.startswith("## "):
            section = line
            continue
        if not line.startswith("- "):
            continue

        if "Dispatch" in section:
            dispatches.append(line.lstrip("- ").strip())
        elif "Heartbeat" in section:
            # Uses Unicode em-dash (U+2014), not ASCII hyphen
            parts = line.split("\u2014", 1)
            if len(parts) == 2:
                key = parts[1].strip()
                heartbeat_counts[key] = heartbeat_counts.get(key, 0) + 1

    # Build summary
    summary_parts: list[str] = []
    if dispatches:
        summary_parts.append("Dispatches: " + "; ".join(dispatches[:5]))
    else:
        summary_parts.append("No dispatches")
    if heartbeat_counts:
        hb = "; ".join(f"{k} ({v}x)" for k, v in heartbeat_counts.items())
        summary_parts.append("Heartbeat: " + hb)

    if not dispatches and not heartbeat_counts:
        return None

    return {
        "session_id": f"maxwell-{date_str}",
        "ts": f"{date_str}T00:00:00Z",
        "summary": ". ".join(summary_parts),
        "session_type": "maxwell",
        "decisions": [],
        "files_touched": [],
        "tool_counts": {},
        "msg_count": len(dispatches) + sum(heartbeat_counts.values()),
    }


def backfill_maxwell(conn: sqlite3.Connection, life_dir: Path | None = None) -> int:
    """Index all maxwell-*.md files. Uses force=True to update stale records."""
    ld = life_dir or LIFE_DIR
    count = 0
    for path in sorted(ld.rglob("maxwell-*.md")):
        digest = _parse_maxwell(path)
        if digest and index_session(conn, digest, force=True):
            count += 1
    return count


def main() -> None:
    json_output = "--json" in sys.argv

    # --index-stdin: index a single digest from stdin
    if "--index-stdin" in sys.argv:
        try:
            digest = json.loads(sys.stdin.read())
            conn = ensure_db()
            index_session(conn, digest)
            conn.close()
        except (json.JSONDecodeError, sqlite3.Error) as e:
            print(f"[session-search] index-stdin error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # --rebuild: delete DB and backfill
    if "--rebuild" in sys.argv:
        if DB_PATH.exists():
            DB_PATH.unlink()
            # Also remove WAL/SHM files
            for suffix in (".db-wal", ".db-shm"):
                p = DB_PATH.with_suffix(suffix)
                if p.exists():
                    p.unlink()
        print("[session-search] DB deleted, rebuilding...")

    conn = ensure_db()

    # --backfill: ingest all JSONL files
    if "--backfill" in sys.argv or "--rebuild" in sys.argv:
        count = backfill(conn)
        print(f"[session-search] Backfilled {count} sessions")
        if "--rebuild" in sys.argv:
            mcount = backfill_maxwell(conn)
            print(f"[session-search] Backfilled {mcount} maxwell entries")
        conn.close()
        return

    # --backfill-maxwell: ingest all maxwell markdown files
    if "--backfill-maxwell" in sys.argv:
        count = backfill_maxwell(conn)
        print(f"[session-search] Backfilled {count} maxwell entries")
        conn.close()
        return

    # Parse flags
    session_type = None
    limit = 10
    query = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--query" and i + 1 < len(args):
            query = args[i + 1]
            i += 2
        elif args[i] == "--type" and i + 1 < len(args):
            session_type = args[i + 1]
            i += 2
        elif args[i] == "--recent" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        else:
            i += 1

    # Search or recent
    if query:
        results = search(conn, query, limit, session_type)
    else:
        results = recent(conn, limit, session_type)

    if json_output:
        json.dump(results, sys.stdout, indent=2)
        print()
    else:
        if not results:
            print("[session-search] No results")
        for r in results:
            ts = r.get("ts", "?")[:16]
            stype = r.get("session_type", "?")
            summary = r.get("summary", "")[:100]
            print(f"  [{stype}] {ts} — {summary}")

    conn.close()


if __name__ == "__main__":
    main()
