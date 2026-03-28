"""Memory endpoints."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..config import OPENCLAW_DIR

router = APIRouter()


def _workspace_files():
    """Return workspace markdown files as memory items."""
    ws = OPENCLAW_DIR / "workspace"
    items = []
    if not ws.is_dir():
        return items
    for f in sorted(ws.iterdir()):
        if f.suffix == ".md" and f.is_file():
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            rel = f"workspace/{f.name}"
            items.append({
                "id": rel,
                "path": rel,
                "source": "workspace",
                "title": f.name,
                "text": text,
                "updated_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
    return items


def _search_fts(q: str, limit: int, offset: int):
    """Search the SQLite FTS5 index for matching chunks."""
    db_path = OPENCLAW_DIR / "memory" / "main.sqlite"
    if not db_path.is_file():
        return [], 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Check if chunks_fts table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'")
        if not cur.fetchone():
            conn.close()
            return [], 0
        # Count total matches
        cur.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (q,))
        total = cur.fetchone()[0]
        # Get paginated results with snippets
        cur.execute(
            """SELECT c.id, c.source, c.metadata, snippet(chunks_fts, 0, '<mark>', '</mark>', '...', 40) AS snippet,
                      c.content
               FROM chunks_fts fts
               JOIN chunks c ON c.id = fts.rowid
               WHERE chunks_fts MATCH ?
               ORDER BY rank
               LIMIT ? OFFSET ?""",
            (q, limit, offset),
        )
        rows = cur.fetchall()
        items = []
        for r in rows:
            items.append({
                "id": f"chunk-{r['id']}",
                "path": r["source"] or "",
                "source": "memory",
                "title": r["source"] or f"chunk-{r['id']}",
                "text": r["content"] or "",
                "snippet": r["snippet"],
            })
        conn.close()
        return items, total
    except Exception:
        return [], 0


@router.get("/api/memories")
def list_memories(
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    ws_items = _workspace_files()

    if q:
        # Filter workspace files by search term
        q_lower = q.lower()
        ws_matched = [it for it in ws_items if q_lower in it["text"].lower() or q_lower in it["title"].lower()]
        fts_items, fts_total = _search_fts(q, limit, offset)
        # Combine: workspace matches first, then FTS results
        all_items = ws_matched + fts_items
        total = len(ws_matched) + fts_total
    else:
        all_items = ws_items
        total = len(ws_items)

    # Apply pagination to combined results (workspace files are few so include all)
    paginated = all_items[offset:offset + limit] if not q else all_items[:limit]
    return {"items": paginated, "total": total, "limit": limit, "offset": offset}


@router.get("/api/memories/file")
def get_memory_file(path: str = Query(...)):
    # Path traversal protection
    resolved = (OPENCLAW_DIR / path).resolve()
    if not str(resolved).startswith(str(OPENCLAW_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = resolved.read_text(errors="replace")
    except OSError:
        raise HTTPException(status_code=500, detail="Cannot read file")
    return {"path": path, "content": content}
