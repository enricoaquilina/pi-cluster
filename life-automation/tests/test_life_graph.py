"""Phase 8.0.5 — life_graph.py tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

CANONICAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANONICAL))

from life_graph import (  # noqa: E402
    Edge,
    build_adjacency,
    canonical_slug,
    known_entities,
    load_relationships,
)


# =========================================================== canonical_slug


@pytest.mark.parametrize("inp,expected", [
    ("alpha", "alpha"),
    ("Pi-Cluster", "pi-cluster"),
    ("  gadget  ", "gadget"),
    ('"quoted"', "quoted"),
    ("'single'", "single"),
    ("a1b2_c3", "a1b2_c3"),
    ("with.dot", "with.dot"),
])
def test_canonical_slug_accepts_valid(inp, expected):
    assert canonical_slug(inp) == expected


@pytest.mark.parametrize("bad", [
    "",
    "α-β",                  # non-ASCII
    "../etc",               # path traversal
    "foo/bar",              # forward slash
    "foo\\bar",             # backslash
    "..",                   # pure traversal
    "foo$(rm)",             # shell substitution
    "foo`bar`",             # backticks
    "foo;bar",              # semicolon
    "foo|bar",              # pipe
    "foo&",                 # background
    "foo*",                 # glob
    "foo?",                 # glob
    "foo\x00bar",           # null byte mid-string
    "foo\nbar",             # newline mid-string
    "foo\tbar",             # tab mid-string
    "-rf",                  # leading dash
    ".hidden",              # leading dot
    '"""',                  # empty after strip
    "foo bar",              # whitespace
    "foo<bar>",             # redirection
    "foo{bar}",             # brace
])
def test_canonical_slug_rejects_unsafe(bad):
    assert canonical_slug(bad) == ""


def test_canonical_slug_non_string():
    assert canonical_slug(None) == ""
    assert canonical_slug(42) == ""
    assert canonical_slug(["alpha"]) == ""


def test_canonical_slug_nfkc_fullwidth():
    # Fullwidth Latin → ASCII under NFKC
    assert canonical_slug("ａｌｐｈａ") == "alpha"


def test_canonical_slug_idempotent_simple():
    for s in ("alpha", "pi-cluster", "abc_def.ghi"):
        assert canonical_slug(canonical_slug(s)) == canonical_slug(s)


@given(st.text(min_size=0, max_size=80))
def test_canonical_slug_idempotent_hypothesis(s):
    """For arbitrary input, slug is idempotent."""
    once = canonical_slug(s)
    twice = canonical_slug(once)
    assert once == twice


@given(st.text(min_size=0, max_size=80))
def test_canonical_slug_output_is_safe(s):
    """Whatever comes out must pass the valid-slug regex or be empty."""
    out = canonical_slug(s)
    if out:
        assert out == canonical_slug(out)
        assert ".." not in out
        assert "/" not in out
        assert not out.startswith("-")
        assert not out.startswith(".")


# ========================================================= load_relationships


def test_load_relationships_missing_file(tmp_path):
    edges, warnings = load_relationships(tmp_path / "nope.json")
    assert edges == []
    assert any("not present" in w for w in warnings)


def test_load_relationships_invalid_json(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("[\n  {broken,\n]")
    edges, warnings = load_relationships(p)
    assert edges == []
    assert any("invalid JSON" in w for w in warnings)


def test_load_relationships_top_level_not_list(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"items": []}')
    edges, warnings = load_relationships(p)
    assert edges == []
    assert any("array" in w for w in warnings)


def test_load_relationships_happy_path(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([
        {"from": "alpha", "to": "gadget", "relation": "works-on",
         "first_seen": "2026-01-01", "last_seen": "2026-04-01"},
        {"from": "beta", "to": "gadget", "relation": "manages"},
    ]))
    edges, warnings = load_relationships(p)
    assert len(edges) == 2
    assert edges[0].from_ == "alpha"
    assert edges[0].last_seen == "2026-04-01"
    assert warnings == []


def test_load_relationships_drops_missing_fields(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([
        {"to": "gadget", "relation": "works-on"},  # missing from
        {"from": "alpha", "relation": "works-on"},  # missing to
        {"from": "alpha", "to": "gadget"},  # missing relation
        {"from": "alpha", "to": "gadget", "relation": "works-on"},  # ok
    ]))
    edges, warnings = load_relationships(p)
    assert len(edges) == 1
    assert len(warnings) == 3


def test_load_relationships_rejects_traversal(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([
        {"from": "../etc/passwd", "to": "gadget", "relation": "uses"},
        {"from": "alpha", "to": "foo/bar", "relation": "uses"},
    ]))
    edges, warnings = load_relationships(p)
    assert edges == []
    assert len(warnings) == 2


def test_load_relationships_invalid_date_warns_but_keeps(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([
        {"from": "a", "to": "b", "relation": "uses", "last_seen": "2030-13-45"},
    ]))
    edges, warnings = load_relationships(p)
    assert len(edges) == 1
    assert edges[0].last_seen == ""  # coerced to empty
    # Either the regex fails (invalid date) or range check fails (out-of-range)
    assert any("date" in w for w in warnings)


def test_load_relationships_first_after_last_warns(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([
        {"from": "a", "to": "b", "relation": "uses",
         "first_seen": "2026-05-01", "last_seen": "2026-01-01"},
    ]))
    edges, warnings = load_relationships(p)
    assert len(edges) == 1
    assert any("first_seen" in w and "last_seen" in w for w in warnings)


def test_load_relationships_unknown_top_level_field_ignored(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([
        {"from": "a", "to": "b", "relation": "uses", "strength": 0.5, "source": "test"},
    ]))
    edges, _ = load_relationships(p)
    assert len(edges) == 1  # unknown field silently ignored


# =============================================================== known_entities


def test_known_entities_scans_three_dirs(mini_life):
    ents = known_entities(mini_life)
    assert ents.get("alpha") == "person"
    assert ents.get("beta") == "person"
    assert ents.get("delta") == "person"
    assert ents.get("gadget") == "project"
    assert ents.get("widget") == "project"
    assert ents.get("pi-hole") == "project"
    assert ents.get("acme") == "company"


def test_known_entities_skips_hidden_and_template(tmp_path):
    (tmp_path / "People" / "_template").mkdir(parents=True)
    (tmp_path / "People" / ".obsidian").mkdir()
    (tmp_path / "People" / "real").mkdir()
    ents = known_entities(tmp_path)
    assert list(ents.keys()) == ["real"]


def test_known_entities_deterministic_order(mini_life):
    a = list(known_entities(mini_life).items())
    b = list(known_entities(mini_life).items())
    assert a == b  # insertion order deterministic


def test_known_entities_missing_dirs_ok(tmp_path):
    # No People/Projects/Companies subdirs
    assert known_entities(tmp_path) == {}


# ================================================================ build_adjacency


def _edge(f, t, r, **kw):
    return Edge(from_=f, to=t, relation=r, **kw)


def test_build_adjacency_basic_out_in():
    edges = [_edge("alpha", "gadget", "works-on")]
    known = {"alpha": "person", "gadget": "project"}
    adj = build_adjacency(edges, known)
    assert "alpha" in adj.out
    assert "gadget" in adj.in_
    assert adj.out["alpha"]["works-on"][0].to == "gadget"
    assert adj.in_["gadget"]["works-on"][0].from_ == "alpha"
    assert adj.dangling == []


def test_build_adjacency_filters_self_loop():
    edges = [_edge("alpha", "alpha", "related-to")]
    adj = build_adjacency(edges, {"alpha": "person"})
    assert adj.out == {}
    assert adj.in_ == {}


def test_build_adjacency_dedupes_exact():
    edges = [
        _edge("a", "b", "uses"),
        _edge("a", "b", "uses"),  # duplicate
    ]
    adj = build_adjacency(edges, {"a": "project", "b": "project"})
    assert len(adj.out["a"]["uses"]) == 1


def test_build_adjacency_keeps_multi_relation_same_pair():
    edges = [
        _edge("a", "b", "uses"),
        _edge("a", "b", "provides"),
    ]
    adj = build_adjacency(edges, {"a": "company", "b": "project"})
    assert "uses" in adj.out["a"]
    assert "provides" in adj.out["a"]


def test_build_adjacency_symmetric_collapsed():
    """related-to should produce exactly one edge regardless of direction."""
    edges = [
        _edge("a", "b", "related-to"),
        _edge("b", "a", "related-to"),  # reverse; should collapse
    ]
    adj = build_adjacency(edges, {"a": "project", "b": "project"})
    # Exactly one edge stored
    total = sum(
        len(buckets)
        for entity in adj.out.values()
        for buckets in entity.values()
    )
    assert total == 1


def test_build_adjacency_dangling_missing():
    edges = [_edge("alpha", "ghost", "works-on")]
    adj = build_adjacency(edges, {"alpha": "person"})
    variants = [d.variant for d in adj.dangling]
    assert "missing" in variants
    assert any(d.slug == "ghost" for d in adj.dangling)


def test_build_adjacency_dangling_type_mismatch():
    edges = [
        _edge("alpha", "gadget", "works-on",
              from_type="project",  # wrong — alpha is a person
              to_type="project"),
    ]
    adj = build_adjacency(edges, {"alpha": "person", "gadget": "project"})
    variants = [d.variant for d in adj.dangling]
    assert "type_mismatch" in variants


# ================================================================ neighbors


def test_adjacency_neighbors_includes_both_sides():
    edges = [
        _edge("alpha", "gadget", "works-on"),
        _edge("beta", "gadget", "manages"),
    ]
    adj = build_adjacency(edges, {"alpha": "person", "beta": "person", "gadget": "project"})
    assert adj.neighbors("gadget") == {"alpha", "beta"}
    assert adj.neighbors("alpha") == {"gadget"}


def test_neighbors_excludes_self():
    edges = [_edge("a", "b", "uses"), _edge("b", "a", "uses")]
    adj = build_adjacency(edges, {"a": "project", "b": "project"})
    assert "a" not in adj.neighbors("a")
    assert "b" not in adj.neighbors("b")


# ================================================================ mini_life


def test_mini_life_full_load(mini_life):
    """End-to-end: load real fixture, build adjacency, verify."""
    edges, warnings = load_relationships(mini_life / "relationships.json")
    assert warnings == []
    known = known_entities(mini_life)
    adj = build_adjacency(edges, known)
    # gadget is the hub
    assert "works-on" in adj.in_["gadget"]
    assert "manages" in adj.in_["gadget"]
    assert "provides" in adj.in_["gadget"]
    # gadget → pi-hole
    assert "uses" in adj.out["gadget"]
    # No dangling (pi-hole exists)
    assert adj.dangling == []
