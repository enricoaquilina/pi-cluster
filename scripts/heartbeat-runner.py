#!/usr/bin/env python3
"""Structured heartbeat runner — executes all checks, validates results,
outputs a verified report. Prevents hallucination by producing output
only from real command results."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE_URL = os.environ.get("MISSION_CONTROL_URL", "http://192.168.0.22:3000/api")
API_KEY = os.environ.get("MISSION_CONTROL_API_KEY", "")
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/node/.openclaw/workspace"))
POLYBOT_DATA = Path(os.environ.get("POLYBOT_DATA_DIR", str(WORKSPACE / "polybot-data")))
MEMORY_DIR = WORKSPACE / "memory"
TASK_STATE_FILE = MEMORY_DIR / "task-execution.json"
HEARTBEAT_STATE_FILE = MEMORY_DIR / "heartbeat-state.json"
CRON_HEALTH_FILE = MEMORY_DIR / "cron-health.json"
TIMEOUT = 10

# --- PRD gate ---
SIMPLE_VERBS = {"fix", "update", "check", "verify", "bump", "rename", "remove", "delete", "revert", "typo"}
SIMPLE_MAX_WORDS = 8
PRD_REMINDER_HOURS = 24

# --- Dispatch quality gate ---
REFUSAL_PATTERNS = [
    "i cannot", "i can't", "i'm unable", "i am unable",
    "as an ai", "i don't have access", "i'm not able",
    "sorry, but i", "i apologize, but",
]
MIN_RESPONSE_LENGTH = 50
MAX_CONSECUTIVE_FAILURES = 3

# --- Persona routing rules (ordered by specificity) ---
PERSONA_ROUTES = [
    (["frontend", "ui", "css", "react", "component", "layout"], "Pixel"),
    (["docker", "deploy", "ci", "infra", "k8s", "nginx", "caddy"], "Harbor"),
    (["security", "auth", "ssl", "cert", "vuln", "audit"], "Sentinel"),
    (["blog", "write", "copy", "content", "article"], "Quill"),
    (["design", "logo", "brand", "wireframe", "mockup"], "Canvas"),
    (["research", "analyze", "report", "investigate"], "Scout"),
]


def api_request(method, path, data=None, params=None):
    """Make a request to Mission Control API."""
    from urllib.parse import urlencode
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urlencode(params)
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, method=method)
    req.add_header("X-Api-Key", API_KEY)
    req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def run_check(name, fn):
    """Run a check function, capture result or error."""
    try:
        result = fn()
        return {"status": "ok", "data": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def send_telegram(text):
    """Send message to Telegram. Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("WARN: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"WARN: Telegram send failed: {e}", file=sys.stderr)
        return False


def _tg_api(method, payload, timeout=15):
    """Call Telegram Bot API. Returns parsed response or None on error."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    data = json.dumps(payload).encode()
    req = Request(f"https://api.telegram.org/bot{token}/{method}",
                  data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"WARN: Telegram {method}: {e}", file=sys.stderr)
        return None


def _tg_send(text, parse_mode="HTML", reply_markup=None):
    """Send Telegram message with optional inline keyboard. Returns message_id or None."""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        return None
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    result = _tg_api("sendMessage", payload)
    if result and result.get("ok"):
        return result["result"]["message_id"]
    return None


def _is_simple_task(title):
    """Simple tasks (quick verbs, short titles) skip PRD and dispatch directly."""
    words = title.lower().split()
    if not words:
        return True
    return words[0] in SIMPLE_VERBS and len(words) <= SIMPLE_MAX_WORDS


def _task_slug(title):
    """Generate a URL-safe slug from task title, capped at 40 chars."""
    import re
    return re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40]


def _task_prd_status(slug):
    """Fetch PRD from MC API. Returns dict or None if not found."""
    try:
        return api_request("GET", f"/prd/{slug}")
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


def _prd_recently_notified(slug, task_state):
    """Check if we sent a PRD reminder for this slug within the cooldown window."""
    notified = task_state.get("prdNotified", {})
    last = notified.get(slug)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
        return (datetime.now(timezone.utc) - last_dt).total_seconds() < PRD_REMINDER_HOURS * 3600
    except (ValueError, TypeError):
        return False


def _mark_prd_notified(slug, task_state):
    """Record that we sent a PRD notification for this slug."""
    if "prdNotified" not in task_state:
        task_state["prdNotified"] = {}
    task_state["prdNotified"][slug] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(TASK_STATE_FILE, task_state)


def _generate_prd(title, description, slug, feedback=None):
    """Generate a PRD via OpenRouter, create in MC, send Telegram keyboard.
    Returns (slug, message_id) or (slug, None) on Telegram failure."""
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        print("WARN: OPENROUTER_API_KEY not set, skipping PRD generation", file=sys.stderr)
        return slug, None

    feedback_section = f"\nPrevious feedback to address:\n{feedback}" if feedback else ""
    user_prompt = (
        f"Generate a concise PRD for: {title}\n"
        f"Description: {description}\n"
        f"{feedback_section}\n\n"
        "Sections: Goal (1 paragraph), Requirements (5-10 numbered), "
        "Technical Approach (bullets), Acceptance Criteria (checkboxes), "
        "Out of Scope. Under 150 lines."
    )

    or_url = "https://openrouter.ai/api/v1/chat/completions"
    or_data = json.dumps({
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": "You are a technical product manager. Write concise, actionable PRDs."},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 4000,
    }).encode()
    or_req = Request(or_url, data=or_data, method="POST")
    or_req.add_header("Authorization", f"Bearer {or_key}")
    or_req.add_header("Content-Type", "application/json")

    try:
        with urlopen(or_req, timeout=60) as resp:
            or_result = json.loads(resp.read().decode())
        content = or_result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"WARN: PRD generation failed: {e}", file=sys.stderr)
        content = f"# PRD: {title}\n\n(Auto-generation failed: {e})\n\nPlease write manually."

    # Create/upsert PRD in MC
    try:
        api_request("POST", "/prd", data={
            "slug": slug,
            "title": title,
            "content": content,
            "model": "google/gemini-2.5-flash",
        })
    except Exception as e:
        print(f"WARN: PRD create failed: {e}", file=sys.stderr)
        return slug, None

    # Send Telegram with inline keyboard
    msg = f"<b>PRD: {title}</b>\n\n{content[:500]}{'...' if len(content) > 500 else ''}"
    keyboard = {
        "inline_keyboard": [[
            {"text": "Approve", "callback_data": f"prd:approve:{slug}"},
            {"text": "Reject", "callback_data": f"prd:reject:{slug}"},
        ]]
    }
    message_id = _tg_send(msg, reply_markup=keyboard)

    # Store message_id on the PRD
    if message_id:
        try:
            api_request("PATCH", f"/prd/{slug}", data={"telegram_message_id": message_id})
        except Exception:
            pass

    return slug, message_id


def _load_prd_context(slug):
    """Fetch approved PRD content, truncate for dispatch prompt."""
    prd = _task_prd_status(slug)
    if not prd or prd.get("status") != "approved":
        return ""
    content = prd.get("content", "")
    if len(content) > 2000:
        content = content[:2000] + f"\n\n[Truncated — full PRD: GET /api/prd/{slug}]"
    return content


def select_persona(task):
    """Select best persona based on task title, tags, and project."""
    searchable = task.get("title", "").lower()
    for tag in task.get("tags", []):
        searchable += f" {tag.lower()}"
    if task.get("project"):
        searchable += f" {task['project'].lower()}"

    for keywords, persona in PERSONA_ROUTES:
        if any(kw in searchable for kw in keywords):
            return persona
    return "Archie"  # Default: backend engineering


def score_task(task):
    """Score a task for dispatch priority. Higher = more urgent."""
    priority_scores = {"urgent": 100, "high": 50, "medium": 20, "low": 5}
    score = priority_scores.get(task.get("priority", "medium"), 20)

    # Age bonus: +1 point per hour the task has been pending (max +48)
    created = task.get("created_at", "")
    if created:
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600
            score += min(age_hours, 48)
        except (ValueError, TypeError):
            pass

    # Due date urgency: +50 if due within 24h, +25 if due within 48h
    due = task.get("due_date", "")
    if due:
        try:
            due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
            hours_until = (due_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_until < 24:
                score += 50
            elif hours_until < 48:
                score += 25
        except (ValueError, TypeError):
            pass

    return score


def assess_quality(response_text):
    """Check if dispatch response is meaningful. Returns (quality, reason)."""
    if not response_text or not response_text.strip():
        return "empty", "No response text"
    text = response_text.strip()
    if len(text) < MIN_RESPONSE_LENGTH:
        return "too_short", f"Response only {len(text)} chars"
    lower = text.lower()
    for pattern in REFUSAL_PATTERNS:
        if pattern in lower:
            return "refused", "Contains refusal pattern"
    return "ok", None


def check_nodes():
    """Check cluster node health via MC API."""
    nodes = api_request("GET", "/nodes")
    results = []
    for n in nodes:
        meta = n.get("metadata", {})
        ram = meta.get("ram_pct", n.get("ram_pct"))
        temp = meta.get("temp_c", n.get("cpu_temp"))
        status = n.get("status", "unknown")
        last_hb = n.get("last_heartbeat", "")

        alert = None
        if status != "healthy":
            alert = f"{n['name']} is {status}"
        elif ram is not None and float(ram) > 85:
            alert = f"{n['name']} RAM {ram}%"

        results.append({
            "name": n.get("name", "?"),
            "status": status,
            "ram_pct": ram,
            "temp_c": temp,
            "last_heartbeat": last_hb,
            "alert": alert,
        })
    return results


def check_polybot():
    """Check polybot status by reading data files directly."""
    data_dir = POLYBOT_DATA
    control = {}
    control_path = data_dir / "control.json"
    if control_path.exists():
        control = json.loads(control_path.read_text())

    positions = []
    positions_path = data_dir / "positions.json"
    if positions_path.exists():
        positions = json.loads(positions_path.read_text())

    open_positions = [p for p in positions if p.get("status") == "open"]

    result = {
        "mode": control.get("mode", "unknown"),
        "order_size_usd": control.get("order_size_usd"),
        "daily_budget_usd": control.get("daily_budget_usd"),
        "daily_spent_usd": control.get("daily_spent_usd", 0),
        "stop_loss_balance_usd": control.get("stop_loss_balance_usd"),
        "open_positions": len(open_positions),
        "enabled_traders": control.get("enabled_traders", []),
    }

    # Alert conditions
    budget = control.get("daily_budget_usd", 500)
    spent = control.get("daily_spent_usd", 0)
    result["alert"] = None
    if budget > 0 and spent > budget * 0.8:
        result["alert"] = f"Daily spend {spent:.0f}/{budget:.0f} USD (>{80}%)"
    if control.get("circuit_breaker_active"):
        result["alert"] = "Circuit breaker ACTIVE"

    return result


def check_tasks():
    """Check task board: read task-execution.json and fetch pending tasks."""
    # Read local state
    task_state = {}
    if TASK_STATE_FILE.exists():
        task_state = json.loads(TASK_STATE_FILE.read_text())

    # Reset daily counter if date changed
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if task_state.get("date") != today:
        task_state["completedToday"] = 0
        task_state["date"] = today
        task_state["currentTask"] = None
        task_state["consecutiveDispatchFailures"] = 0
        atomic_write_json(TASK_STATE_FILE, task_state)

    # Fetch assigned tasks from MC
    try:
        my_tasks = api_request("GET", "/tasks", params={"assignee": "maxwell"})
    except Exception as e:
        return {
            "state": task_state,
            "pending_tasks": [],
            "stuck_tasks": [],
            "error": str(e),
        }

    todo_tasks = [t for t in my_tasks if t.get("status") in ("todo", "in_progress")]

    # Check for stuck tasks (in_progress > 2h)
    stuck_tasks = []
    for t in my_tasks:
        if t.get("status") == "in_progress":
            updated = t.get("updated_at", "")
            if updated:
                try:
                    updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    age_hours = (datetime.now(timezone.utc) - updated_dt).total_seconds() / 3600
                    if age_hours > 2:
                        stuck_tasks.append({"id": t["id"][:8], "title": t["title"],
                                            "stuck_hours": round(age_hours, 1)})
                except (ValueError, TypeError):
                    pass

    return {
        "state": task_state,
        "pending_tasks": [
            {"id": t["id"][:8], "title": t["title"], "status": t["status"],
             "priority": t.get("priority", "medium"),
             "created_at": t.get("created_at", ""),
             "due_date": t.get("due_date", ""),
             "tags": t.get("tags", []),
             "project": t.get("project", "")}
            for t in todo_tasks
        ],
        "stuck_tasks": stuck_tasks,
    }


def check_cron_health():
    """Check if scheduled jobs are stale."""
    if not CRON_HEALTH_FILE.exists():
        return {"status": "no cron-health.json found"}

    cron = json.loads(CRON_HEALTH_FILE.read_text())
    now = datetime.now(timezone.utc)
    alerts = []

    morning = cron.get("morningBriefing")
    if morning:
        try:
            last = datetime.fromisoformat(morning.replace("Z", "+00:00"))
            if (now - last) > timedelta(hours=26):
                alerts.append(f"morningBriefing stale ({morning})")
        except (ValueError, TypeError):
            pass

    weekly = cron.get("weeklyReview")
    if weekly:
        try:
            last = datetime.fromisoformat(weekly.replace("Z", "+00:00"))
            if (now - last) > timedelta(days=8):
                alerts.append(f"weeklyReview stale ({weekly})")
        except (ValueError, TypeError):
            pass

    return {"cron": cron, "alerts": alerts}


def check_containers():
    """Check Docker container health (only when running on host)."""
    expected = ["openclaw-openclaw-gateway-1", "mission-control-api",
                "mission-control-db", "mission-control-proxy"]
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}:{{.Status}}"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"running": [], "missing": expected, "alert": "docker ps failed"}

        running = {}
        for line in result.stdout.strip().split("\n"):
            if ":" in line:
                name, status = line.split(":", 1)
                running[name] = status

        missing = [c for c in expected if c not in running]
        unhealthy = [c for c in expected if c in running and "unhealthy" in running.get(c, "")]

        alert = None
        if missing:
            alert = f"Containers down: {', '.join(missing)}"
        elif unhealthy:
            alert = f"Containers unhealthy: {', '.join(unhealthy)}"

        return {"running": list(running.keys()), "missing": missing, "unhealthy": unhealthy, "alert": alert}
    except FileNotFoundError:
        return {"status": "skipped", "reason": "docker not available (running inside container?)"}
    except Exception as e:
        return {"error": str(e)}


def check_disk():
    """Check /mnt/external disk usage."""
    try:
        import shutil
        usage = shutil.disk_usage("/mnt/external")
        pct = (usage.used / usage.total) * 100
        free_gb = usage.free / (1024**3)
        alert = None
        if pct > 90:
            alert = f"/mnt/external {pct:.0f}% full ({free_gb:.1f}GB free)"
        elif pct > 80:
            alert = f"/mnt/external {pct:.0f}% used ({free_gb:.1f}GB free)"
        return {"total_gb": round(usage.total / (1024**3), 1), "free_gb": round(free_gb, 1),
                "pct": round(pct, 1), "alert": alert}
    except Exception as e:
        return {"error": str(e)}


def check_dispatch_history():
    """Get recent dispatch stats from MC API."""
    try:
        log = api_request("GET", "/dispatch/log", params={"limit": "50"})
        entries = log.get("items", [])
        total = len(entries)
        successes = sum(1 for e in entries if e.get("status") == "success")
        errors = sum(1 for e in entries if e.get("status") != "success")
        avg_ms = 0
        if successes:
            avg_ms = sum(e.get("elapsed_ms", 0) or 0 for e in entries if e.get("status") == "success") / successes
        return {
            "total_recent": total,
            "successes": successes,
            "errors": errors,
            "avg_latency_ms": round(avg_ms),
            "alert": f"Dispatch errors: {errors}/{total} recently" if errors > total * 0.5 and total > 2 else None,
        }
    except Exception as e:
        return {"error": str(e)}


def atomic_write_json(path, data):
    """Write JSON atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(path)


def dispatch_task(task_id, task_title, persona, prompt, timeout=180):
    """Dispatch a task to ZeroClaw and update state on success.
    Returns the dispatch result dict with added 'task_updated' field."""
    full_id = task_id

    # Read task state for circuit breaker
    task_state = {}
    if TASK_STATE_FILE.exists():
        task_state = json.loads(TASK_STATE_FILE.read_text())

    # Circuit breaker — skip dispatch after consecutive failures
    consecutive_failures = task_state.get("consecutiveDispatchFailures", 0)
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return {
            "status": "error",
            "error": f"Dispatch paused: {consecutive_failures} consecutive failures",
            "task_updated": False,
            "circuit_breaker": True,
        }

    # If short ID, try to resolve full ID from API
    if len(task_id) <= 8:
        try:
            my_tasks = api_request("GET", "/tasks", params={"assignee": "maxwell"})
            for t in my_tasks:
                if t["id"][:8] == task_id:
                    full_id = t["id"]
                    break
        except Exception:
            pass

    # Mark task as in_progress before dispatch
    try:
        api_request("PATCH", f"/tasks/{full_id}", data={"status": "in_progress"})
    except Exception as e:
        return {"status": "error", "error": f"Failed to update task status: {e}",
                "task_updated": False}

    # Update local state
    task_state["currentTask"] = task_id
    atomic_write_json(TASK_STATE_FILE, task_state)

    # Dispatch
    try:
        result = api_request("POST", "/dispatch", data={
            "persona": persona,
            "prompt": prompt,
            "timeout": timeout,
        })
    except (HTTPError, URLError) as e:
        # HTTP-level failure (503/504/502) — increment failure counter
        try:
            api_request("PATCH", f"/tasks/{full_id}", data={"status": "todo"})
        except Exception:
            pass
        task_state["currentTask"] = None
        task_state["consecutiveDispatchFailures"] = consecutive_failures + 1
        task_state["lastDispatchError"] = str(e)
        atomic_write_json(TASK_STATE_FILE, task_state)
        # Alert if hitting circuit breaker threshold
        if task_state["consecutiveDispatchFailures"] >= MAX_CONSECUTIVE_FAILURES:
            send_telegram(f"Dispatch circuit breaker: {task_state['consecutiveDispatchFailures']} consecutive failures. Last: {str(e)[:100]}")
        return {"status": "error", "error": f"Dispatch failed: {e}",
                "task_updated": False}
    except Exception as e:
        # Other failures
        try:
            api_request("PATCH", f"/tasks/{full_id}", data={"status": "todo"})
        except Exception:
            pass
        task_state["currentTask"] = None
        task_state["consecutiveDispatchFailures"] = consecutive_failures + 1
        task_state["lastDispatchError"] = str(e)
        atomic_write_json(TASK_STATE_FILE, task_state)
        return {"status": "error", "error": f"Dispatch failed: {e}",
                "task_updated": False}

    # Quality gate — check response before marking as done
    response_text = result.get("response", "")
    quality, reason = assess_quality(response_text)

    if quality != "ok":
        # Revert task to todo — don't count as completed
        try:
            api_request("PATCH", f"/tasks/{full_id}", data={
                "status": "todo",
                "description": f"Quality gate: {quality} ({reason}). Response: {response_text[:200]}",
            })
        except Exception:
            pass
        task_state["currentTask"] = None
        task_state["consecutiveDispatchFailures"] = task_state.get("consecutiveDispatchFailures", 0) + 1
        atomic_write_json(TASK_STATE_FILE, task_state)
        result["quality_gate"] = {"status": quality, "reason": reason}
        result["task_new_status"] = "todo (quality gate)"
        result["task_updated"] = False
        return result

    # Dispatch succeeded with good quality — update task to review, increment counter
    try:
        desc_update = f"Dispatched to {persona}. Response:\n{result.get('response', '')[:500]}"
        api_request("PATCH", f"/tasks/{full_id}", data={
            "status": "review",
            "description": desc_update,
        })
    except Exception:
        pass  # Non-critical — dispatch already succeeded

    task_state["completedToday"] = task_state.get("completedToday", 0) + 1
    task_state["currentTask"] = None
    task_state["consecutiveDispatchFailures"] = 0  # Reset on success
    atomic_write_json(TASK_STATE_FILE, task_state)

    result["task_updated"] = True
    result["task_id"] = task_id
    result["task_new_status"] = "review"
    return result


def _ram_bar(pct):
    """Compact visual RAM bar."""
    if pct is None:
        return "?"
    pct = int(pct)
    filled = pct // 10
    return f"{'█' * filled}{'░' * (10 - filled)} {pct}%"


def _status_icon(status):
    """Status to icon."""
    return {"healthy": "🟢", "degraded": "🟡", "offline": "🔴"}.get(status, "⚪")


def format_report(checks):
    """Format checks into a concise Telegram-friendly report."""
    now_str = datetime.now().strftime("%H:%M")
    alerts = []
    sections = []

    # ── Nodes ──
    nodes = checks.get("nodes", {})
    if nodes["status"] == "ok":
        node_lines = []
        for n in nodes["data"]:
            if n.get("alert"):
                alerts.append(f"⚠️ {n['alert']}")
            icon = _status_icon(n["status"])
            ram = _ram_bar(n.get("ram_pct"))
            temp = n.get("temp_c", "?")
            node_lines.append(f"  {icon} {n['name']:7s}  {ram}  {temp}°C")
        sections.append("🖥 Cluster\n" + "\n".join(node_lines))
    else:
        sections.append(f"🖥 Cluster: ERROR — {nodes.get('error', '?')}")
        alerts.append("⚠️ Cluster check failed")

    # ── Containers ──
    containers = checks.get("containers", {})
    if containers.get("status") == "ok" and isinstance(containers.get("data"), dict):
        cd = containers["data"]
        if cd.get("alert"):
            alerts.append(f"⚠️ {cd['alert']}")
        else:
            n_running = len(cd.get("running", []))
            sections.append(f"🐳 Containers: {n_running} running, all expected")

    # ── Bot ──
    bot = checks.get("polybot", {})
    if bot["status"] == "ok":
        d = bot["data"]
        budget = d.get("daily_budget_usd", 0)
        spent = d.get("daily_spent_usd", 0)
        pct_spent = (spent / budget * 100) if budget > 0 else 0
        mode_icon = "📄" if d["mode"] == "paper" else "💰"
        bot_line = f"🤖 Bot ({mode_icon} {d['mode']})\n"
        bot_line += f"  Budget: ${spent:.0f}/${budget:.0f} ({pct_spent:.0f}%)\n"
        bot_line += f"  Positions: {d['open_positions']} open"
        if d.get("enabled_traders"):
            bot_line += f"  |  Traders: {len(d['enabled_traders'])}"
        sections.append(bot_line)
        if d.get("alert"):
            alerts.append(f"⚠️ {d['alert']}")
    else:
        sections.append(f"🤖 Bot: ERROR — {bot.get('error', '?')}")

    # ── Tasks ──
    tasks = checks.get("tasks", {})
    if tasks["status"] == "ok":
        d = tasks["data"]
        n_pending = len(d.get("pending_tasks", []))
        completed = d["state"].get("completedToday", 0)
        failures = d["state"].get("consecutiveDispatchFailures", 0)
        task_line = f"📋 Tasks: {n_pending} pending · {completed} done today"
        if failures > 0:
            task_line += f" · ⚠️ {failures} dispatch fails"
        sections.append(task_line)
        for st in d.get("stuck_tasks", []):
            alerts.append(f"⚠️ Task {st['id']} stuck in_progress for {st['stuck_hours']}h")
    else:
        sections.append(f"📋 Tasks: ERROR — {tasks.get('error', '?')}")

    # ── Disk ──
    disk = checks.get("disk", {})
    if disk.get("status") == "ok" and isinstance(disk.get("data"), dict):
        dd = disk["data"]
        if dd.get("alert"):
            alerts.append(f"⚠️ {dd['alert']}")
        else:
            sections.append(f"💾 Disk: {dd['free_gb']:.0f}GB free ({dd['pct']}% used)")

    # ── Dispatch history ──
    dh = checks.get("dispatch_history", {})
    if dh.get("status") == "ok" and isinstance(dh.get("data"), dict):
        d = dh["data"]
        if d["total_recent"] > 0:
            avg_s = d["avg_latency_ms"] / 1000
            sections.append(f"📡 Dispatch: {d['successes']}/{d['total_recent']} ok · avg {avg_s:.0f}s")
        if d.get("alert"):
            alerts.append(f"⚠️ {d['alert']}")

    # ── Cron ──
    cron = checks.get("cron", {})
    if cron.get("status") == "ok" and cron.get("data", {}).get("alerts"):
        for a in cron["data"]["alerts"]:
            alerts.append(f"⚠️ {a}")

    # ── Dispatch result ──
    dispatch = checks.get("dispatch")
    if dispatch:
        if dispatch.get("status") == "ok":
            d = dispatch["data"]
            persona = d.get("persona", "?")
            task_id = d.get("task_id", "?")
            sections.append(f"🚀 Dispatched: {task_id} → {persona} ✓")
            if d.get("quality_gate"):
                qg = d["quality_gate"]
                sections.append(f"  Quality: {qg['status']} ({qg.get('reason', '')})")
        elif dispatch.get("status") == "error":
            d = dispatch.get("data", dispatch)
            if d.get("circuit_breaker"):
                alerts.append("⚠️ Dispatch circuit breaker active")
            else:
                err = d.get("error", "?")
                sections.append(f"🚀 Dispatch failed: {err[:80]}")

    # ── PRD gate ──
    prd_check = checks.get("prd")
    if prd_check and prd_check.get("status") == "ok":
        d = prd_check["data"]
        action = d.get("action", "?")
        slug = d.get("slug", "?")
        if action == "generated":
            sections.append(f"📝 PRD generated: {slug} — awaiting approval")
        elif action == "regenerated":
            sections.append(f"📝 PRD regenerated: {slug} (feedback addressed)")
        elif action == "pending":
            sections.append(f"📝 PRD pending: {slug}")

    # ── Assemble ──
    header = f"{'🔴' if alerts else '🟢'} Heartbeat @ {now_str}"
    parts = [header]
    if alerts:
        parts.append("\n".join(alerts))
        parts.append("─" * 20)
    parts.extend(sections)
    return "\n".join(parts)


def cmd_heartbeat(args):
    """Run full heartbeat: all checks + optional dispatch."""
    checks = {
        "nodes": run_check("nodes", check_nodes),
        "polybot": run_check("polybot", check_polybot),
        "tasks": run_check("tasks", check_tasks),
        "cron": run_check("cron", check_cron_health),
        "containers": run_check("containers", check_containers),
        "disk": run_check("disk", check_disk),
        "dispatch_history": run_check("dispatch_history", check_dispatch_history),
    }

    # Auto-dispatch if there are pending tasks and we haven't hit the daily limit
    task_data = checks["tasks"].get("data", {}) if checks["tasks"]["status"] == "ok" else {}
    state = task_data.get("state", {})
    pending = task_data.get("pending_tasks", [])
    max_per_day = state.get("maxPerDay", 5)
    completed_today = state.get("completedToday", 0)

    if pending and completed_today < max_per_day and not args.no_dispatch:
        pending.sort(key=score_task, reverse=True)
        task = pending[0]
        title = task.get("title", "")
        description = task.get("description", "")
        persona = select_persona(task)
        dispatch_timeout = state.get("dispatchTimeoutSecs", 180)

        if _is_simple_task(title):
            # Simple tasks dispatch directly, no PRD needed
            prompt = f"You are {persona}. Task: {title}\nTask ID: {task['id']}\nPlease work on this task and report your findings."
            dispatch_result = dispatch_task(
                task_id=task["id"], task_title=title,
                persona=persona, prompt=prompt, timeout=dispatch_timeout,
            )
            checks["dispatch"] = {"status": "ok" if dispatch_result.get("task_updated") else "error",
                                  "data": dispatch_result}
        else:
            # Complex tasks go through PRD gate
            slug = _task_slug(title)
            prd = _task_prd_status(slug)

            if prd is None:
                # No PRD exists — generate one and send for approval
                _slug, msg_id = _generate_prd(title, description, slug)
                _mark_prd_notified(slug, state)
                checks["prd"] = {"status": "ok", "data": {
                    "action": "generated", "slug": _slug, "message_id": msg_id}}

            elif prd["status"] == "approved":
                # PRD approved — dispatch with PRD context
                prd_context = _load_prd_context(slug)
                prompt = (
                    f"You are {persona}. Task: {title}\nTask ID: {task['id']}\n\n"
                    f"## Approved PRD\n{prd_context}\n\n"
                    "Work on this task following the PRD requirements and report your findings."
                )
                dispatch_result = dispatch_task(
                    task_id=task["id"], task_title=title,
                    persona=persona, prompt=prompt, timeout=dispatch_timeout,
                )
                checks["dispatch"] = {"status": "ok" if dispatch_result.get("task_updated") else "error",
                                      "data": dispatch_result}

            elif prd["status"] == "rejected":
                # Rejected with feedback — regenerate
                feedback = prd.get("feedback", "")
                _slug, msg_id = _generate_prd(title, description, slug, feedback=feedback)
                _mark_prd_notified(slug, state)
                checks["prd"] = {"status": "ok", "data": {
                    "action": "regenerated", "slug": _slug, "feedback": feedback[:100]}}

            elif prd["status"] == "pending":
                # Waiting for approval — send reminder if cooldown passed
                if not _prd_recently_notified(slug, state):
                    _tg_send(f"Reminder: PRD <b>{slug}</b> still pending approval.")
                    _mark_prd_notified(slug, state)
                checks["prd"] = {"status": "ok", "data": {
                    "action": "pending", "slug": slug}}

    report = format_report(checks)

    if args.json:
        print(json.dumps(checks, indent=2, default=str))
    else:
        print(report)

    if getattr(args, "telegram", False):
        send_telegram(report)

    # Exit code: non-zero if any critical check failed
    has_errors = any(checks[k]["status"] == "error" for k in ("nodes", "polybot", "tasks"))
    sys.exit(0 if not has_errors else 1)


def cmd_daily_summary(args):
    """Generate end-of-day summary."""
    today = datetime.now().strftime("%a %b %d")

    # Read task state
    task_state = {}
    if TASK_STATE_FILE.exists():
        task_state = json.loads(TASK_STATE_FILE.read_text())

    # Get dispatch history from MC API
    dispatch_stats = {"total": 0, "successes": 0, "errors": 0, "avg_ms": 0}
    try:
        log = api_request("GET", "/dispatch/log", params={"limit": "100"})
        entries = log.get("items", [])
        dispatch_stats["total"] = len(entries)
        dispatch_stats["successes"] = sum(1 for e in entries if e.get("status") == "success")
        dispatch_stats["errors"] = sum(1 for e in entries if e.get("status") != "success")
        if dispatch_stats["successes"]:
            dispatch_stats["avg_ms"] = sum(
                e.get("elapsed_ms", 0) or 0 for e in entries if e.get("status") == "success"
            ) / dispatch_stats["successes"]
    except Exception:
        pass

    # Get pending tasks
    pending_count = 0
    review_count = 0
    try:
        my_tasks = api_request("GET", "/tasks", params={"assignee": "maxwell"})
        pending_count = sum(1 for t in my_tasks if t.get("status") in ("todo", "in_progress"))
        review_count = sum(1 for t in my_tasks if t.get("status") == "review")
    except Exception:
        pass

    # Get node health snapshot
    node_line = ""
    try:
        nodes = api_request("GET", "/nodes")
        all_healthy = all(n.get("status") == "healthy" for n in nodes)
        node_line = "🟢 All nodes healthy" if all_healthy else "🟡 Some nodes degraded"
    except Exception:
        node_line = "❓ Nodes unreachable"

    completed = task_state.get("completedToday", 0)
    failures = task_state.get("consecutiveDispatchFailures", 0)

    lines = [
        f"📊 Daily Summary — {today}",
        "",
        "📋 Tasks",
        f"  Completed today: {completed}",
        f"  In review: {review_count}",
        f"  Still pending: {pending_count}",
        "",
        "📡 Dispatch",
        f"  Success rate: {dispatch_stats['successes']}/{dispatch_stats['total']}",
    ]
    if dispatch_stats["avg_ms"]:
        lines.append(f"  Avg latency: {dispatch_stats['avg_ms'] / 1000:.0f}s")
    if failures > 0:
        lines.append(f"  ⚠️ {failures} consecutive failures")

    lines.extend(["", node_line])

    report = "\n".join(lines)
    if args.json:
        print(json.dumps({"summary": report, "task_state": task_state, "dispatch": dispatch_stats}, indent=2))
    else:
        print(report)

    if getattr(args, "telegram", False):
        send_telegram(report)


def cmd_morning_brief(args):
    """Morning briefing — what needs attention today."""
    today = datetime.now().strftime("%a %b %d")
    checks = {
        "nodes": run_check("nodes", check_nodes),
        "tasks": run_check("tasks", check_tasks),
        "polybot": run_check("polybot", check_polybot),
    }

    alerts = []
    sections = [f"☀️ Morning Brief — {today}"]

    # Node health
    if checks["nodes"]["status"] == "ok":
        unhealthy = [n for n in checks["nodes"]["data"] if n.get("alert")]
        if unhealthy:
            for n in unhealthy:
                alerts.append(f"⚠️ {n['alert']}")
        else:
            sections.append("🖥 Cluster: all healthy")
    else:
        alerts.append("⚠️ Cluster check failed")

    # Top tasks for today
    if checks["tasks"]["status"] == "ok":
        pending = checks["tasks"]["data"].get("pending_tasks", [])
        stuck = checks["tasks"]["data"].get("stuck_tasks", [])
        if stuck:
            for st in stuck:
                alerts.append(f"⚠️ Task {st['id']} stuck {st['stuck_hours']}h")
        if pending:
            pending_scored = sorted(pending, key=score_task, reverse=True)[:5]
            priority_icons = {"U": "🔴", "H": "🟠", "M": "🔵", "L": "⚪"}
            sections.append(f"\n📋 Today's Queue ({len(pending)} pending)")
            for t in pending_scored:
                p = t["priority"][0].upper()
                icon = priority_icons.get(p, "⚪")
                sections.append(f"  {icon} {t['title'][:45]}")
        else:
            sections.append("\n📋 No pending tasks — inbox zero!")

    # Bot overnight
    if checks["polybot"]["status"] == "ok":
        d = checks["polybot"]["data"]
        mode_icon = "📄" if d["mode"] == "paper" else "💰"
        bot_line = f"\n🤖 Bot: {mode_icon} {d['mode']} · {d['open_positions']} positions"
        if d.get("alert"):
            alerts.append(f"⚠️ {d['alert']}")
        sections.append(bot_line)

    # Assemble
    parts = []
    if alerts:
        parts.append(sections[0])  # header
        parts.append("\n".join(alerts))
        parts.append("─" * 20)
        parts.extend(sections[1:])
    else:
        parts = sections

    report = "\n".join(parts)

    if args.json:
        print(json.dumps(checks, indent=2, default=str))
    else:
        print(report)

    if getattr(args, "telegram", False):
        send_telegram(report)


def cmd_dispatch_task(args):
    """Dispatch a specific task with feedback loop."""
    result = dispatch_task(
        task_id=args.task_id,
        task_title=args.title or "Untitled",
        persona=args.persona,
        prompt=args.prompt,
        timeout=args.timeout,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("task_updated"):
            print(f"✓ Dispatched task {args.task_id} → {args.persona}")
            print(f"  Status: {result.get('task_new_status', '?')}")
            print(f"  Response: {result.get('response', '')[:200]}")
        else:
            print(f"✗ Dispatch failed: {result.get('error', 'unknown')}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Heartbeat Runner")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    sub = parser.add_subparsers(dest="command")

    # heartbeat
    p_hb = sub.add_parser("heartbeat", help="Run full heartbeat checks")
    p_hb.add_argument("--no-dispatch", action="store_true",
                       help="Skip auto-dispatch even if tasks are pending")
    p_hb.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    p_hb.add_argument("--json", action="store_true", help="JSON output")

    # daily-summary
    p_ds = sub.add_parser("daily-summary", help="End-of-day summary")
    p_ds.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    p_ds.add_argument("--json", action="store_true", help="JSON output")

    # morning-brief
    p_mb = sub.add_parser("morning-brief", help="Morning briefing")
    p_mb.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    p_mb.add_argument("--json", action="store_true", help="JSON output")

    # dispatch-task
    p_dt = sub.add_parser("dispatch-task", help="Dispatch a task with state tracking")
    p_dt.add_argument("task_id", help="Task ID (short or full)")
    p_dt.add_argument("persona", help="Persona to dispatch to")
    p_dt.add_argument("prompt", help="Prompt for the persona")
    p_dt.add_argument("--title", help="Task title (for logging)")
    p_dt.add_argument("--timeout", type=int, default=180, help="Timeout seconds")

    args = parser.parse_args()
    if not args.command:
        # Default: run heartbeat
        args.command = "heartbeat"
        args.no_dispatch = False

    if args.command == "heartbeat":
        cmd_heartbeat(args)
    elif args.command == "dispatch-task":
        cmd_dispatch_task(args)
    elif args.command == "daily-summary":
        cmd_daily_summary(args)
    elif args.command == "morning-brief":
        cmd_morning_brief(args)


if __name__ == "__main__":
    main()
