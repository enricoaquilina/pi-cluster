#!/bin/bash
set -euo pipefail
# install-openclaw-router-venv.sh — create / refresh the dedicated venv for
# openclaw-router-api.service.
#
# Why: openclaw-cluster-service.py is pinned against fastapi 0.115.6 but the
# user site-packages on heavy got upgraded to starlette 1.0.0 (pulled in by
# sse-starlette via an unrelated --break-system-packages install), breaking
# the FastAPI app at import time. The fix is to give router-api its own
# venv that pip can actually solve, independent of whatever the user site
# does next.
#
# Idempotent: safe to re-run. Updates the venv in place if it already exists.
#
# Usage: scripts/install-openclaw-router-venv.sh [VENV_PATH]
#        default VENV_PATH: /home/enrico/.local/openclaw-router-venv

VENV="${1:-/home/enrico/.local/openclaw-router-venv}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUIREMENTS="$SCRIPT_DIR/requirements-router-api.txt"
SERVICE_SCRIPT="$SCRIPT_DIR/openclaw-cluster-service.py"

log() { echo "[install-router-venv] $*"; }

if [ ! -f "$REQUIREMENTS" ]; then
    echo "requirements file missing: $REQUIREMENTS" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found on PATH" >&2
    exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
    log "creating venv at $VENV"
    python3 -m venv "$VENV"
else
    log "venv already present at $VENV (will refresh)"
fi

log "upgrading pip"
"$VENV/bin/pip" install --quiet --upgrade pip

log "installing pinned requirements from $REQUIREMENTS"
"$VENV/bin/pip" install --quiet --require-virtualenv -r "$REQUIREMENTS"

# Smoke-test: the service script must import cleanly inside the venv.
# Uses importlib to avoid actually starting uvicorn.
if [ -f "$SERVICE_SCRIPT" ]; then
    log "smoke-testing import of openclaw-cluster-service.py"
    "$VENV/bin/python" - <<PY
import importlib.util, sys
spec = importlib.util.spec_from_file_location("svc", "$SERVICE_SCRIPT")
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(f"IMPORT FAIL: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
assert hasattr(mod, "app"), "expected FastAPI 'app' attribute"
print("import ok; fastapi=", __import__("fastapi").__version__,
      " starlette=", __import__("starlette").__version__)
PY
else
    log "NOTE: $SERVICE_SCRIPT not found — skipping import smoke test"
fi

log "done. Update the systemd unit's ExecStart to use: $VENV/bin/python"
log "then: sudo systemctl daemon-reload && sudo systemctl restart openclaw-router-api"
