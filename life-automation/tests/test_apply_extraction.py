import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "apply_extraction.py"
TODAY = "2026-03-28"


@pytest.fixture
def life_dir(tmp_path: Path) -> Path:
    """Minimal ~/life/ tree matching the real structure (YYYY/MM hierarchy for Daily)."""
    (tmp_path / "Projects/pi-cluster").mkdir(parents=True)
    (tmp_path / "Projects/pi-cluster/items.json").write_text("[]")
    (tmp_path / "Projects/pi-cluster/summary.md").write_text("---\ntype: project\n---\n")
    (tmp_path / "Projects/_template").mkdir()
    (tmp_path / "People").mkdir()
    (tmp_path / "Companies").mkdir()
    (tmp_path / "Areas/about-me").mkdir(parents=True)
    for f in ["hard-rules", "workflow-habits", "communication-preferences", "lessons-learned"]:
        (tmp_path / f"Areas/about-me/{f}.md").write_text(f"# {f}\n")
    # Daily uses YYYY/MM/ hierarchy
    (tmp_path / "Daily/2026/03").mkdir(parents=True)
    (tmp_path / f"Daily/2026/03/{TODAY}.md").write_text(
        f"---\ndate: {TODAY}\n---\n\n_Not yet consolidated_"
    )
    return tmp_path


def run_apply(
    life_dir: Path, payload: dict, today: str = TODAY, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = today
    cmd = [sys.executable, str(SCRIPT)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(
        cmd,
        input=json.dumps(payload).encode(),
        capture_output=True,
        env=env,
    )


def run_apply_raw(life_dir: Path, raw_input: bytes, today: str = TODAY) -> subprocess.CompletedProcess:
    """Send raw bytes (not JSON) for fence stripping / invalid input tests."""
    env = os.environ.copy()
    env["LIFE_DIR"] = str(life_dir)
    env["CONSOLIDATION_DATE"] = today
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=raw_input,
        capture_output=True,
        env=env,
    )


def empty_payload(**overrides) -> dict:
    base = {"new_entities": [], "fact_updates": [], "tacit_knowledge": [], "summary": ""}
    base.update(overrides)
    return base


class TestNewEntityCreation:
    def test_creates_person_folder(self, life_dir: Path) -> None:
        r = run_apply(life_dir, empty_payload(
            new_entities=[{"type": "person", "name": "archie", "display": "Archie"}]
        ))
        assert r.returncode == 0
        assert (life_dir / "People/archie/summary.md").exists()
        assert (life_dir / "People/archie/items.json").exists()
        assert json.loads((life_dir / "People/archie/items.json").read_text()) == []

    def test_creates_company_folder(self, life_dir: Path) -> None:
        r = run_apply(life_dir, empty_payload(
            new_entities=[{"type": "company", "name": "openrouter", "display": "OpenRouter"}]
        ))
        assert r.returncode == 0
        assert (life_dir / "Companies/openrouter/summary.md").exists()

    def test_does_not_overwrite_existing_entity(self, life_dir: Path) -> None:
        run_apply(life_dir, empty_payload(
            new_entities=[{"type": "person", "name": "archie", "display": "Archie"}]
        ))
        (life_dir / "People/archie/summary.md").write_text("SENTINEL")
        run_apply(life_dir, empty_payload(
            new_entities=[{"type": "person", "name": "archie", "display": "Archie"}]
        ))
        assert (life_dir / "People/archie/summary.md").read_text() == "SENTINEL"

    def test_summary_contains_type_and_display(self, life_dir: Path) -> None:
        run_apply(life_dir, empty_payload(
            new_entities=[{"type": "project", "name": "new-proj", "display": "New Project"}]
        ))
        content = (life_dir / "Projects/new-proj/summary.md").read_text()
        assert "New Project" in content
        assert "project" in content

    def test_rejects_unknown_entity_type(self, life_dir: Path) -> None:
        r = run_apply(life_dir, empty_payload(
            new_entities=[{"type": "spaceship", "name": "ufo", "display": "UFO"}]
        ))
        assert r.returncode == 0  # doesn't crash, just skips
        assert b"unknown entity type" in r.stderr


class TestFactUpdates:
    def test_appends_fact_to_existing_entity(self, life_dir: Path) -> None:
        r = run_apply(life_dir, empty_payload(
            fact_updates=[{
                "entity_type": "project", "entity": "pi-cluster",
                "date": TODAY, "fact": "upgraded to v2", "category": "deployment"
            }]
        ))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 1
        assert items[0]["fact"] == "upgraded to v2"
        assert items[0]["category"] == "deployment"
        assert items[0]["source"] == f"daily/{TODAY}"

    def test_skips_fact_for_nonexistent_entity_gracefully(self, life_dir: Path) -> None:
        r = run_apply(life_dir, empty_payload(
            fact_updates=[{
                "entity_type": "project", "entity": "does-not-exist",
                "date": TODAY, "fact": "...", "category": "event"
            }]
        ))
        assert r.returncode == 0

    def test_accumulates_multiple_facts(self, life_dir: Path) -> None:
        for i in range(3):
            run_apply(life_dir, empty_payload(
                fact_updates=[{
                    "entity_type": "project", "entity": "pi-cluster",
                    "date": TODAY, "fact": f"fact {i}", "category": "event"
                }]
            ))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 3

    def test_recovers_from_corrupt_items_json(self, life_dir: Path) -> None:
        (life_dir / "Projects/pi-cluster/items.json").write_text("NOT JSON {{{")
        r = run_apply(life_dir, empty_payload(
            fact_updates=[{
                "entity_type": "project", "entity": "pi-cluster",
                "date": TODAY, "fact": "recovery test", "category": "event"
            }]
        ))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 1

    def test_skips_duplicate_fact(self, life_dir: Path) -> None:
        """Conflict detection: exact duplicate facts should be skipped."""
        payload = empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "same fact twice", "category": "event"
        }])
        run_apply(life_dir, payload)
        run_apply(life_dir, payload)
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 1  # not 2


class TestTacitKnowledge:
    def test_appends_to_hard_rules(self, life_dir: Path) -> None:
        r = run_apply(life_dir, empty_payload(
            tacit_knowledge=[{"file": "hard-rules", "entry": "Never skip smoke tests."}]
        ))
        assert r.returncode == 0
        content = (life_dir / "Areas/about-me/hard-rules.md").read_text()
        assert "Never skip smoke tests." in content

    def test_appends_to_correct_file(self, life_dir: Path) -> None:
        run_apply(life_dir, empty_payload(
            tacit_knowledge=[{"file": "workflow-habits", "entry": "Use tmux for long ops."}]
        ))
        assert "Use tmux for long ops." in (
            life_dir / "Areas/about-me/workflow-habits.md"
        ).read_text()
        assert "Use tmux for long ops." not in (
            life_dir / "Areas/about-me/hard-rules.md"
        ).read_text()


class TestSkillExtraction:
    def test_creates_skill_file(self, life_dir: Path) -> None:
        (life_dir / "Resources/skills").mkdir(parents=True, exist_ok=True)
        r = run_apply(life_dir, empty_payload(
            skills=[{"name": "rotate-api-keys", "display": "How to Rotate API Keys",
                     "steps": ["Stop the service", "Generate new key", "Update .env", "Restart"]}]
        ))
        assert r.returncode == 0
        skill = life_dir / "Resources/skills/rotate-api-keys.md"
        assert skill.exists()
        content = skill.read_text()
        assert "How to Rotate API Keys" in content
        assert "1. Stop the service" in content
        assert "4. Restart" in content

    def test_does_not_overwrite_existing_skill(self, life_dir: Path) -> None:
        (life_dir / "Resources/skills").mkdir(parents=True, exist_ok=True)
        run_apply(life_dir, empty_payload(
            skills=[{"name": "test-skill", "display": "Test", "steps": ["Step 1"]}]
        ))
        (life_dir / "Resources/skills/test-skill.md").write_text("SENTINEL")
        run_apply(life_dir, empty_payload(
            skills=[{"name": "test-skill", "display": "Test", "steps": ["Step 1"]}]
        ))
        assert (life_dir / "Resources/skills/test-skill.md").read_text() == "SENTINEL"


class TestConsolidationLog:
    def test_replaces_placeholder_in_daily_note(self, life_dir: Path) -> None:
        r = run_apply(life_dir, empty_payload(summary="Set up the life system."))
        assert r.returncode == 0
        content = (life_dir / f"Daily/2026/03/{TODAY}.md").read_text()
        assert "_Not yet consolidated_" not in content
        assert "Set up the life system." in content

    def test_does_not_double_write_consolidation_log(self, life_dir: Path) -> None:
        run_apply(life_dir, empty_payload(summary="First run."))
        run_apply(life_dir, empty_payload(summary="Second run."))
        content = (life_dir / f"Daily/2026/03/{TODAY}.md").read_text()
        assert content.count("Consolidat") <= 2  # section header + one log entry

    def test_no_daily_note_does_not_crash(self, life_dir: Path) -> None:
        (life_dir / f"Daily/2026/03/{TODAY}.md").unlink()
        r = run_apply(life_dir, empty_payload(summary="No note today."))
        assert r.returncode == 0


class TestMarkdownFenceStripping:
    def test_strips_json_code_fence(self, life_dir: Path) -> None:
        payload = empty_payload(
            new_entities=[{"type": "person", "name": "test-person", "display": "Test"}]
        )
        fenced = f"```json\n{json.dumps(payload)}\n```".encode()
        r = run_apply_raw(life_dir, fenced)
        assert r.returncode == 0
        assert (life_dir / "People/test-person/summary.md").exists()

    def test_strips_plain_code_fence(self, life_dir: Path) -> None:
        payload = empty_payload()
        fenced = f"```\n{json.dumps(payload)}\n```".encode()
        r = run_apply_raw(life_dir, fenced)
        assert r.returncode == 0


class TestFuzzyReinforcement:
    """Phase 4: Jaccard fuzzy matching for reinforce_fact()."""

    def test_similar_case_insensitive(self, life_dir: Path) -> None:
        """'Gateway runs on heavy' vs 'gateway runs on heavy' → reinforced."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "Gateway runs on heavy", "confidence": "single", "mentions": 1, "date": TODAY, "last_seen": TODAY}
        ]))
        r = run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "gateway runs on heavy", "category": "deployment"
        }]))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 1
        assert items[0]["mentions"] == 2
        assert items[0]["confidence"] == "confirmed"

    def test_similar_jaccard_match(self, life_dir: Path) -> None:
        """'QMD search works on life directory' vs 'QMD search works on the life directory' → reinforced (high Jaccard)."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "QMD search works on life directory", "confidence": "single", "mentions": 1, "date": TODAY, "last_seen": TODAY}
        ]))
        run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "QMD search works on the life directory", "category": "configuration"
        }]))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 1
        assert items[0]["mentions"] == 2

    def test_dissimilar_not_reinforced(self, life_dir: Path) -> None:
        """'Gateway healthy' vs 'Bot deployed' → NOT reinforced."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "Gateway healthy", "confidence": "single", "mentions": 1, "date": TODAY}
        ]))
        run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Bot deployed", "category": "deployment"
        }]))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 2  # Both kept

    def test_short_facts_not_falsely_matched(self, life_dir: Path) -> None:
        """'Bot up' vs 'Bot down' → NOT reinforced (Jaccard 0.5)."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "Bot up", "confidence": "single", "mentions": 1, "date": TODAY}
        ]))
        run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Bot down", "category": "event"
        }]))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 2

    def test_empty_fact_not_matched(self, life_dir: Path) -> None:
        """'' vs 'something' → NOT reinforced, no crash."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "", "confidence": "single", "mentions": 1, "date": TODAY}
        ]))
        r = run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "something", "category": "event"
        }]))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 2

    def test_stale_fact_restored_on_reinforce(self, life_dir: Path) -> None:
        """Stale fact re-mentioned → confidence becomes confirmed."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "Gateway on heavy node", "confidence": "stale", "mentions": 1, "date": "2026-03-01", "last_seen": "2026-03-01"}
        ]))
        run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Gateway on heavy node", "category": "deployment"
        }]))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 1
        assert items[0]["confidence"] == "confirmed"
        assert items[0]["last_seen"] == TODAY

    def test_archived_fact_restored_on_reinforce(self, life_dir: Path) -> None:
        """Archived fact re-mentioned → confidence becomes confirmed."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "Uses systemd timers", "confidence": "archived", "mentions": 1, "date": "2026-02-01", "last_seen": "2026-02-01"}
        ]))
        run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Uses systemd timers", "category": "configuration"
        }]))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 1
        assert items[0]["confidence"] == "confirmed"


class TestSuperseded:
    """Phase 4: Haiku-delegated contradiction detection via supersedes field."""

    def test_superseded_fact_marked(self, life_dir: Path) -> None:
        """Fact with supersedes → old fact gets superseded_by + superseded_date."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "Using memsearch for semantic search", "confidence": "single", "mentions": 1, "date": TODAY}
        ]))
        run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Using QMD for semantic search",
            "category": "decision",
            "supersedes": "Using memsearch for semantic search"
        }]))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        old = [i for i in items if "memsearch" in i["fact"]][0]
        assert old["confidence"] == "superseded"
        assert old["superseded_by"] == "Using QMD for semantic search"
        assert old["superseded_date"] == TODAY

    def test_superseded_confidence_set(self, life_dir: Path) -> None:
        """Old fact's confidence becomes superseded."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "Order size is $10", "confidence": "confirmed", "mentions": 3, "date": TODAY}
        ]))
        run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Order size reduced to $4",
            "category": "configuration",
            "supersedes": "Order size is $10"
        }]))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        old = [i for i in items if "$10" in i["fact"]][0]
        assert old["confidence"] == "superseded"

    def test_supersedes_missing_old_fact(self, life_dir: Path) -> None:
        """supersedes points to nonexistent fact → no crash, new fact still added."""
        (life_dir / "Projects/pi-cluster/items.json").write_text("[]")
        r = run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "New approach",
            "category": "decision",
            "supersedes": "Old approach that doesn't exist"
        }]))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert len(items) == 1
        assert items[0]["fact"] == "New approach"

    def test_supersedes_uses_fuzzy_match(self, life_dir: Path) -> None:
        """supersedes text slightly different from old fact → still matched via _similar()."""
        (life_dir / "Projects/pi-cluster/items.json").write_text(json.dumps([
            {"fact": "Gateway running on heavy node with nginx reverse proxy", "confidence": "single", "mentions": 1, "date": TODAY}
        ]))
        # New fact is completely different (won't trigger reinforce), but supersedes text is fuzzy-close to old
        run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Switched to Caddy as reverse proxy on heavy",
            "category": "configuration",
            "supersedes": "Gateway running on heavy node with nginx as reverse proxy"
        }]))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        old = [i for i in items if "nginx" in i["fact"]][0]
        assert old["confidence"] == "superseded"


class TestLastSeen:
    """Phase 3: last_seen field on new facts."""

    def test_new_fact_has_last_seen(self, life_dir: Path) -> None:
        """New facts get last_seen = TODAY."""
        run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "New fact with last_seen", "category": "event"
        }]))
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert items[0]["last_seen"] == TODAY


class TestInvalidInput:
    def test_invalid_json_exits_1(self, life_dir: Path) -> None:
        r = run_apply_raw(life_dir, b"this is not json at all")
        assert r.returncode == 1

    def test_empty_arrays_is_valid_noop(self, life_dir: Path) -> None:
        r = run_apply(life_dir, empty_payload(summary="Nothing to do today."))
        assert r.returncode == 0

    def test_dry_run_makes_no_changes(self, life_dir: Path) -> None:
        payload = empty_payload(
            new_entities=[{"type": "person", "name": "dry-test", "display": "Dry Test"}]
        )
        r = run_apply(life_dir, payload, extra_args=["--dry-run"])
        assert r.returncode == 0
        assert not (life_dir / "People/dry-test").exists()
        assert b"(dry)" in r.stdout


class TestEntitySlugNormalization:
    def test_normalizes_uppercase_entity_slug(self, life_dir: Path) -> None:
        """Fact update with 'Pi-Cluster' should resolve to 'pi-cluster'."""
        r = run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "Pi-Cluster",
            "date": TODAY, "fact": "Test uppercase slug", "category": "event",
        }]))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert any(i["fact"] == "Test uppercase slug" for i in items)

    def test_normalizes_underscore_entity_slug(self, life_dir: Path) -> None:
        """Fact update with 'pi_cluster' should resolve to 'pi-cluster'."""
        r = run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi_cluster",
            "date": TODAY, "fact": "Test underscore slug", "category": "event",
        }]))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert any(i["fact"] == "Test underscore slug" for i in items)

    def test_normalizes_space_in_entity_slug(self, life_dir: Path) -> None:
        """Fact update with 'pi cluster' should resolve to 'pi-cluster'."""
        r = run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi cluster",
            "date": TODAY, "fact": "Test space slug", "category": "event",
        }]))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert any(i["fact"] == "Test space slug" for i in items)

    def test_normalizes_new_entity_name(self, life_dir: Path) -> None:
        """New entity with 'Archie' as name creates 'archie' folder."""
        r = run_apply(life_dir, empty_payload(
            new_entities=[{"type": "person", "name": "Archie", "display": "Archie"}]
        ))
        assert r.returncode == 0
        assert (life_dir / "People/archie/summary.md").exists()


class TestTemporalField:
    def test_temporal_true_stored(self, life_dir: Path) -> None:
        """Fact with temporal: true stores the field."""
        r = run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Deploy in progress", "category": "deployment",
            "temporal": True,
        }]))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert items[0]["temporal"] is True

    def test_temporal_false_default(self, life_dir: Path) -> None:
        """Fact without temporal field defaults to false."""
        r = run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Cluster has 4 nodes", "category": "configuration",
        }]))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert items[0]["temporal"] is False

    def test_temporal_non_bool_coerced(self, life_dir: Path) -> None:
        """Non-bool temporal value is coerced to bool."""
        r = run_apply(life_dir, empty_payload(fact_updates=[{
            "entity_type": "project", "entity": "pi-cluster",
            "date": TODAY, "fact": "Blocked on approval", "category": "pending",
            "temporal": "yes",
        }]))
        assert r.returncode == 0
        items = json.loads((life_dir / "Projects/pi-cluster/items.json").read_text())
        assert items[0]["temporal"] is True
