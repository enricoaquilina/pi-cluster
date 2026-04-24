# Claude Code Adapter

Status: **Active**

## Setup

1. Register MCP write server:
   ```bash
   claude mcp add life-write -s user -- python3 /home/enrico/pi-cluster/life-automation/mcp_write_server.py
   ```

2. Hooks (already configured):
   - `cc_start_hook.sh` — SessionStart: loads daily context + cross-platform summary
   - `cc_session_digest.py` — SessionEnd: logs session digest + episodic event

## Protocol

- Reads: ~/life/ directly (hard-rules, profile, daily notes, entities, QMD search)
- Writes: MCP tools (append_daily_note, create_entity, add_fact) with `platform="claude-code"`
- Episodic: auto-logged via hooks and MCP calls
