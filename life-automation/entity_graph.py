#!/usr/bin/env python3
"""
Entity relationship graph queries.
Usage:
  entity_graph.py connections <entity>     — all edges for entity
  entity_graph.py who-works-on <project>   — people working on project
  entity_graph.py what-does <entity> <rel> — targets of relation
  entity_graph.py full                     — full graph dump
  entity_graph.py stats                    — counts
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
RELATIONSHIPS_FILE = LIFE_DIR / "relationships.json"


def load_graph() -> list[dict]:
    if not RELATIONSHIPS_FILE.exists():
        return []
    try:
        return json.loads(RELATIONSHIPS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def query_connections(entity: str) -> list[dict]:
    return [e for e in load_graph() if e.get("from") == entity or e.get("to") == entity]


def query_by_relation(target: str, relation: str, direction: str = "to") -> list[dict]:
    graph = load_graph()
    return [e for e in graph if e.get(direction) == target and e.get("relation") == relation]


def graph_stats() -> dict:
    graph = load_graph()
    entities = set()
    for e in graph:
        entities.add(e.get("from", ""))
        entities.add(e.get("to", ""))
    entities.discard("")
    return {
        "entity_count": len(entities),
        "edge_count": len(graph),
        "relations": dict(Counter(e.get("relation", "") for e in graph)),
        "entities": sorted(entities),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: entity_graph.py <command> [args]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "full":
        graph = load_graph()
        json.dump({"edges": graph, "total": len(graph)}, sys.stdout, indent=2)
    elif cmd == "stats":
        json.dump(graph_stats(), sys.stdout, indent=2)
    elif cmd == "connections" and len(sys.argv) >= 3:
        entity = sys.argv[2]
        edges = query_connections(entity)
        json.dump({"edges": edges, "total": len(edges), "entity": entity}, sys.stdout, indent=2)
    elif cmd == "who-works-on" and len(sys.argv) >= 3:
        target = sys.argv[2]
        edges = query_by_relation(target, "works-on", direction="to")
        json.dump({"edges": edges, "total": len(edges), "target": target, "relation": "works-on"}, sys.stdout, indent=2)
    elif cmd == "what-does" and len(sys.argv) >= 4:
        entity = sys.argv[2]
        relation = sys.argv[3]
        edges = query_by_relation(entity, relation, direction="from")
        json.dump({"edges": edges, "total": len(edges), "entity": entity, "relation": relation}, sys.stdout, indent=2)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
