#!/usr/bin/env python3
"""
Autonomous heartbeat agent. Runs every 30 minutes via cron.
Checks MC tasks, node resources, daily note heartbeat.
Auto-dispatches clear tasks; asks Enrico for unclear ones.

Channel split:
  Telegram: operational updates, task completions, daily digest
  WhatsApp: human decisions needed, ambiguous tasks, budget alerts
"""
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

LIFE_DIR = Path(os.environ.get("LIFE_DIR", Path.home() / "life"))
TODAY = str(date.today())
DRY_RUN = "--dry-run" in sys.argv
JSON_MODE = "--json" in sys.argv


def log(msg: str) -> None:
    """Print to stderr in JSON mode (keep stdout clean), stdout otherwise."""
    print(msg, file=sys.stderr if JSON_MODE else sys.stdout)


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown v1 metacharacters."""
    for ch in r"\_*`[":
        text = text.replace(ch, f"\\{ch}")
    return text

# MC API config
MC_API = os.environ.get("MC_API_URL", "http://localhost:3000/api")
MC_API_KEY = os.environ.get("MC_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Budget guardrails
DAILY_BUDGET_USD = float(os.environ.get("AGENT_DAILY_BUDGET", "5.00"))
SPEND_LOG = LIFE_DIR / "logs" / "agent-spend.json"

# Telegram config
TELEGRAM_TOKEN = ""
TELEGRAM_CHAT_ID = ""
_tg_token_path = Path.home() / ".telegram-bot-token"
_tg_chat_path = Path.home() / ".telegram-chat-id"
if _tg_token_path.exists():
    TELEGRAM_TOKEN = _tg_token_path.read_text().strip()
if _tg_chat_path.exists():
    TELEGRAM_CHAT_ID = _tg_chat_path.read_text().strip()

# Persona routing for task dispatch
PERSONA_MAP = {
    "backend": "Archie",
    "frontend": "Pixel",
    "devops": "Harbor",
    "security": "Sentinel",
    "docs": "Docsworth",
    "research": "Scout",
    "data": "Ledger",
}


def _get_cc_session_context() -> str:
    """Read today's Claude Code session digests for dispatch context."""
    parts = TODAY.split("-")
    digest_path = LIFE_DIR / "Daily" / parts[0] / parts[1] / f"sessions-digest-{TODAY}.jsonl"
    if not digest_path.exists():
        return ""
    lines = []
    try:
        for raw in digest_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
                lines.append(f"- {d.get('ts', '')[:16]}: {d.get('summary', '')}")
            except (json.JSONDecodeError, KeyError):
                continue
    except OSError:
        return ""
    if not lines:
        return ""
    return "Recent Claude Code sessions:\n" + "\n".join(lines[-5:])


def mc_get(endpoint: str) -> dict | list | None:
    """GET from Mission Control API."""
    try:
        req = Request(
            f"{MC_API}{endpoint}",
            headers={"X-Api-Key": MC_API_KEY},
        )
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError, OSError) as e:
        print(f"[agent] MC API error ({endpoint}): {e}", file=sys.stderr)
        return None


def mc_post(endpoint: str, data: dict) -> dict | None:
    """POST to Mission Control API."""
    try:
        body = json.dumps(data).encode()
        req = Request(
            f"{MC_API}{endpoint}",
            data=body,
            headers={
                "X-Api-Key": MC_API_KEY,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        resp = urlopen(req, timeout=60)
        return json.loads(resp.read().decode())
    except (URLError, json.JSONDecodeError, OSError) as e:
        print(f"[agent] MC POST error ({endpoint}): {e}", file=sys.stderr)
        return None


def send_telegram(message: str) -> bool:
    """Send message via Telegram bot."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log(f"[agent] Telegram not configured, would send: {message[:100]}")
        return False
    if DRY_RUN:
        log(f"[agent] (dry) Telegram: {message[:100]}")
        return True
    try:
        message = _escape_md(message)
        data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}).encode()
        req = Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=10)
        return True
    except (URLError, OSError) as e:
        print(f"[agent] Telegram send failed: {e}", file=sys.stderr)
        return False


# ── Confidence Scoring ───────────────────────────────────────────────────────

# Keywords that indicate clear, actionable tasks
CLEAR_VERBS = {"fix", "update", "add", "remove", "delete", "create", "install",
               "upgrade", "migrate", "deploy", "restart", "configure", "enable",
               "disable", "rename", "refactor", "implement", "write", "build"}

# Keywords that indicate vague tasks needing human input
VAGUE_WORDS = {"think", "consider", "explore", "investigate", "brainstorm",
               "plan", "design", "decide", "evaluate", "assess", "review",
               "discuss", "research", "analyze", "improve", "optimize"}

# Keywords that indicate high blast radius (always ask)
RISKY_WORDS = {"production", "delete all", "drop table", "force push",
               "send to customer", "deploy to master", "merge to main",
               "rm -rf", "reset --hard"}


def score_confidence(title: str, description: str = "") -> tuple[str, str]:
    """Score task confidence using rule-based heuristics.
    Returns (level, reason) where level is 'high', 'medium', or 'low'.
    """
    text = f"{title} {description}".lower()

    # Layer 3: Blast radius gate — always ask
    for risky in RISKY_WORDS:
        if risky in text:
            return "low", f"risky action detected: '{risky}'"

    # Layer 1: Rule-based scoring
    first_word = title.strip().lower().split()[0] if title.strip() else ""

    if first_word in CLEAR_VERBS:
        return "high", f"clear action verb: '{first_word}'"

    for vague in VAGUE_WORDS:
        if vague in text:
            return "low", f"vague/exploratory: contains '{vague}'"

    # Default: medium — would use LLM classifier in production
    return "medium", "no strong signal — defaulting to medium"


def select_persona(title: str, description: str = "") -> str:
    """Select best persona for a task based on keywords."""
    text = f"{title} {description}".lower()

    if any(w in text for w in ["docker", "deploy", "ci", "cron", "ansible", "infra"]):
        return "Harbor"
    if any(w in text for w in ["security", "firewall", "ufw", "audit", "vulnerability"]):
        return "Sentinel"
    if any(w in text for w in ["frontend", "ui", "css", "html", "react"]):
        return "Pixel"
    if any(w in text for w in ["docs", "readme", "documentation", "guide"]):
        return "Docsworth"
    if any(w in text for w in ["research", "compare", "benchmark", "evaluate"]):
        return "Scout"
    if any(w in text for w in ["data", "metrics", "dashboard", "analytics"]):
        return "Ledger"
    # Default to Archie (backend)
    return "Archie"


# ── Resource Checking ────────────────────────────────────────────────────────


def check_node_resources() -> dict[str, dict]:
    """Get node resource status from MC. Returns {node_name: {ram_pct, cpu_pct, status}}."""
    nodes_data = mc_get("/nodes")
    if not nodes_data:
        return {}
    nodes = nodes_data if isinstance(nodes_data, list) else nodes_data.get("nodes", [])
    result = {}
    for node in nodes:
        name = node.get("name", "")
        ram_total = node.get("ram_total_mb", 1)
        ram_used = node.get("ram_used_mb", 0)
        result[name] = {
            "ram_pct": round(ram_used / max(ram_total, 1) * 100),
            "cpu_pct": node.get("cpu_percent", 0),
            "status": node.get("status", "unknown"),
            "dispatchable": node.get("status") in ("healthy", "online") and (ram_used / max(ram_total, 1)) < 0.8,
        }
    return result


def find_available_node(preferred: str, nodes: dict) -> str | None:
    """Find an available node, preferring the given one."""
    if preferred in nodes and nodes[preferred].get("dispatchable"):
        return preferred
    # Try fallback
    fallback = {"slave0": "slave1", "slave1": "slave0"}
    alt = fallback.get(preferred)
    if alt and alt in nodes and nodes[alt].get("dispatchable"):
        return alt
    return None


# ── Budget Tracking ──────────────────────────────────────────────────────────


def get_today_spend() -> float:
    """Get total spend for today from spend log."""
    if not SPEND_LOG.exists():
        return 0.0
    try:
        data = json.loads(SPEND_LOG.read_text(encoding="utf-8"))
        return sum(e.get("cost_usd", 0) for e in data if e.get("date") == TODAY)
    except (json.JSONDecodeError, OSError):
        return 0.0


def log_spend(task: str, cost_usd: float) -> None:
    """Log a spend entry."""
    SPEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if SPEND_LOG.exists():
        try:
            entries = json.loads(SPEND_LOG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []
    entries.append({
        "date": TODAY,
        "timestamp": datetime.now().isoformat(),
        "task": task[:200],
        "cost_usd": cost_usd,
    })
    # Keep last 30 days
    cutoff = str(date.today().replace(day=1))  # rough 30-day cutoff
    entries = [e for e in entries if e.get("date", "") >= cutoff]
    SPEND_LOG.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def budget_ok(estimated_cost: float = 0.05) -> bool:
    """Check if we're within daily budget."""
    spent = get_today_spend()
    return (spent + estimated_cost) <= DAILY_BUDGET_USD


# ── Planning Gate (Ralph PRD) ─────────────────────────────────────────────────

# Tasks that are simple enough to dispatch without a PRD
SIMPLE_TASK_VERBS = {"fix", "update", "restart", "enable", "disable", "rename",
                     "delete", "remove", "upgrade", "rollback", "revert"}


def _task_slug(title: str) -> str:
    """Convert task title to a filesystem-safe slug."""
    return re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')


def _prd_path(title: str) -> Path:
    """Return the PRD file path for a given task title."""
    return LIFE_DIR / "Projects" / _task_slug(title) / "prd.md"


def _is_simple_task(title: str) -> bool:
    """Return True if the task starts with a simple operational verb."""
    first_word = title.strip().lower().split()[0] if title.strip() else ""
    return first_word in SIMPLE_TASK_VERBS


def _task_prd_status(title: str) -> str:
    """Single decision point for the planning gate.
    Returns: 'simple' | 'needs_generation' | 'awaiting_approval' | 'approved'."""
    if _is_simple_task(title):
        return "simple"
    try:
        content = _prd_path(title).read_text(encoding="utf-8")
    except OSError:
        return "needs_generation"
    if "approved: true" in content:
        return "approved"
    return "awaiting_approval"


def _load_prd_context(title: str) -> str:
    """Load PRD content if it exists for this task (up to 3000 chars)."""
    try:
        return _prd_path(title).read_text(encoding="utf-8")[:3000]
    except OSError:
        return ""


def _call_openrouter(prompt: str, timeout: int = 60) -> str | None:
    """Direct OpenRouter call for agent-internal tasks (PRD generation etc.)."""
    if not OPENROUTER_API_KEY:
        print("[agent] OPENROUTER_API_KEY not set — cannot generate PRD", file=sys.stderr)
        return None
    body = json.dumps({
        "model": "google/gemini-flash-1.5-8b",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2000,
    }).encode()
    req = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]
    except (URLError, OSError, KeyError, json.JSONDecodeError) as e:
        print(f"[agent] OpenRouter error: {e}", file=sys.stderr)
        return None


def _generate_prd(title: str, description: str) -> str:
    """Generate a draft PRD for a complex task and send for Enrico's approval.
    Returns the action taken ('prd_generated', 'dry_prd_needed', 'budget_exceeded',
    'prd_generation_failed')."""
    slug = _task_slug(title)
    prd_dir = LIFE_DIR / "Projects" / slug

    if DRY_RUN:
        log(f"[agent] (dry) Would generate PRD for: {title}")
        return "dry_prd_needed"

    if not budget_ok():
        return "budget_exceeded"

    prd_prompt = (
        f"You are a product manager. Generate a concise PRD for this task.\n\n"
        f"Task: {title}\n"
        f"Additional context: {description or 'None provided'}\n\n"
        "Generate a PRD with these sections:\n"
        "## Introduction\nBrief description of what this is and why.\n\n"
        "## Goals\n3-5 specific, measurable objectives.\n\n"
        "## User Stories\n3-8 stories, each:\n"
        "### US-001: Title\n"
        "**Description:** As a [user], I want [feature] so that [benefit].\n"
        "**Acceptance Criteria:**\n- [ ] Specific verifiable criterion\n\n"
        "## Functional Requirements\nNumbered list (FR-1, FR-2...).\n\n"
        "## Non-Goals\nWhat this will NOT include.\n\n"
        "## Technical Considerations\nKnown constraints, tech stack, integration points.\n\n"
        "Keep it practical. Each user story should be completable in one coding session."
    )

    prd_body = _call_openrouter(prd_prompt)
    if not prd_body:
        send_telegram(f"❌ *PRD generation failed* for: {title}")
        return "prd_generation_failed"

    prd_content = f"---\napproved: false\n---\n\n# PRD: {title}\n\n{prd_body}"

    prd_dir.mkdir(parents=True, exist_ok=True)
    _prd_path(title).write_text(prd_content, encoding="utf-8")
    items_path = prd_dir / "items.json"
    if not items_path.exists():
        items_path.write_text("[]", encoding="utf-8")
    summary_path = prd_dir / "summary.md"
    if not summary_path.exists():
        summary_path.write_text(
            f"---\ntype: project\nname: {title}\ncreated: {TODAY}\n"
            f"last-updated: {TODAY}\nstatus: active\n---\n\n"
            f"## What This Is\n{description or title}\n",
            encoding="utf-8",
        )

    log_spend(f"PRD: {title}", 0.005)

    msg = (
        f"📋 *PRD Generated for: {title}*\n\n"
        f"_{prd_content[:400]}_...\n\n"
        f"Saved to: `~/life/Projects/{slug}/prd.md`\n\n"
        f"Add `approved: true` to the frontmatter to let me start coding."
    )
    send_telegram(msg)
    return "prd_generated"


# ── Main Agent Loop ──────────────────────────────────────────────────────────


def collect_actionable_items() -> list[dict]:
    """Gather all items that need attention."""
    items = []

    # 1. MC tasks in "todo" status with priority >= medium
    ACTIONABLE_PRIORITIES = {"medium", "high", "urgent"}
    tasks_data = mc_get("/tasks?status=todo&limit=20")
    if tasks_data:
        task_list = tasks_data if isinstance(tasks_data, list) else tasks_data.get("items", tasks_data.get("tasks", []))
        for task in task_list:
            priority = task.get("priority", "medium")
            if priority not in ACTIONABLE_PRIORITIES:
                continue
            items.append({
                "source": "mc_task",
                "id": task.get("id"),
                "title": task.get("title", ""),
                "description": task.get("description", ""),
                "priority": priority,
            })

    # 2. Heartbeat — blocked/stale projects
    heartbeat = mc_get("/life/heartbeat")
    if heartbeat:
        for proj in heartbeat.get("projects", []):
            if proj.get("needs_attention"):
                items.append({
                    "source": "heartbeat",
                    "id": proj.get("slug"),
                    "title": f"Project needs attention: {proj['slug']}",
                    "description": proj.get("attention_reason", ""),
                    "priority": "high" if proj.get("blocked_days", 0) >= 3 else "medium",
                })

    return items


def process_item(item: dict, nodes: dict) -> dict:
    """Process a single actionable item. Returns action taken."""
    title = item.get("title", "")
    desc = item.get("description", "")
    source = item.get("source", "")

    confidence, reason = score_confidence(title, desc)
    persona = select_persona(title, desc)

    result = {
        "title": title,
        "source": source,
        "confidence": confidence,
        "confidence_reason": reason,
        "persona": persona,
        "action": "none",
    }

    if confidence == "low":
        # Ask Enrico via Telegram (using Telegram for now, WhatsApp integration would need OpenClaw)
        msg = f"🤔 *Need your input:*\n\n*{title}*\n{desc}\n\n_Reason: {reason}_\n\nReply with instructions or 'skip'."
        send_telegram(msg)
        result["action"] = "asked_human"
        return result

    if confidence in ("high", "medium"):
        if not budget_ok():
            msg = f"💰 *Budget limit reached* (${DAILY_BUDGET_USD}/day)\n\nSkipping: {title}"
            send_telegram(msg)
            result["action"] = "budget_exceeded"
            return result

        # ── Planning Gate: single decision point for PRD status ──
        prd_status = _task_prd_status(title)

        if prd_status == "needs_generation":
            result["action"] = _generate_prd(title, desc)
            return result

        if prd_status == "awaiting_approval":
            slug = _task_slug(title)
            send_telegram(
                f"⏳ *Awaiting PRD approval:* {title}\n\n"
                f"Edit `~/life/Projects/{slug}/prd.md` and set `approved: true` to proceed."
            )
            result["action"] = "awaiting_approval"
            return result

        if DRY_RUN:
            log(f"[agent] (dry) Would dispatch to {persona}: {title}")
            result["action"] = "dry_dispatch"
            return result

        # Dispatch to persona with PRD context + CC session context
        prd_context = _load_prd_context(title)
        cc_context = _get_cc_session_context()
        prompt_parts = [f"Task: {title}"]
        if desc:
            prompt_parts.append(f"Context: {desc}")
        if cc_context:
            prompt_parts.append(cc_context)
        if prd_context:
            prompt_parts.append(f"PRD:\n{prd_context}")
        prompt_parts.append("Please complete this task. Create a GitHub PR if code changes are involved.")

        dispatch_result = mc_post("/dispatch", {
            "persona": persona,
            "prompt": "\n\n".join(prompt_parts),
            "timeout": 120,
        })

        if dispatch_result and dispatch_result.get("response"):
            log_spend(title, 0.05)
            response_preview = dispatch_result.get("response", "")[:200]
            msg = f"✅ *Dispatched to {persona}*\n\n*{title}*\n\n_{response_preview}_"
            send_telegram(msg)
            result["action"] = "dispatched"
            result["response_preview"] = response_preview
        else:
            msg = f"❌ *Dispatch failed* for {persona}\n\n{title}"
            send_telegram(msg)
            result["action"] = "dispatch_failed"

    return result


def run_agent() -> dict:
    """Main agent entry point."""
    log(f"[agent] Starting heartbeat check at {datetime.now().isoformat()}")

    # Check node resources
    nodes = check_node_resources()
    if not nodes:
        log("[agent] WARNING: Could not reach MC nodes API")

    # Collect actionable items
    items = collect_actionable_items()
    if not items:
        log("[agent] No actionable items found")
        return {"timestamp": datetime.now().isoformat(), "items_checked": 0, "actions": []}

    log(f"[agent] Found {len(items)} actionable items")

    # Process each item
    actions = []
    for item in items:
        result = process_item(item, nodes)
        actions.append(result)
        log(f"[agent] {result['action'].upper()}: {result['title'][:80]}")

    # Log results
    log_path = LIFE_DIR / "logs" / "agent-runs.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_entries = []
    if log_path.exists():
        try:
            log_entries = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log_entries = []
    log_entries.append({
        "timestamp": datetime.now().isoformat(),
        "items_checked": len(items),
        "actions": actions,
        "nodes": nodes,
        "budget_spent_today": get_today_spend(),
    })
    # Keep last 200 entries
    if len(log_entries) > 200:
        log_entries = log_entries[-200:]
    log_path.write_text(json.dumps(log_entries, indent=2), encoding="utf-8")

    return {
        "timestamp": datetime.now().isoformat(),
        "items_checked": len(items),
        "actions": actions,
    }


if __name__ == "__main__":
    result = run_agent()
    if "--json" in sys.argv:
        json.dump(result, sys.stdout, indent=2)
