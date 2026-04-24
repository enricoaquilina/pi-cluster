# OpenCode — ~/life/ Integration

## Memory Protocol

At session start:
1. Read ~/life/Areas/about-me/hard-rules.md — apply immediately
2. Read ~/life/Areas/about-me/profile.md — who the user is
3. Read ~/life/Areas/about-me/workflow-habits.md — calibrate behavior

## Writing

Use MCP write server tools with `platform="opencode"`:
- `append_daily_note(content, section, platform="opencode")`
- `create_entity(entity_type, slug, display_name, platform="opencode")`
- `add_fact(entity_slug, fact, category, platform="opencode")`

## Search

Use QMD MCP tools for searching ~/life/:
- `qmd query` — hybrid search (best quality)
- `qmd get` — retrieve document by path

## Session End

Log session summary to episodic log via MCP or direct call.
