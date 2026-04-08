#!/usr/bin/env python3
"""Generate ~/life/graph.html — interactive D3.js knowledge graph.

Reads relationships.json and entity dirs, outputs a self-contained HTML file.

Usage:
    python3 generate_graph.py              # Generate graph.html
    python3 generate_graph.py --dry-run    # Print node/edge counts

Environment:
    LIFE_DIR  — path to ~/life/ (default: ~/life)
"""
import json
import os
import re
import sys
from pathlib import Path

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
DRY_RUN = "--dry-run" in sys.argv
ENTITY_TYPES = {"Projects": "project", "People": "person", "Companies": "company"}
TYPE_COLORS = {"project": "#50C878", "person": "#4A90D9", "company": "#F5A623"}


def _count_items(path: Path) -> int:
    try:
        return len(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return 0


def _get_status(path: Path) -> str:
    try:
        m = re.search(r"^status:\s*(\S+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        return m.group(1) if m else "unknown"
    except OSError:
        return "unknown"


def build_graph() -> dict:
    """Build {nodes: [...], edges: [...]} from ~/life."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    # Scan entity dirs
    for parent, etype in ENTITY_TYPES.items():
        parent_dir = LIFE_DIR / parent
        if not parent_dir.is_dir():
            continue
        for d in sorted(parent_dir.iterdir()):
            if not d.is_dir() or d.name.startswith(("_", ".")):
                continue
            nodes[d.name] = {
                "id": d.name,
                "type": etype,
                "label": d.name.replace("-", " ").title(),
                "status": _get_status(d / "summary.md"),
                "facts": _count_items(d / "items.json"),
                "has_dir": True,
            }

    # Load relationships
    rel_path = LIFE_DIR / "relationships.json"
    try:
        rels = json.loads(rel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        rels = []

    for edge in rels:
        src = edge.get("from", "")
        tgt = edge.get("to", "")
        rel = edge.get("relation", "")
        if not src or not tgt:
            continue

        # Add orphan nodes (in relationships but no dir)
        for slug, slug_type in [(src, edge.get("from_type", "")), (tgt, edge.get("to_type", ""))]:
            if slug not in nodes:
                nodes[slug] = {
                    "id": slug,
                    "type": slug_type,
                    "label": slug.replace("-", " ").title(),
                    "status": "orphan",
                    "facts": 0,
                    "has_dir": False,
                }

        edges.append({"source": src, "target": tgt, "relation": rel})

    return {"nodes": list(nodes.values()), "edges": edges}


def generate_html(graph: dict) -> str:
    """Generate self-contained HTML with D3.js force-directed graph."""
    graph_json = json.dumps(graph)
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>~/life Knowledge Graph</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
body {{ margin: 0; background: #0a0e17; font-family: system-ui; color: #e0e0e0; }}
svg {{ width: 100vw; height: 100vh; }}
.node circle {{ stroke: #333; stroke-width: 1.5px; cursor: pointer; }}
.node.orphan circle {{ stroke-dasharray: 4; opacity: 0.6; }}
.node text {{ font-size: 11px; fill: #ccc; pointer-events: none; }}
.edge line {{ stroke: #555; stroke-width: 1px; }}
.edge text {{ font-size: 9px; fill: #888; }}
.tooltip {{ position: absolute; background: #1a1f2e; border: 1px solid #333;
  padding: 8px 12px; border-radius: 6px; font-size: 12px; pointer-events: none; }}
h1 {{ position: absolute; top: 10px; left: 20px; font-size: 16px; color: #4f8ff7; }}
</style>
</head><body>
<h1>~/life Knowledge Graph</h1>
<div id="tooltip" class="tooltip" style="display:none"></div>
<svg></svg>
<script>
const data = {graph_json};
const colors = {json.dumps(TYPE_COLORS)};
const width = window.innerWidth, height = window.innerHeight;

const svg = d3.select("svg").attr("viewBox", [0, 0, width, height]);
const g = svg.append("g");
svg.call(d3.zoom().on("zoom", (e) => g.attr("transform", e.transform)));

const sim = d3.forceSimulation(data.nodes)
  .force("link", d3.forceLink(data.edges).id(d => d.id).distance(120))
  .force("charge", d3.forceManyBody().strength(-300))
  .force("center", d3.forceCenter(width/2, height/2));

const edge = g.selectAll(".edge").data(data.edges).join("g").attr("class","edge");
edge.append("line");
edge.append("text").text(d => d.relation).attr("text-anchor","middle");

const node = g.selectAll(".node").data(data.nodes).join("g")
  .attr("class", d => "node" + (d.has_dir ? "" : " orphan"))
  .call(d3.drag().on("start",(e,d)=>{{sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y}})
    .on("drag",(e,d)=>{{d.fx=e.x;d.fy=e.y}}).on("end",(e,d)=>{{sim.alphaTarget(0);d.fx=null;d.fy=null}}));

node.append("circle").attr("r", d => 8 + Math.sqrt(d.facts))
  .attr("fill", d => colors[d.type] || "#999");
node.append("text").text(d => d.label).attr("dx", 14).attr("dy", 4);

const tip = d3.select("#tooltip");
node.on("mouseover", (e,d) => {{
  tip.style("display","block").html(
    `<b>${{d.label}}</b><br>Type: ${{d.type}}<br>Status: ${{d.status}}<br>Facts: ${{d.facts}}`
  ).style("left",(e.pageX+12)+"px").style("top",(e.pageY-12)+"px");
}}).on("mouseout", () => tip.style("display","none"));

sim.on("tick", () => {{
  edge.select("line").attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
    .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
  edge.select("text").attr("x",d=>(d.source.x+d.target.x)/2).attr("y",d=>(d.source.y+d.target.y)/2);
  node.attr("transform",d=>`translate(${{d.x}},${{d.y}})`);
}});
</script></body></html>"""


def main() -> None:
    graph = build_graph()

    if DRY_RUN:
        print(f"[graph] {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")
        for n in graph["nodes"]:
            flag = " (orphan)" if not n["has_dir"] else ""
            print(f"  [{n['type']}] {n['id']}{flag} — {n['facts']} facts")
        return

    html = generate_html(graph)
    out_path = LIFE_DIR / "graph.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[graph] Generated {out_path} ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")


if __name__ == "__main__":
    main()
