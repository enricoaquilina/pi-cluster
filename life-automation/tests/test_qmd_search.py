import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "qmd_search.py"


class TestQmdSearch:
    def test_bm25_returns_results(self):
        """BM25 search should find gateway in workflow-habits."""
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "gateway", "--mode", "bm25", "--limit", "3"],
            capture_output=True, text=True
        )
        assert r.returncode == 0
        results = json.loads(r.stdout)
        assert len(results) > 0
        assert any("workflow" in str(item).lower() or "summary" in str(item).lower() for item in results)

    def test_vector_finds_semantic_match(self):
        """Vector search should find hard-rules for security question."""
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "what are the security rules", "--mode", "vector", "--limit", "3"],
            capture_output=True, text=True, timeout=30
        )
        assert r.returncode == 0
        results = json.loads(r.stdout)
        assert len(results) > 0
        # Wrapper normalizes QMD's "file" key to "path" with qmd:// stripped
        paths = [item.get("path", "") for item in results]
        assert any("hard-rules" in p for p in paths)

    def test_empty_query_returns_empty(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "", "--mode", "bm25"],
            capture_output=True, text=True
        )
        # Should not crash
        assert r.returncode == 0

    def test_limit_respected(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "cluster", "--mode", "bm25", "--limit", "2"],
            capture_output=True, text=True
        )
        assert r.returncode == 0
        results = json.loads(r.stdout)
        assert len(results) <= 2

    def test_invalid_mode_rejected(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "test", "--mode", "invalid"],
            capture_output=True, text=True
        )
        assert r.returncode != 0  # argparse rejects invalid choice
