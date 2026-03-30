"""Mission Control API — thin entry point with backward-compatible re-exports."""

import logging

import psycopg2  # noqa: F401 — needed for test patches (e.g. mock psycopg2.connect)

from app import app  # noqa: F401
from app.db import _pool, init_db, get_db  # noqa: F401
from app.event_bus import EventBus, event_bus  # noqa: F401
from app.auth import verify_api_key, _global_limiter  # noqa: F401
from app.config import (  # noqa: F401
    DATABASE_URL, POLYBOT_DATA, API_KEY, ALLOWED_ORIGINS, OPENCLAW_DIR, LIFE_DIR,
    _start_time, HEARTBEAT_STALE_SECONDS,
    OPENROUTER_API_KEY, BUDGET_DAILY, BUDGET_WEEKLY, BUDGET_MONTHLY,
    BUDGET_ALERT_THRESHOLD,
    DEEPSEEK_API_KEY, MOONSHOT_API_KEY, TAVILY_API_KEY, BILLING_ALERT_BALANCE_USD,
)
from app.helpers import row_to_dict  # noqa: F401
from app.models import (  # noqa: F401
    TaskStatus, TaskPriority, TaskCreate, TaskUpdate, TaskResponse, TASK_COLUMNS,
    NodeStatus, NodeCreate, NodeUpdate, NodeResponse, NODE_COLUMNS,
    ServiceStatus, AlertStatus, ServiceCheckIn, ServiceCheckBulk, ServiceAlertIn,
    DispatchRequest, DispatchResponse, DispatchLogEntry,
)
from app.dispatch_engine import (  # noqa: F401
    PERSONA_ROUTING, ZEROCLAW_NODES, RateLimiter,
    rate_limiter, _zeroclaw_chat, _is_node_dispatchable, _log_dispatch,
    NODE_MODELS, FALLBACK_NODES, FALLBACK_DELEGATE_MAP,
)
from app.trading_helpers import TEAM_ROSTER  # noqa: F401
from app.budget_helpers import (  # noqa: F401
    _fetch_openrouter_usage, _budget_cache,
    _fetch_deepseek_balance, _fetch_moonshot_balance, _fetch_tavily_usage,
    _fetch_all_provider_balances, _check_balance_alert, _check_usage_alert,
)
from app.background import _heartbeat_sweep, _budget_snapshot, _node_snapshot, _provider_balance_snapshot  # noqa: F401

logger = logging.getLogger("mission-control")
