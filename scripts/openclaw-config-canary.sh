#!/bin/bash
# openclaw-config-canary.sh — independent hourly schema check for
# ~/.openclaw/openclaw.json.
#
# Context: the mission-control-watchdog only validates the config as part
# of a restart decision. That means a schema-tightening upstream release
# (or a silent edit of openclaw.json) can sit undetected until the next
# unrelated restart event — which is exactly how the 2026-04-09 gateway
# restart-loop outage became invisible until 04:05. This canary closes
# that window by validating the on-disk config on a fixed hourly cadence,
# completely decoupled from restart events.
#
# It does NOT attempt to fix or restart anything. It only emits a
# structured line to stdout/stderr; alerting lives in whoever consumes
# the systemd journal for this unit.
#
# Exit codes (propagated from openclaw-config-validate.sh):
#   0 — config is schema-valid
#   2 — config is schema-invalid
#   3 — invocation failure (validator missing, config missing, docker down)
#
# Environment:
#   OPENCLAW_CONFIG    path to openclaw.json (default: $HOME/.openclaw/openclaw.json)
#   OPENCLAW_VALIDATOR path to validator script (default: sibling openclaw-config-validate.sh)
#
# Usage:
#   openclaw-config-canary.sh            # one-shot check

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
VALIDATOR="${OPENCLAW_VALIDATOR:-$SCRIPT_DIR/openclaw-config-validate.sh}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

if [ ! -x "$VALIDATOR" ]; then
    echo "[canary] $(ts) INVOCATION_FAILED validator not executable: $VALIDATOR" >&2
    exit 3
fi
if [ ! -f "$OPENCLAW_CONFIG" ]; then
    echo "[canary] $(ts) INVOCATION_FAILED config not found: $OPENCLAW_CONFIG" >&2
    exit 3
fi

# Intentionally NOT using --quiet: the canary's whole job is to leave
# diagnostic evidence in the journal. When the validator fails we want
# its "Config invalid: required property ..." line captured here so
# whoever reads the unit log tomorrow doesn't have to re-run it by hand.
out=$("$VALIDATOR" "$OPENCLAW_CONFIG" 2>&1)
rc=$?

case "$rc" in
    0)
        echo "[canary] $(ts) OK $OPENCLAW_CONFIG"
        ;;
    2)
        echo "[canary] $(ts) SCHEMA_INVALID $OPENCLAW_CONFIG" >&2
        # Surface whatever the validator said so the journal has the detail.
        if [ -n "$out" ]; then
            echo "$out" >&2
        fi
        ;;
    3)
        echo "[canary] $(ts) INVOCATION_FAILED $OPENCLAW_CONFIG" >&2
        if [ -n "$out" ]; then
            echo "$out" >&2
        fi
        ;;
    *)
        echo "[canary] $(ts) UNKNOWN_RC=$rc $OPENCLAW_CONFIG" >&2
        if [ -n "$out" ]; then
            echo "$out" >&2
        fi
        ;;
esac

exit "$rc"
