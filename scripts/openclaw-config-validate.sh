#!/bin/bash
# openclaw-config-validate.sh — host-side schema validator for ~/.openclaw/openclaw.json.
#
# Runs `openclaw doctor` inside a throwaway container (read-only bind mount)
# and returns a structured exit code so callers (watchdog, CI, canary timer)
# can gate restarts on config validity.
#
# Exit codes:
#   0 — config is schema-valid (no errors; legacy-key warnings are allowed)
#   2 — config is schema-invalid; stderr names the problem
#   3 — invocation failure (docker missing, image missing, bad path)
#
# Usage:
#   openclaw-config-validate.sh [--quiet] <path-to-openclaw.json>
#
# Environment overrides (for tests / CI):
#   OPENCLAW_IMAGE         — image to run (default: openclaw-custom:local)
#   OPENCLAW_VALIDATE_CMD  — if set, this command is executed instead of the
#                            docker-based validator. Used by tests to inject a
#                            deterministic result without requiring Docker.

set -uo pipefail

QUIET=false
CONFIG=""
IMAGE="${OPENCLAW_IMAGE:-openclaw-custom:local}"

while [ $# -gt 0 ]; do
    case "$1" in
        --quiet|-q) QUIET=true; shift ;;
        -h|--help)
            cat <<'EOF'
Usage: openclaw-config-validate.sh [--quiet] <path-to-openclaw.json>

Exit codes:
  0  valid
  2  schema-invalid (stderr names the problem)
  3  invocation failure
EOF
            exit 0
            ;;
        --) shift; CONFIG="${1:-}"; break ;;
        -*) echo "unknown flag: $1" >&2; exit 3 ;;
        *)  CONFIG="$1"; shift ;;
    esac
done

log_out() { $QUIET || echo "$*"; }
log_err() { echo "$*" >&2; }

if [ -z "$CONFIG" ]; then
    log_err "usage: openclaw-config-validate.sh [--quiet] <path>"
    exit 3
fi
if [ ! -e "$CONFIG" ]; then
    log_err "config not found: $CONFIG"
    exit 3
fi
if [ ! -f "$CONFIG" ]; then
    log_err "config is not a regular file: $CONFIG"
    exit 3
fi
# Upfront readability check. Without this, a root-owned config (the
# 2026-04-07 incident pattern) slips past the existence check, the
# cp below silently fails, docker then runs doctor against an empty
# mount, and the validator cheerfully returns "config valid" for a
# file it never actually read. That silent-lie behaviour would defeat
# the entire point of #124's validation gate — turning it from "skips
# on missing validator" into "always green on unreadable config",
# which is strictly worse. Catch it here, exit 3, name the owner so
# the operator can chown it.
if [ ! -r "$CONFIG" ]; then
    owner="unknown"
    if command -v stat >/dev/null 2>&1; then
        owner="$(stat -c '%U:%G (mode %a)' "$CONFIG" 2>/dev/null || echo unknown)"
    fi
    log_err "config not readable by $(id -un): $CONFIG (owned by $owner)"
    log_err "fix: sudo chown $(id -un):$(id -gn) $CONFIG"
    exit 3
fi

# Test hook: delegate entirely to an injected command.
if [ -n "${OPENCLAW_VALIDATE_CMD:-}" ]; then
    if $QUIET; then
        "$OPENCLAW_VALIDATE_CMD" "$CONFIG" >/dev/null 2>&1
        exit $?
    else
        "$OPENCLAW_VALIDATE_CMD" "$CONFIG"
        exit $?
    fi
fi

if ! command -v docker >/dev/null 2>&1; then
    log_err "docker not found on PATH"
    exit 3
fi

# Doctor expects the file to be named `openclaw.json` inside
# /home/node/.openclaw, so copy the target into a fresh tmpdir under a
# canonical name before bind-mounting. This also decouples the validator
# from whatever the on-disk filename happens to be.
WORK_DIR="$(mktemp -d -t openclaw-validate.XXXXXX)"
TMP_OUT="$WORK_DIR/doctor.out"
trap 'rm -rf "$WORK_DIR"' EXIT
# Belt-and-braces: even though the upfront -r check above should have
# caught any permission problems, still verify that the copy actually
# produced the expected file before proceeding. `set -e` is NOT active
# in this script (we rely on explicit exit-code inspection elsewhere),
# so a silent cp failure is otherwise recoverable only by checking
# the destination. If either step fails, exit 3 — do NOT let docker
# run against an empty mount and report "valid" on a file we never
# actually copied.
if ! cp "$CONFIG" "$WORK_DIR/openclaw.json" 2>"$WORK_DIR/cp.err"; then
    log_err "failed to stage config for validation:"
    sed -n '1,5p' "$WORK_DIR/cp.err" >&2 2>/dev/null || true
    exit 3
fi
if [ ! -s "$WORK_DIR/openclaw.json" ]; then
    log_err "staged config is empty or missing: $WORK_DIR/openclaw.json"
    exit 3
fi
if ! chmod 644 "$WORK_DIR/openclaw.json" 2>>"$WORK_DIR/cp.err"; then
    log_err "failed to chmod staged config:"
    sed -n '1,5p' "$WORK_DIR/cp.err" >&2 2>/dev/null || true
    exit 3
fi

docker run --rm \
    -v "$WORK_DIR:/home/node/.openclaw:ro" \
    "$IMAGE" \
    openclaw doctor \
    > "$TMP_OUT" 2>&1
docker_rc=$?

# Schema failure signatures. The image prints these whether or not doctor
# itself exits non-zero, so we pattern-match rather than trust docker_rc.
if grep -qE 'Config invalid|required property|invalid config' "$TMP_OUT"; then
    if ! $QUIET; then
        grep -E 'Config invalid|required property|invalid config|File:|Problem:' "$TMP_OUT" >&2 || true
    else
        grep -E 'Config invalid|required property|invalid config' "$TMP_OUT" >&2 || true
    fi
    exit 2
fi

# Docker itself failed (image missing, daemon down).
if [ "$docker_rc" -ne 0 ] && ! grep -qE 'Gateway|Doctor|Config' "$TMP_OUT"; then
    log_err "doctor invocation failed (rc=$docker_rc):"
    sed -n '1,20p' "$TMP_OUT" >&2
    exit 3
fi

# Success (legacy warnings are printed but non-fatal).
if grep -qE 'legacy' "$TMP_OUT"; then
    log_err "config valid with legacy-key warnings — run 'openclaw doctor --fix'"
fi
log_out "config valid"
exit 0
