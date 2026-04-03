import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ENTITY_GRAPH_SCRIPT = Path(__file__).parent.parent / "entity_graph.py"
APPLY_SCRIPT = Path(__file__).parent.parent / "apply_extraction.py"


@pytest.fixture
def life_dir(tmp_path):
    (tmp_path / "relationships.json").write_text("[]")
    (tmp_path / "logs").mkdir()
    # Create a daily note directory so apply_extraction doesn't break
    daily_dir = tmp_path / "Daily" / "2026" / "03"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-03-30.md").write_text(
        "---\ndate: 2026-03-30\n---\n\n## Consolidation\n_Not yet consolidated_\n"
    )
    return tmp_path


def run_graph(life_dir, args):
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    cmd = [sys.executable, str(ENTITY_GRAPH_SCRIPT)] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


def run_apply(life_dir, input_json, today="2026-03-30"):
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = today
    cmd = [sys.executable, str(APPLY_SCRIPT)]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, input=json.dumps(input_json), timeout=30)


class TestLoadGraph:
    def test_empty_graph(self, life_dir):
        r = run_graph(life_dir, ["full"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["edges"] == []
        assert data["total"] == 0

    def test_valid_graph(self, life_dir):
        (life_dir / "relationships.json").write_text(json.dumps([
            {"from": "enrico", "to": "pi-cluster", "relation": "works-on",
             "from_type": "person", "to_type": "project",
             "first_seen": "2026-03-28", "last_seen": "2026-03-30"},
        ]))
        r = run_graph(life_dir, ["full"])
        data = json.loads(r.stdout)
        assert data["total"] == 1
        assert data["edges"][0]["from"] == "enrico"


class TestAddRelationship:
    def test_new_relationship_via_apply(self, life_dir):
        extraction = {
            "new_entities": [],
            "fact_updates": [],
            "relationships": [
                {"from": "enrico", "from_type": "person",
                 "to": "pi-cluster", "to_type": "project",
                 "relation": "works-on"}
            ],
            "summary": "test",
        }
        r = run_apply(life_dir, extraction)
        assert r.returncode == 0
        rels = json.loads((life_dir / "relationships.json").read_text())
        assert len(rels) == 1
        assert rels[0]["from"] == "enrico"
        assert rels[0]["relation"] == "works-on"

    def test_dedup_updates_last_seen(self, life_dir):
        extraction = {
            "new_entities": [],
            "fact_updates": [],
            "relationships": [
                {"from": "enrico", "from_type": "person",
                 "to": "pi-cluster", "to_type": "project",
                 "relation": "works-on"}
            ],
            "summary": "test",
        }
        run_apply(life_dir, extraction, today="2026-03-28")
        run_apply(life_dir, extraction, today="2026-03-30")
        rels = json.loads((life_dir / "relationships.json").read_text())
        assert len(rels) == 1
        assert rels[0]["last_seen"] == "2026-03-30"

    def test_different_relation_not_deduped(self, life_dir):
        extraction1 = {
            "new_entities": [],
            "fact_updates": [],
            "relationships": [
                {"from": "enrico", "from_type": "person",
                 "to": "pi-cluster", "to_type": "project",
                 "relation": "works-on"}
            ],
            "summary": "test",
        }
        extraction2 = {
            "new_entities": [],
            "fact_updates": [],
            "relationships": [
                {"from": "enrico", "from_type": "person",
                 "to": "pi-cluster", "to_type": "project",
                 "relation": "owns"}
            ],
            "summary": "test",
        }
        run_apply(life_dir, extraction1)
        run_apply(life_dir, extraction2)
        rels = json.loads((life_dir / "relationships.json").read_text())
        assert len(rels) == 2

    def test_invalid_relation_skipped(self, life_dir):
        extraction = {
            "new_entities": [],
            "fact_updates": [],
            "relationships": [
                {"from": "enrico", "from_type": "person",
                 "to": "pi-cluster", "to_type": "project",
                 "relation": "loves"}
            ],
            "summary": "test",
        }
        run_apply(life_dir, extraction)
        rels = json.loads((life_dir / "relationships.json").read_text())
        assert len(rels) == 0


class TestQueryConnections:
    def test_connections(self, life_dir):
        (life_dir / "relationships.json").write_text(json.dumps([
            {"from": "enrico", "to": "pi-cluster", "relation": "works-on",
             "from_type": "person", "to_type": "project",
             "first_seen": "2026-03-28", "last_seen": "2026-03-30"},
            {"from": "archie", "to": "pi-cluster", "relation": "contributes-to",
             "from_type": "person", "to_type": "project",
             "first_seen": "2026-03-29", "last_seen": "2026-03-30"},
            {"from": "enrico", "to": "polymarket-bot", "relation": "owns",
             "from_type": "person", "to_type": "project",
             "first_seen": "2026-03-28", "last_seen": "2026-03-30"},
        ]))
        r = run_graph(life_dir, ["connections", "enrico"])
        data = json.loads(r.stdout)
        assert data["total"] == 2
        assert data["entity"] == "enrico"

    def test_connections_no_match(self, life_dir):
        r = run_graph(life_dir, ["connections", "nobody"])
        data = json.loads(r.stdout)
        assert data["total"] == 0


class TestQueryByRelation:
    def test_who_works_on(self, life_dir):
        (life_dir / "relationships.json").write_text(json.dumps([
            {"from": "enrico", "to": "pi-cluster", "relation": "works-on",
             "from_type": "person", "to_type": "project",
             "first_seen": "2026-03-28", "last_seen": "2026-03-30"},
            {"from": "archie", "to": "pi-cluster", "relation": "works-on",
             "from_type": "person", "to_type": "project",
             "first_seen": "2026-03-29", "last_seen": "2026-03-30"},
        ]))
        r = run_graph(life_dir, ["who-works-on", "pi-cluster"])
        data = json.loads(r.stdout)
        assert data["total"] == 2


class TestGraphStats:
    def test_stats(self, life_dir):
        (life_dir / "relationships.json").write_text(json.dumps([
            {"from": "enrico", "to": "pi-cluster", "relation": "works-on",
             "from_type": "person", "to_type": "project",
             "first_seen": "2026-03-28", "last_seen": "2026-03-30"},
            {"from": "enrico", "to": "polymarket-bot", "relation": "owns",
             "from_type": "person", "to_type": "project",
             "first_seen": "2026-03-28", "last_seen": "2026-03-30"},
        ]))
        r = run_graph(life_dir, ["stats"])
        data = json.loads(r.stdout)
        assert data["entity_count"] == 3
        assert data["edge_count"] == 2
        assert data["relations"]["works-on"] == 1
        assert data["relations"]["owns"] == 1
        assert "enrico" in data["entities"]

    def test_stats_empty(self, life_dir):
        r = run_graph(life_dir, ["stats"])
        data = json.loads(r.stdout)
        assert data["entity_count"] == 0
        assert data["edge_count"] == 0
