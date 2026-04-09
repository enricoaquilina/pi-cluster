#!/bin/bash
# Test that the pinned router-api requirements actually let
# openclaw-cluster-service.py import cleanly.
#
# This regression-guards the 2026-04-09 outage: fastapi 0.115.6 +
# starlette 1.0.0 (the broken pairing) threw TypeError on import because
# FastAPI.routing.APIRouter still forwards on_startup= to Starlette,
# which removed that parameter in 1.0. The pin in
# scripts/requirements-router-api.txt must keep starlette inside FastAPI's
# allowed range.
#
# The test creates a disposable venv, installs the pinned requirements,
# and imports openclaw-cluster-service.py via importlib. It does NOT
# start uvicorn or touch the live venv at /home/enrico/.local/openclaw-router-venv.
#
# Skipped in CI if python3-venv is unavailable or if the test would take
# too long (set SKIP_VENV_TESTS=1).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REQUIREMENTS="$REPO_DIR/scripts/requirements-router-api.txt"
SERVICE_SCRIPT="$REPO_DIR/scripts/openclaw-cluster-service.py"

# shellcheck source=scripts/lib/test-harness.sh
source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== router-api pinned venv import test ==="

if [ "${SKIP_VENV_TESTS:-0}" = "1" ]; then
    warn "router-api venv test" "SKIP_VENV_TESTS=1 — skipping"
    test_summary
    exit 0
fi

if ! python3 -c 'import venv' 2>/dev/null; then
    warn "router-api venv test" "python3-venv not installed — skipping"
    test_summary
    exit 0
fi

if [ ! -f "$REQUIREMENTS" ]; then
    fail "requirements file present" "missing $REQUIREMENTS"
    test_summary
    exit 1
fi
pass "requirements file present"

if [ ! -f "$SERVICE_SCRIPT" ]; then
    fail "service script present" "missing $SERVICE_SCRIPT"
    test_summary
    exit 1
fi
pass "service script present"

# Contract assertion: the requirements file must name BOTH fastapi and
# starlette explicitly. If you bump fastapi, you must also bump starlette
# to stay inside FastAPI's Requires-Dist range.
if grep -q '^fastapi==' "$REQUIREMENTS"; then
    pass "fastapi is pinned"
else
    fail "fastapi is pinned" "no 'fastapi==' line in $REQUIREMENTS"
fi

if grep -q '^starlette==' "$REQUIREMENTS"; then
    pass "starlette is pinned"
else
    fail "starlette is pinned" "no 'starlette==' line in $REQUIREMENTS"
fi

# Don't trigger a real pip download in cheap CI runs: if a marker file is
# set, stop here. Otherwise proceed to the live venv build.
if [ "${ROUTER_VENV_ASSERTIONS_ONLY:-0}" = "1" ]; then
    test_summary
    exit $?
fi

TMP_VENV=$(mktemp -d -t router-venv.XXXXXX)
trap 'rm -rf "$TMP_VENV"' EXIT

echo "  building disposable venv at $TMP_VENV ..."
if ! python3 -m venv "$TMP_VENV" >/tmp/router-venv-create.log 2>&1; then
    fail "venv creation" "see /tmp/router-venv-create.log"
    test_summary
    exit 1
fi
pass "venv creation"

"$TMP_VENV/bin/pip" install --quiet --upgrade pip >/tmp/router-venv-pip.log 2>&1 || true

echo "  installing pinned requirements ..."
if ! "$TMP_VENV/bin/pip" install --quiet --require-virtualenv -r "$REQUIREMENTS" \
        >/tmp/router-venv-pip.log 2>&1; then
    fail "pip install requirements" "see /tmp/router-venv-pip.log"
    test_summary
    exit 1
fi
pass "pip install requirements"

# Confirm starlette is actually within fastapi's allowed range.
if "$TMP_VENV/bin/python" - <<'PY'
import fastapi, starlette
fa = fastapi.__version__
st = starlette.__version__
print(f"fastapi={fa} starlette={st}")
# fastapi 0.115.x pins starlette<0.42,>=0.40
assert fa.startswith("0.115."), f"unexpected fastapi version: {fa}"
assert st.startswith("0.41."), f"starlette {st} outside fastapi 0.115.x allowed range (<0.42)"
PY
then
    pass "fastapi/starlette version compatibility"
else
    fail "fastapi/starlette version compatibility" "assertion failed"
fi

# The real regression: importing the module must not raise TypeError.
if "$TMP_VENV/bin/python" - <<PY
import importlib.util, sys
spec = importlib.util.spec_from_file_location("svc", "$SERVICE_SCRIPT")
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(f"IMPORT FAIL: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
assert hasattr(mod, "app"), "expected FastAPI 'app' attribute"
print("import ok")
PY
then
    pass "openclaw-cluster-service.py imports cleanly"
else
    fail "openclaw-cluster-service.py imports cleanly" \
         "see stderr above — likely FastAPI/Starlette incompatibility"
fi

test_summary
