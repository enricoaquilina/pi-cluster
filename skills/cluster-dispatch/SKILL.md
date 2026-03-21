---
name: cluster-dispatch
description: Route and execute tasks on the Pi cluster nodes based on workload type and node health.
version: 1.0.0
metadata:
  openclaw:
    emoji: "🖧"
    os: ["linux"]
    always: true
---

## Cluster Task Dispatch

You have access to cluster dispatch tools for routing work to the best-fit node in Enrico's Pi cluster.

### Available Tools

- **cluster_route** — Select the best node for a task type without executing anything
- **cluster_dispatch** — Execute a command on the best node for a task type
- **cluster_health** — Get real-time health stats for all cluster nodes

### Node Capabilities

| Node | Hardware | Role | Best For |
|------|----------|------|----------|
| **build** | Pi 5, 4GB RAM, ARM | coding | Git, builds, code editing, npm |
| **light** | Pi 4, 2GB RAM, ARM | research | Web search, reading, lightweight tasks |
| **heavy** | NiPoGi, 16GB RAM, x86_64, 8 cores | compute | Large codebases, multi-step tasks, Python/Node heavy processing |

### Routing Rules

- Use `task_type: "coding"` for git operations, code changes, builds
- Use `task_type: "research"` for web searches, file reading, quick lookups
- Use `task_type: "compute"` for heavy processing, data analysis, large repos
- Use `task_type: "any"` to let the router pick the least-loaded node
- If a node is overloaded (>85% RAM), the router automatically picks the next best node
