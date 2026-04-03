#!/usr/bin/env python3
"""
QMD search wrapper. Calls qmd CLI and returns structured JSON.
Usage: python3 qmd_search.py "query" [--mode bm25|vector|hybrid] [--limit 10]
"""
import json
import subprocess
import sys
from pathlib import Path

QMD_BIN = Path.home() / ".local" / "bin" / "qmd"

def search(query: str, mode: str = "bm25", limit: int = 10) -> list[dict]:
    """Run QMD search and return parsed results."""
    if not QMD_BIN.exists():
        return []

    cmd_map = {"bm25": "search", "vector": "vsearch", "hybrid": "query"}
    cmd = cmd_map.get(mode, "search")

    try:
        result = subprocess.run(
            [str(QMD_BIN), cmd, query, "-n", str(limit), "-c", "life", "--json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return []
        raw = json.loads(result.stdout)
        # Normalize keys: QMD uses "file" for path, strip qmd:// prefix
        normalized = []
        for item in raw:
            path = item.get("file", item.get("path", ""))
            if path.startswith("qmd://"):
                path = path[len("qmd://"):]
            normalized.append({
                "path": path,
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "score": item.get("score", 0),
            })
        return normalized
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--mode", choices=["bm25", "vector", "hybrid"], default="bm25")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    results = search(args.query, args.mode, args.limit)
    json.dump(results, sys.stdout, indent=2)
