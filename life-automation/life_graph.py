"""Phase 8.0.5 — shared graph module.

Used by:
* 8A lint (``lint_knowledge_llm.py``) — for orphan detection + star grouping.
* 8D index (``generate_index.py``) — for cross-reference rendering + dangling
  detection.

Single source of truth for:
* ``canonical_slug`` — slug normalization + hardened rejection policy
  (non-ASCII, path traversal, shell metachars, null bytes, leading dash/dot).
* ``load_relationships`` — fail-open loader with warning list.
* ``known_entities`` — sorted folder scan skipping ``_*`` / dotfiles.
* ``build_adjacency`` — out/in indices, dangling variants, symmetric collapse.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

__all__ = [
    "Edge",
    "Adjacency",
    "DanglingVariant",
    "SYMMETRIC_RELATIONS",
    "ASYMMETRIC_RELATIONS",
    "canonical_slug",
    "load_relationships",
    "known_entities",
    "build_adjacency",
]

# -------------------------------------------------------------- relation sets

SYMMETRIC_RELATIONS: frozenset[str] = frozenset({
    "related-to",
    "similar-to",
    "collaborates-with",
})

ASYMMETRIC_RELATIONS: frozenset[str] = frozenset({
    "uses",
    "works-on",
    "provides",
    "manages",
    "reports-to",
    "owns",
    "contributes-to",
    "depends-on",
    "blocks",
    "replaces",
    "deprecates",
})


# ---------------------------------------------------------- canonical_slug

# Valid after normalization: starts with alphanumeric, then alphanumerics plus
# ``.-_``. Matches filesystem-safe slugs we control.
_VALID_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.ASCII)

# Shell metachars + whitespace + null + path separators we explicitly reject.
# (NFKC normalization will have turned fullwidth into ASCII first.)
_FORBIDDEN_CHARS = set("`$()[]{};|&<>*?\\/\n\t\r\x00")


def canonical_slug(s: str) -> str:
    """Return a canonical slug or the empty string if ``s`` is unsafe.

    Rejection rules (plan v3 §8.0.5):

    * ``None`` / non-str -> ``""``
    * After ``NFKC`` + ``.strip()`` + ``strip('"\\'')`` + ``.lower()``:
      - empty -> ``""``
      - contains non-ASCII -> ``""``
      - contains ``..`` anywhere -> ``""``
      - contains ``/``, ``\\``, shell metachar, whitespace, or null -> ``""``
      - leading dash (``-``) -> ``""`` (argument injection)
      - leading dot (``.``) -> ``""`` (dotfile collision)
      - does not match ``^[a-z0-9][a-z0-9._-]*$`` -> ``""``
    """
    if not isinstance(s, str):
        return ""
    candidate = unicodedata.normalize("NFKC", s).strip().strip('"\'').lower()
    if not candidate:
        return ""
    # Non-ASCII rejection after NFKC
    try:
        candidate.encode("ascii")
    except UnicodeEncodeError:
        return ""
    if ".." in candidate:
        return ""
    if any(ch in _FORBIDDEN_CHARS for ch in candidate):
        return ""
    if candidate.startswith("-") or candidate.startswith("."):
        return ""
    if not _VALID_SLUG_RE.match(candidate):
        return ""
    return candidate


# ----------------------------------------------------------------- Edge


@dataclass(frozen=True)
class Edge:
    from_: str
    to: str
    relation: str
    from_type: Optional[str] = None
    to_type: Optional[str] = None
    first_seen: str = ""
    last_seen: str = ""


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(value, warnings: list[str], context: str) -> str:
    if not value:
        return ""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        warnings.append(f"{context}: invalid date {value!r}")
        return ""
    # Sanity: month 01-12, day 01-31
    try:
        y, m, d = map(int, value.split("-"))
        if not (1 <= m <= 12 and 1 <= d <= 31):
            warnings.append(f"{context}: out-of-range date {value}")
            return ""
    except ValueError:
        warnings.append(f"{context}: unparseable date {value}")
        return ""
    return value


def load_relationships(path: Path) -> tuple[list[Edge], list[str]]:
    """Parse ``relationships.json`` and return ``(edges, warnings)``.

    Fail-open: returns ``([], [...])`` on invalid JSON or missing file, never
    raises. Caller is responsible for logging / surfacing warnings.
    """
    warnings: list[str] = []
    if not path.exists():
        warnings.append(f"{path}: file not present; continuing without graph data")
        return [], warnings
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        warnings.append(f"{path}: invalid JSON: {exc.msg} at line {exc.lineno}")
        return [], warnings
    except OSError as exc:
        warnings.append(f"{path}: read error: {exc}")
        return [], warnings
    if not isinstance(raw, list):
        warnings.append(f"{path}: top level must be a JSON array, got {type(raw).__name__}")
        return [], warnings

    edges: list[Edge] = []
    for idx, item in enumerate(raw):
        ctx = f"{path.name}[{idx}]"
        if not isinstance(item, dict):
            warnings.append(f"{ctx}: entry is not an object; dropped")
            continue
        raw_from = item.get("from")
        raw_to = item.get("to")
        raw_rel = item.get("relation")
        from_ = canonical_slug(raw_from) if isinstance(raw_from, str) else ""
        to = canonical_slug(raw_to) if isinstance(raw_to, str) else ""
        if not from_:
            warnings.append(f"{ctx}: missing or invalid `from` ({raw_from!r}); dropped")
            continue
        if not to:
            warnings.append(f"{ctx}: missing or invalid `to` ({raw_to!r}); dropped")
            continue
        if not isinstance(raw_rel, str) or not raw_rel:
            warnings.append(f"{ctx}: missing or invalid `relation`; dropped")
            continue
        relation = raw_rel.strip()
        first_seen = _validate_date(item.get("first_seen"), warnings, f"{ctx}.first_seen")
        last_seen = _validate_date(item.get("last_seen"), warnings, f"{ctx}.last_seen")
        if first_seen and last_seen and first_seen > last_seen:
            warnings.append(f"{ctx}: first_seen {first_seen} > last_seen {last_seen}")
        edge = Edge(
            from_=from_,
            to=to,
            relation=relation,
            from_type=item.get("from_type"),
            to_type=item.get("to_type"),
            first_seen=first_seen,
            last_seen=last_seen,
        )
        edges.append(edge)
    return edges, warnings


# --------------------------------------------------------- known_entities

ENTITY_TYPE_DIRS: dict[str, str] = {
    "People": "person",
    "Projects": "project",
    "Companies": "company",
}


def known_entities(life_dir: Path) -> dict[str, str]:
    """Scan ``life_dir`` entity dirs and return ``{canonical_slug: entity_type}``.

    Skips any child whose name starts with ``_`` or ``.`` (template, .git,
    .obsidian, etc). Sort is deterministic (lexicographic).
    """
    out: dict[str, str] = {}
    for dirname, etype in sorted(ENTITY_TYPE_DIRS.items()):
        parent = life_dir / dirname
        if not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            if not child.is_dir():
                continue
            name = child.name
            if name.startswith("_") or name.startswith("."):
                continue
            slug = canonical_slug(name)
            if not slug:
                continue
            if slug not in out:
                out[slug] = etype
    return out


# --------------------------------------------------------- Adjacency + build


DanglingVariant = Literal["missing", "type_mismatch", "case_mismatch"]


@dataclass(frozen=True)
class Dangling:
    slug: str
    variant: DanglingVariant
    detail: str


@dataclass
class Adjacency:
    out: dict[str, dict[str, list[Edge]]] = field(default_factory=dict)
    in_: dict[str, dict[str, list[Edge]]] = field(default_factory=dict)
    dangling: list[Dangling] = field(default_factory=list)

    def neighbors(self, slug: str) -> set[str]:
        seen: set[str] = set()
        for bucket in self.out.get(slug, {}).values():
            for e in bucket:
                seen.add(e.to)
        for bucket in self.in_.get(slug, {}).values():
            for e in bucket:
                seen.add(e.from_)
        seen.discard(slug)
        return seen


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def build_adjacency(edges: list[Edge], known: dict[str, str]) -> Adjacency:
    """Build an adjacency over ``edges`` against ``known`` entities.

    Filters:
    * Self-loops (``from == to``)
    * Exact duplicates (same ``from``, ``to``, ``relation``)
    * Symmetric-relation collapse by canonical ``(min, max)`` slug pair

    Detection:
    * ``missing``: edge endpoint has no entity folder
    * ``type_mismatch``: edge declares ``from_type`` / ``to_type`` different
      from the actual folder type
    """
    adj = Adjacency()
    seen_sym: set[tuple[str, str, str]] = set()  # (canonical_a, canonical_b, relation)
    seen_asym: set[tuple[str, str, str]] = set()

    for edge in edges:
        if edge.from_ == edge.to:
            continue  # self-loop
        # Detect dangling endpoints
        if edge.from_ not in known:
            adj.dangling.append(Dangling(slug=edge.from_, variant="missing",
                                         detail=f"from={edge.from_}, relation={edge.relation}"))
        elif edge.from_type and known[edge.from_] != edge.from_type:
            adj.dangling.append(Dangling(
                slug=edge.from_, variant="type_mismatch",
                detail=f"declared={edge.from_type}, actual={known[edge.from_]}"))
        if edge.to not in known:
            adj.dangling.append(Dangling(slug=edge.to, variant="missing",
                                         detail=f"to={edge.to}, relation={edge.relation}"))
        elif edge.to_type and known[edge.to] != edge.to_type:
            adj.dangling.append(Dangling(
                slug=edge.to, variant="type_mismatch",
                detail=f"declared={edge.to_type}, actual={known[edge.to]}"))

        # Dedupe + symmetric collapse
        if edge.relation in SYMMETRIC_RELATIONS:
            a, b = _canonical_pair(edge.from_, edge.to)
            key = (a, b, edge.relation)
            if key in seen_sym:
                continue
            seen_sym.add(key)
            # Insert a single canonical edge
            canonical_edge = Edge(
                from_=a, to=b, relation=edge.relation,
                from_type=edge.from_type if edge.from_ == a else edge.to_type,
                to_type=edge.to_type if edge.to == b else edge.from_type,
                first_seen=edge.first_seen, last_seen=edge.last_seen,
            )
            adj.out.setdefault(a, {}).setdefault(edge.relation, []).append(canonical_edge)
            adj.in_.setdefault(b, {}).setdefault(edge.relation, []).append(canonical_edge)
        else:
            key = (edge.from_, edge.to, edge.relation)
            if key in seen_asym:
                continue
            seen_asym.add(key)
            adj.out.setdefault(edge.from_, {}).setdefault(edge.relation, []).append(edge)
            adj.in_.setdefault(edge.to, {}).setdefault(edge.relation, []).append(edge)

    return adj
