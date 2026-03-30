"""Memory endpoints."""

import json
import os
import sqlite3
import subprocess
import time
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..config import LIFE_DIR, OPENCLAW_DIR, QMD_INDEX

router = APIRouter()

# TTL cache for file listings (avoid re-reading filesystem on every request)
_cache: dict[str, tuple[float, list]] = {}
_CACHE_TTL = 30  # seconds


def _cached(key: str, loader):
    """Return cached result if fresh, otherwise call loader and cache."""
    now = time.monotonic()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < _CACHE_TTL:
            return data
    data = loader()
    _cache[key] = (now, data)
    return data


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


def _life_files():
    """Return ~/life/ PARA files as memory items (recursive)."""
    items = []
    if not LIFE_DIR.is_dir():
        return items
    skip_dirs = {"scripts", "logs", "_template", "__pycache__", ".pytest_cache"}
    for f in sorted(LIFE_DIR.rglob("*")):
        if f.suffix not in (".md", ".json") or not f.is_file():
            continue
        if any(skip in f.parts for skip in skip_dirs):
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(LIFE_DIR))
        title = f"{f.parent.name}/{f.name}" if f.parent != LIFE_DIR else f.name
        items.append({
            "id": f"life/{rel}",
            "path": f"life/{rel}",
            "source": "life",
            "title": title,
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
    ws_items = _cached("workspace", _workspace_files)
    life_items = _cached("life", _life_files)

    if q:
        # Filter workspace files by search term
        q_lower = q.lower()
        ws_matched = [it for it in ws_items if q_lower in it["text"].lower() or q_lower in it["title"].lower()]
        life_matched = [it for it in life_items if q_lower in it["text"].lower() or q_lower in it["title"].lower()]
        fts_items, fts_total = _search_fts(q, limit, offset)
        # Combine: workspace matches first, then life, then FTS results
        all_items = ws_matched + life_matched + fts_items
        total = len(ws_matched) + len(life_matched) + fts_total
    else:
        all_items = ws_items + life_items
        total = len(ws_items) + len(life_items)

    # Apply pagination to combined results (workspace files are few so include all)
    paginated = all_items[offset:offset + limit] if not q else all_items[:limit]
    return {"items": paginated, "total": total, "limit": limit, "offset": offset}


@router.get("/api/memories/file")
def get_memory_file(path: str = Query(...)):
    # Resolve against the correct base directory
    if path.startswith("life/"):
        base_dir = LIFE_DIR
        rel_path = path[len("life/"):]
    else:
        base_dir = OPENCLAW_DIR
        rel_path = path
    # Path traversal protection
    resolved = (base_dir / rel_path).resolve()
    if not str(resolved).startswith(str(base_dir.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = resolved.read_text(errors="replace")
    except OSError:
        raise HTTPException(status_code=500, detail="Cannot read file")
    return {"path": path, "content": content}


@router.get("/api/life/daily-status")
def daily_status():
    today = date.today()
    note_path = LIFE_DIR / "Daily" / str(today.year) / f"{today.month:02d}" / f"{today}.md"
    exists = note_path.is_file()
    size = note_path.stat().st_size if exists else 0
    consolidated = False
    if exists:
        content = note_path.read_text(errors="replace")
        consolidated = "_Not yet consolidated_" not in content
    return {
        "date": str(today),
        "exists": exists,
        "size": size,
        "consolidated": consolidated,
        "path": str(note_path.relative_to(LIFE_DIR)) if exists else None,
    }


def _qmd_search_fts(q: str, limit: int) -> list[dict]:
    """Search QMD's FTS5 index directly via sqlite3."""
    if not QMD_INDEX.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{QMD_INDEX}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # QMD FTS5 has columns: filepath, title, body (no join needed)
        cur.execute(
            """SELECT
                   filepath,
                   title,
                   snippet(documents_fts, 2, '<mark>', '</mark>', '...', 40) AS snippet,
                   rank
               FROM documents_fts
               WHERE documents_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (q, limit),
        )
        rows = cur.fetchall()
        items = [
            {
                "path": r["filepath"],
                "title": r["title"],
                "snippet": r["snippet"],
                "score": round(-r["rank"], 4),
            }
            for r in rows
        ]
        conn.close()
        return items
    except Exception:
        return []


@router.get("/api/qmd/search")
def qmd_search(
    q: str = Query(...),
    mode: str = Query("bm25", regex="^(bm25|vector|hybrid)$"),
    limit: int = Query(10, ge=1, le=50),
):
    """Search ~/life/ via QMD (BM25 mode reads FTS5 index directly)."""
    if mode != "bm25":
        # Vector/hybrid modes require the qmd binary which isn't in the container.
        # Fall back to the host-side search script if mounted.
        search_script = LIFE_DIR / "scripts" / "qmd_search.py"
        if not search_script.exists():
            raise HTTPException(
                status_code=503,
                detail="QMD vector/hybrid search not available in container",
            )
        try:
            result = subprocess.run(
                ["python3", str(search_script), q, "--mode", mode, "--limit", str(limit)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return {"items": [], "total": 0, "mode": mode, "error": result.stderr[:200]}
            items = json.loads(result.stdout)
            return {"items": items, "total": len(items), "mode": mode}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            return {"items": [], "total": 0, "mode": mode, "error": str(e)}

    # BM25: read FTS5 index directly (no external binary needed)
    items = _qmd_search_fts(q, limit)
    return {"items": items, "total": len(items), "mode": mode}


@router.get("/api/life/heartbeat")
def life_heartbeat():
    """Run heartbeat check on active projects."""
    search_script = LIFE_DIR / "scripts" / "heartbeat_check.py"
    if not search_script.exists():
        raise HTTPException(status_code=503, detail="Heartbeat script not available")
    try:
        result = subprocess.run(
            ["python3", str(search_script), "--json", "--dry-run"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "LIFE_DIR": str(LIFE_DIR)},
        )
        if result.returncode != 0:
            return {"error": result.stderr[:200]}
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"error": str(e)}


@router.get("/api/life/graph")
def life_graph(
    entity: Optional[str] = Query(None),
    relation: Optional[str] = Query(None),
):
    """Query entity relationship graph."""
    graph_script = LIFE_DIR / "scripts" / "entity_graph.py"
    if not graph_script.exists():
        raise HTTPException(status_code=503, detail="Entity graph script not available")
    cmd = ["python3", str(graph_script)]
    if entity:
        cmd.extend(["connections", entity])
    else:
        cmd.append("full")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
            env={**os.environ, "LIFE_DIR": str(LIFE_DIR)},
        )
        if result.returncode != 0:
            return {"edges": [], "total": 0, "error": result.stderr[:200]}
        data = json.loads(result.stdout)
        if relation:
            data["edges"] = [e for e in data.get("edges", []) if e.get("relation") == relation]
            data["total"] = len(data["edges"])
        return data
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"edges": [], "total": 0, "error": str(e)}
