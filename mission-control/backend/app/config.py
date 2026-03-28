"""Environment variables and constants for Mission Control."""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("mission-control")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)

DATABASE_URL = os.environ["DATABASE_URL"]
POLYBOT_DATA = Path(os.environ.get("POLYBOT_DATA_DIR", "/polybot-data"))
API_KEY = os.environ.get("API_KEY", "")
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
OPENCLAW_DIR = Path(os.environ.get("OPENCLAW_DIR", "/openclaw"))

_start_time = time.time()

HEARTBEAT_STALE_SECONDS = int(os.environ.get("HEARTBEAT_STALE_SECONDS", "120"))

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
BUDGET_DAILY = float(os.environ.get("BUDGET_DAILY", "5.00"))
BUDGET_WEEKLY = float(os.environ.get("BUDGET_WEEKLY", "25.00"))
BUDGET_MONTHLY = float(os.environ.get("BUDGET_MONTHLY", "75.00"))
BUDGET_ALERT_THRESHOLD = float(os.environ.get("BUDGET_ALERT_THRESHOLD", "0.80"))
