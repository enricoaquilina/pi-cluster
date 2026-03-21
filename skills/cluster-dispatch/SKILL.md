---
name: cluster-dispatch
description: Route and execute tasks on the Pi cluster nodes with intelligent model selection.
version: 2.0.0
metadata:
  openclaw:
    emoji: "🖧"
    os: ["linux"]
    always: true
---

## Cluster Task Dispatch

You have access to cluster dispatch tools for routing work to the best-fit node and AI model.

### Available Tools

- **cluster_route** — Select the best node + recommended model for a task type
- **cluster_dispatch** — Execute a command on the best node for a task type
- **cluster_health** — Get real-time health stats for all cluster nodes
- **cluster_model** — Get the recommended AI model and fallbacks for a task type

### Node Capabilities

| Node | Hardware | Role | Best For |
|------|----------|------|----------|
| **build** | Pi 5, 4GB RAM, ARM | coding | Git, builds, code editing |
| **light** | Pi 4, 2GB RAM, ARM | research | Web search, reading, lightweight tasks |
| **heavy** | NiPoGi, 16GB RAM, x86_64, 8 cores | compute | Large codebases, multi-step tasks, heavy processing |

### Model Allocation

| Task Type | Primary Model | Fallbacks | Cost Tier |
|-----------|--------------|-----------|-----------|
| **coding** | Claude Sonnet 4.6 | GLM-5, MiniMax M2.7 | Standard ($3/$15) |
| **research** | Qwen 3.5 Plus | MiniMax M2.7, Gemini Flash Lite | Economy ($0.26/$1.56) |
| **compute** | Claude Opus 4.6 | GPT-5.4, GLM-5 | Premium ($5/$25) |
| **any** | MiniMax M2.7 | Gemini Flash, DeepSeek V3.2 | Value ($0.30/$1.20) |

### Routing Rules
- Use `task_type: "coding"` for git operations, code changes, builds
- Use `task_type: "research"` for web searches, file reading, quick lookups
- Use `task_type: "compute"` for heavy processing, data analysis, complex reasoning
- Use `task_type: "any"` to let the router pick the least-loaded node + best value model
