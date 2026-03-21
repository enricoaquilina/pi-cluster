# Cluster Task Routing

When executing commands on remote nodes, use the Router API to select the best node.

## Router API (http://192.168.0.22:8520)

### Route a task (GET)
```bash
curl -s http://192.168.0.22:8520/route/coding
# Returns: {"task_type": "coding", "node": "build"}
```

Task types: `coding`, `research`, `compute`, `any`

### Dispatch a command (POST)
```bash
curl -s -X POST http://192.168.0.22:8520/dispatch \
  -H "Content-Type: application/json" \
  -d '{"task_type": "coding", "command": "git status", "cwd": "/opt/workspace"}'
```

### Check cluster health (GET)
```bash
curl -s http://192.168.0.22:8520/health
```

## Node Roles

| Node | Role | Hardware | Best for |
|------|------|----------|----------|
| build | coding | Pi 5, 4GB, ARM | Git ops, builds, code editing |
| light | research | Pi 4, 2GB, ARM | Web search, file reading, lightweight tasks |
| heavy | compute | NiPoGi, 16GB, x86_64 | Heavy processing, large codebases, multi-step tasks |

## Routing Rules
- Tasks are routed to the node matching the role, unless that node is overloaded (>85% RAM)
- If the preferred node is unavailable, falls back to the next best node
- `any` type routes to the least-loaded node (usually heavy)
