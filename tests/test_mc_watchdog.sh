#!/bin/bash
# Tests for scripts/mission-control-watchdog.sh.
#
# Exercises the hardened watchdog via shims. Asserts the behaviours that
# prevent the 2026-04-09 gateway restart-loop class of outage:
#
#   1. Pre-flight config validation gates any gateway restart/recreate.
#      On schema-invalid config, the watchdog must NOT call docker compose
#      on the gateway, must post status=degraded to MC, and must exit 0.
#
#   2. Plain unhealthy gateway uses `docker compose restart` (non-destructive)
#      instead of `docker compose up -d` or `--force-recreate`.
#
#   3. Token-drift path is gated behind the config validator — no
#      --force-recreate on invalid config.
#
#   4. Consecutive-failure circuit breaker: after 3 failed recoveries,
#      the watchdog stops restarting and escalates instead of looping.
#
#   5. `_consecutive_failures` resets to 0 on a successful recovery.
#
#   6. flock: two concurrent invocations — the second exits 0 immediately
#      without running any check.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WATCHDOG="$REPO_DIR/scripts/mission-control-watchdog.sh"

# shellcheck source=scripts/lib/test-harness.sh
source "$REPO_DIR/scripts/lib/test-harness.sh"
# shellcheck source=tests/lib/watchdog-shims.sh
source "$SCRIPT_DIR/lib/watchdog-shims.sh"

echo "=== mission-control-watchdog.sh ==="

# --- common shim setup ---
# A healthy-everywhere baseline: docker inspect returns matching token,
# curl always succeeds, mongosh succeeds.
_baseline_shims() {
    shims_set_script docker <<'BASH'
case "$*" in
    *"inspect openclaw-openclaw-gateway-1"*)
        # Token matches the .env so no drift is reported.
        echo "OPENCLAW_GATEWAY_TOKEN=shim-expected-token"
        ;;
    *"exec mongodb"*)
        echo "1"
        ;;
    *"compose ps"*)
        echo "mission-control-db"
        ;;
    *)
        :
        ;;
esac
exit 0
BASH

    shims_set_script curl <<'BASH'
# Every URL is healthy by default.
exit 0
BASH
}

# Force gateway /healthz to fail while everything else is OK.
_gateway_unhealthy_curl() {
    shims_set_script curl <<'BASH'
case "$*" in
    *"localhost:18789/healthz"*)
        exit 22   # curl-style HTTP failure
        ;;
    *)
        exit 0
        ;;
esac
BASH
}

# Point the watchdog at a fixture config and inject a canned validator result.
# $1 = "valid" | "invalid"
_install_validator_mock() {
    local mode="$1"
    local mock="$SHIM_BIN/openclaw-config-validate"
    cat > "$mock" <<SHIM
#!/bin/bash
printf '%q ' "\$0" "\$@" >> "$CALLS_DIR/openclaw-config-validate.log"
printf '\n' >> "$CALLS_DIR/openclaw-config-validate.log"
case "$mode" in
    valid)   exit 0 ;;
    invalid) echo "Config invalid: memory-lancedb.embedding missing" >&2; exit 2 ;;
    *)       exit 3 ;;
esac
SHIM
    chmod +x "$mock"
    # Watchdog must consult the validator via this hook path.
    export OPENCLAW_VALIDATE_CMD="$mock"
    export OPENCLAW_CONFIG="$SCRIPT_DIR/fixtures/openclaw/invalid-lancedb.json"
}

# -- T1: invalid config + unhealthy gateway => skip restart, post degraded --
run_t1() {
    shims_init
    _baseline_shims
    _gateway_unhealthy_curl
    _install_validator_mock invalid

    "$WATCHDOG" >/dev/null 2>&1
    rc=$?

    if [ "$rc" -eq 0 ]; then
        pass "T1: watchdog exits 0 on invalid config"
    else
        fail "T1: watchdog exits 0 on invalid config" "rc=$rc"
    fi

    shims_assert_not_called docker "compose up -d openclaw-gateway" \
        "T1: no 'docker compose up -d' for gateway on invalid config"
    shims_assert_not_called docker "compose restart openclaw-gateway" \
        "T1: no 'docker compose restart' for gateway on invalid config"
    shims_assert_not_called docker "force-recreate" \
        "T1: no --force-recreate on invalid config"
    shims_assert_called openclaw-config-validate "openclaw-gateway\|OPENCLAW_CONFIG\|\.json" \
        "T1: validator was called"
    shims_assert_called curl "services/check" \
        "T1: posted status update to MC /services/check"

    shims_cleanup
    unset OPENCLAW_VALIDATE_CMD OPENCLAW_CONFIG
}

# -- T2: valid config + unhealthy gateway => uses `compose restart`, not `up -d` --
run_t2() {
    shims_init
    _baseline_shims
    _gateway_unhealthy_curl
    _install_validator_mock valid

    "$WATCHDOG" >/dev/null 2>&1 || true

    shims_assert_called docker "compose restart openclaw-gateway" \
        "T2: plain unhealthy uses 'compose restart'"
    shims_assert_not_called docker "compose up -d openclaw-gateway" \
        "T2: plain unhealthy does NOT use 'compose up -d'"
    shims_assert_not_called docker "force-recreate" \
        "T2: plain unhealthy does NOT --force-recreate"

    shims_cleanup
    unset OPENCLAW_VALIDATE_CMD OPENCLAW_CONFIG
}

# -- T3: token drift + invalid config => no recreate --
run_t3() {
    shims_init
    _baseline_shims
    # Report drift: the inspect returns a different token.
    shims_set_script docker <<'BASH'
case "$*" in
    *"inspect openclaw-openclaw-gateway-1"*)
        echo "OPENCLAW_GATEWAY_TOKEN=actual-different-token"
        ;;
    *"exec mongodb"*) echo 1 ;;
    *"compose ps"*)  echo "mission-control-db" ;;
esac
exit 0
BASH
    # Gateway healthy — we only want to trigger drift path.
    shims_set_script curl <<'BASH'
exit 0
BASH
    _install_validator_mock invalid

    "$WATCHDOG" >/dev/null 2>&1 || true

    shims_assert_not_called docker "force-recreate" \
        "T3: drift + invalid config does NOT --force-recreate"
    shims_assert_called openclaw-config-validate "." \
        "T3: validator called on drift path"

    shims_cleanup
    unset OPENCLAW_VALIDATE_CMD OPENCLAW_CONFIG
}

# -- T4: circuit breaker — 3 consecutive failures => skip restart, escalate --
run_t4() {
    shims_init
    _baseline_shims
    _gateway_unhealthy_curl
    _install_validator_mock valid

    # Seed state file showing 3 consecutive failures.
    printf '{"gateway_consecutive_failures":3}\n' > "$WATCHDOG_STATE"

    "$WATCHDOG" >/dev/null 2>&1 || true

    shims_assert_not_called docker "compose restart openclaw-gateway" \
        "T4: circuit-open skips 'compose restart'"
    shims_assert_not_called docker "compose up -d openclaw-gateway" \
        "T4: circuit-open skips 'compose up -d'"
    shims_assert_called curl "services/check" \
        "T4: still posts status to MC when circuit-open"

    shims_cleanup
    unset OPENCLAW_VALIDATE_CMD OPENCLAW_CONFIG
}

# -- T5: counter resets to 0 on successful recovery --
run_t5() {
    shims_init
    _baseline_shims
    _install_validator_mock valid

    # Seed state with some failures; gateway healthy this run.
    printf '{"gateway_consecutive_failures":2}\n' > "$WATCHDOG_STATE"

    "$WATCHDOG" >/dev/null 2>&1 || true

    # The state file should now show 0 consecutive failures for the gateway.
    if grep -q '"gateway_consecutive_failures": *0' "$WATCHDOG_STATE" 2>/dev/null; then
        pass "T5: counter resets to 0 on healthy check"
    else
        fail "T5: counter resets to 0 on healthy check" "state=$(cat "$WATCHDOG_STATE" 2>/dev/null)"
    fi

    shims_cleanup
    unset OPENCLAW_VALIDATE_CMD OPENCLAW_CONFIG
}

# -- T6: flock — second concurrent run exits immediately --
run_t6() {
    shims_init
    _baseline_shims
    _install_validator_mock valid

    # Hold the lock file ourselves; run the watchdog and check it bails fast.
    exec 9> "$WATCHDOG_LOCK_FILE"
    flock -n 9 || { fail "T6: test setup could not take lock" "flock failed"; shims_cleanup; return; }

    start=$(date +%s)
    "$WATCHDOG" >/dev/null 2>&1
    rc=$?
    end=$(date +%s)
    elapsed=$((end - start))

    exec 9>&-  # release lock

    if [ "$rc" -eq 0 ] && [ "$elapsed" -lt 3 ]; then
        pass "T6: second concurrent run exits 0 quickly (${elapsed}s)"
    else
        fail "T6: second concurrent run exits 0 quickly" "rc=$rc elapsed=${elapsed}s"
    fi

    shims_assert_not_called docker "compose" \
        "T6: second concurrent run did not touch docker"

    shims_cleanup
    unset OPENCLAW_VALIDATE_CMD OPENCLAW_CONFIG
}

run_t1
run_t2
run_t3
run_t4
run_t5
run_t6

test_summary
