#!/bin/bash
# Tests for scripts/openclaw-restart-count-alerter.sh.
#
# Exercises the sliding-window flapping detector via a docker shim
# that returns deterministic RestartCount values. The shim lets us
# fast-forward the sliding window without actually crashing containers.
#
# Contract under test:
#   - First run with no prior state: no alert (need >=2 samples)
#   - Count unchanged across window: no alert
#   - Count delta < threshold: no alert
#   - Count delta >= threshold: alert
#   - Re-run within dedup window: no re-alert
#   - Re-run after dedup expires: alert again
#   - RestartCount decrease (container recreate): baseline reset, no alert
#   - Samples older than window are pruned
#   - Non-existent container is skipped silently
#   - Non-integer RestartCount (shim returns "bogus") is skipped with warning

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ALERTER="$REPO_DIR/scripts/openclaw-restart-count-alerter.sh"

# shellcheck source=scripts/lib/test-harness.sh
source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== openclaw-restart-count-alerter ==="

TMP=$(mktemp -d -t restart-alerter.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# --- Docker shim ------------------------------------------------------
# Writes the container name + requested RestartCount to a log so we
# can assert what the alerter asked for. Returns counts from a table
# the test sets up via COUNT_<sanitized_container_name>_<n>.
SHIM_BIN="$TMP/bin"
mkdir -p "$SHIM_BIN"
DOCKER_SHIM="$SHIM_BIN/docker"
cat > "$DOCKER_SHIM" <<'BASH'
#!/bin/bash
# Usage: docker inspect --format '{{.RestartCount}}' <name>
if [ "$1" != "inspect" ]; then
    exit 1
fi
# Last arg is the container name. Use the portable last-arg idiom
# "${@: -1}" — note the space before -1 is required.
name="${@: -1}"
# Sanitize via bash parameter expansion — avoids the `echo | tr`
# gotcha where echo's trailing newline gets replaced by tr -c,
# producing a spurious trailing underscore.
sanitized="${name//[^A-Za-z0-9]/_}"
var="COUNT_${sanitized}"
# If the variable is unset, pretend the container doesn't exist
# (exit non-zero, like real docker inspect on unknown containers).
val="${!var:-__UNSET__}"
if [ "$val" = "__UNSET__" ]; then
    exit 1
fi
echo "$val"
BASH
chmod +x "$DOCKER_SHIM"

# Stub curl so send_alert doesn't actually hit telegram API in tests.
CURL_SHIM="$SHIM_BIN/curl"
cat > "$CURL_SHIM" <<'BASH'
#!/bin/bash
echo "curl $*" >> "${CURL_LOG:-/dev/null}"
exit 0
BASH
chmod +x "$CURL_SHIM"

# Common env for every invocation: single service, tight window, low
# threshold so fewer ticks are needed to trigger.
run_alerter() {
    RESTART_COUNT_SERVICES="test-gateway" \
    RESTART_COUNT_WINDOW_SECS="1800" \
    RESTART_COUNT_THRESHOLD="3" \
    RESTART_COUNT_DEDUP_SECS="3600" \
    RESTART_COUNT_STATE_DIR="$STATE" \
    RESTART_COUNT_DOCKER_CMD="$DOCKER_SHIM" \
    RESTART_COUNT_CURL_CMD="$CURL_SHIM" \
    TELEGRAM_BOT_TOKEN="fake" \
    TELEGRAM_CHAT_ID="fake" \
    COUNT_test_gateway="$1" \
    CURL_LOG="$CURL_LOG" \
        bash "$ALERTER" 2>&1
}

# --- Case 1: first sample, no state -> no alert ----------------------
STATE="$TMP/case1-state"
mkdir -p "$STATE"
CURL_LOG="$TMP/case1.curl"
: > "$CURL_LOG"

out=$(run_alerter 0)
rc=$?
if [ "$rc" -eq 0 ]; then
    pass "first sample exits 0"
else
    fail "first sample exits 0" "rc=$rc out=$out"
fi
if [ -s "$CURL_LOG" ]; then
    fail "first sample does NOT alert" "curl log: $(cat "$CURL_LOG")"
else
    pass "first sample does NOT alert"
fi
if [ -f "$STATE/test-gateway.tsv" ]; then
    pass "first sample writes state file"
else
    fail "first sample writes state file" "state dir: $(ls -la "$STATE")"
fi

# --- Case 2: two samples, unchanged count -> no alert ----------------
STATE="$TMP/case2-state"
mkdir -p "$STATE"
CURL_LOG="$TMP/case2.curl"
: > "$CURL_LOG"

run_alerter 5 >/dev/null
# Sleep so the second row has a later timestamp
sleep 1
run_alerter 5 >/dev/null
if [ ! -s "$CURL_LOG" ]; then
    pass "unchanged count does NOT alert"
else
    fail "unchanged count does NOT alert" "curl log: $(cat "$CURL_LOG")"
fi

# --- Case 3: delta below threshold -> no alert -----------------------
STATE="$TMP/case3-state"
mkdir -p "$STATE"
CURL_LOG="$TMP/case3.curl"
: > "$CURL_LOG"

run_alerter 1 >/dev/null
sleep 1
run_alerter 2 >/dev/null   # delta = 1, below threshold of 3
if [ ! -s "$CURL_LOG" ]; then
    pass "delta below threshold does NOT alert"
else
    fail "delta below threshold does NOT alert" "curl log: $(cat "$CURL_LOG")"
fi

# --- Case 4: delta >= threshold -> alert -----------------------------
STATE="$TMP/case4-state"
mkdir -p "$STATE"
CURL_LOG="$TMP/case4.curl"
: > "$CURL_LOG"

run_alerter 0 >/dev/null
sleep 1
run_alerter 4 >/dev/null   # delta = 4, at threshold
if [ -s "$CURL_LOG" ]; then
    pass "delta >= threshold alerts"
else
    fail "delta >= threshold alerts" "no curl invocation"
fi
if grep -q "sendMessage" "$CURL_LOG"; then
    pass "alert hits telegram sendMessage endpoint"
else
    fail "alert hits telegram sendMessage endpoint" "log: $(cat "$CURL_LOG")"
fi

# --- Case 5: re-run within dedup window -> no re-alert ---------------
CURL_LOG="$TMP/case5.curl"
: > "$CURL_LOG"
sleep 1
run_alerter 5 >/dev/null   # still above threshold
if [ ! -s "$CURL_LOG" ]; then
    pass "re-alert suppressed within dedup window"
else
    fail "re-alert suppressed within dedup window" "log: $(cat "$CURL_LOG")"
fi

# --- Case 6: dedup window expired -> alert fires again ---------------
# Simulate dedup expiry by backdating the last-alerted sentinel file.
CURL_LOG="$TMP/case6.curl"
: > "$CURL_LOG"
echo "1" > "$STATE/test-gateway.last-alerted"  # 1970 — very old
sleep 1
run_alerter 6 >/dev/null
if [ -s "$CURL_LOG" ]; then
    pass "re-alert fires after dedup expires"
else
    fail "re-alert fires after dedup expires" "log empty"
fi

# --- Case 7: RestartCount decrease -> baseline reset, no alert ------
STATE="$TMP/case7-state"
mkdir -p "$STATE"
CURL_LOG="$TMP/case7.curl"
: > "$CURL_LOG"

run_alerter 10 >/dev/null
sleep 1
out=$(run_alerter 2 2>&1)  # decrease = container recreate
if [ ! -s "$CURL_LOG" ]; then
    pass "decrease in count does NOT alert (baseline reset)"
else
    fail "decrease in count does NOT alert" "log: $(cat "$CURL_LOG")"
fi
if echo "$out" | grep -q "baseline reset"; then
    pass "decrease logs baseline reset message"
else
    fail "decrease logs baseline reset message" "out=$out"
fi
# State file should now contain exactly one row (the reset point)
row_count=$(wc -l < "$STATE/test-gateway.tsv")
if [ "$row_count" = "1" ]; then
    pass "decrease prunes state to single row"
else
    fail "decrease prunes state to single row" "row_count=$row_count"
fi

# --- Case 8: non-existent container -> silent skip -------------------
STATE="$TMP/case8-state"
mkdir -p "$STATE"
CURL_LOG="$TMP/case8.curl"
: > "$CURL_LOG"
# Don't set COUNT_test_gateway at all (via unset)
out=$(
    RESTART_COUNT_SERVICES="test-gateway" \
    RESTART_COUNT_WINDOW_SECS="1800" \
    RESTART_COUNT_THRESHOLD="3" \
    RESTART_COUNT_STATE_DIR="$STATE" \
    RESTART_COUNT_DOCKER_CMD="$DOCKER_SHIM" \
    RESTART_COUNT_CURL_CMD="$CURL_SHIM" \
    CURL_LOG="$CURL_LOG" \
        bash "$ALERTER" 2>&1
)
rc=$?
if [ "$rc" -eq 0 ]; then
    pass "non-existent container exits 0"
else
    fail "non-existent container exits 0" "rc=$rc out=$out"
fi
if [ ! -f "$STATE/test-gateway.tsv" ]; then
    pass "non-existent container writes no state file"
else
    fail "non-existent container writes no state file" "state exists"
fi

# --- Case 9: non-integer RestartCount -> skip with warning -----------
STATE="$TMP/case9-state"
mkdir -p "$STATE"
CURL_LOG="$TMP/case9.curl"
: > "$CURL_LOG"

out=$(
    RESTART_COUNT_SERVICES="test-gateway" \
    RESTART_COUNT_STATE_DIR="$STATE" \
    RESTART_COUNT_DOCKER_CMD="$DOCKER_SHIM" \
    RESTART_COUNT_CURL_CMD="$CURL_SHIM" \
    COUNT_test_gateway="bogus" \
    CURL_LOG="$CURL_LOG" \
        bash "$ALERTER" 2>&1
)
if echo "$out" | grep -q "non-integer"; then
    pass "non-integer RestartCount logs warning"
else
    fail "non-integer RestartCount logs warning" "out=$out"
fi
if [ ! -s "$CURL_LOG" ]; then
    pass "non-integer RestartCount does NOT alert"
else
    fail "non-integer RestartCount does NOT alert" "log=$(cat "$CURL_LOG")"
fi

# --- Case 10: old samples are pruned from state ----------------------
STATE="$TMP/case10-state"
mkdir -p "$STATE"
# Pre-seed state with samples older than the window
old_ts=$(( $(date +%s) - 10000 ))  # way past 1800s window
printf '%s\t0\n%s\t1\n' "$old_ts" "$((old_ts + 1))" > "$STATE/test-gateway.tsv"
CURL_LOG="$TMP/case10.curl"
: > "$CURL_LOG"

run_alerter 10 >/dev/null
# After pruning + appending one new row, state should have exactly 1 row.
row_count=$(wc -l < "$STATE/test-gateway.tsv")
if [ "$row_count" = "1" ]; then
    pass "old samples pruned from state"
else
    fail "old samples pruned from state" "row_count=$row_count contents=$(cat "$STATE/test-gateway.tsv")"
fi
# And no alert because we only have one surviving sample.
if [ ! -s "$CURL_LOG" ]; then
    pass "pruned-then-single-sample does NOT alert"
else
    fail "pruned-then-single-sample does NOT alert" "log=$(cat "$CURL_LOG")"
fi

test_summary
