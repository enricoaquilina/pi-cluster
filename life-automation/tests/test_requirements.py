"""Phase 8.0.0 — dependency pinning + gitignore invariants.

Guards against:
- Accidentally importing forbidden libraries (tenacity, anthropic, python-frontmatter).
- Backup files (`*.bak.*`) leaking into git because the gitignore rules are stale.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

CANONICAL = Path(__file__).resolve().parent.parent
LIFE_DIR = Path.home() / "life"

FORBIDDEN_TOP_LEVEL = {"frontmatter", "tenacity", "anthropic"}


def _py_files() -> list[Path]:
    out: list[Path] = []
    for p in sorted(CANONICAL.glob("*.py")):
        out.append(p)
    for p in sorted(CANONICAL.glob("lib/**/*.py")):
        out.append(p)
    return out


def _top_level_imports(path: Path) -> set[str]:
    """Return the set of top-level module names imported by `path`.

    Parses with ast so comments / strings don't false-trigger.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


# ---------------------------------------------------------------- requirements


def test_requirements_file_exists():
    req = CANONICAL / "requirements.txt"
    assert req.exists(), f"missing {req}"
    text = req.read_text()
    assert "jsonschema" in text
    assert "PyYAML" in text or "pyyaml" in text.lower()
    assert "hypothesis" in text


def test_allowed_libraries_import():
    """Imports of allowed libs must succeed in the CI/local env."""
    import jsonschema  # noqa: F401
    import yaml  # noqa: F401
    import hypothesis  # noqa: F401


def test_forbidden_libraries_not_imported():
    """No canonical .py file imports a forbidden top-level module."""
    offenders: dict[str, set[str]] = {}
    for path in _py_files():
        imports = _top_level_imports(path)
        bad = imports & FORBIDDEN_TOP_LEVEL
        if bad:
            offenders[str(path.relative_to(CANONICAL))] = bad
    assert not offenders, (
        f"forbidden imports present: {offenders}\n"
        f"see requirements.txt for the rationale."
    )


# -------------------------------------------------------------------- gitignore


def test_canonical_gitignore_exists():
    gi = CANONICAL / ".gitignore"
    assert gi.exists(), f"missing {gi}"
    text = gi.read_text()
    assert "logs/" in text
    assert "*.bak.*" in text, "must cover timestamped backups"
    assert ".rewrite.lock" in text


@pytest.mark.skipif(not LIFE_DIR.exists(), reason="~/life/ not present on this node")
def test_life_gitignore_has_timestamped_backup_rule():
    """Regression: `*.bak` alone does NOT match `summary.md.bak.20260409-031522`."""
    gi = LIFE_DIR / ".gitignore"
    assert gi.exists(), f"missing {gi}"
    text = gi.read_text()
    assert "*.bak.*" in text, (
        "~/life/.gitignore needs `*.bak.*` — the pre-v3 rule `*.bak` does not "
        "match timestamped backup files like `summary.md.bak.20260409-031522`."
    )
