#!/bin/bash
# Tests for scripts/openclaw-config-canary.sh.
#
# The canary wrapper delegates to openclaw-config-validate.sh, which in
# turn supports an OPENCLAW_VALIDATE_CMD test hook to bypass docker. We
# exploit that hook to exercise every exit code without needing the real
# openclaw image on the test host.
#
# Contract under test:
#   - exit 0 when validator reports valid  -> stdout line contains OK
#   - exit 2 when validator reports invalid -> stderr line contains SCHEMA_INVALID
#   - exit 3 when config path is missing    -> stderr line contains INVOCATION_FAILED
#   - exit 3 when validator script is missing or not executable

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CANARY="$REPO_DIR/scripts/openclaw-config-canary.sh"
VALIDATOR="$REPO_DIR/scripts/openclaw-config-validate.sh"

# shellcheck source=scripts/lib/test-harness.sh
source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== openclaw-config-canary.sh ==="

TMP=$(mktemp -d -t canary-test.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# --- Fixtures: a dummy config and two fake validator backends ---------
FAKE_CONFIG="$TMP/openclaw.json"
echo '{"plugins":{}}' > "$FAKE_CONFIG"

# Injected validator backends the real validator will delegate to when
# OPENCLAW_VALIDATE_CMD is set.
cat > "$TMP/validator-ok" <<'BASH'
#!/bin/bash
exit 0
BASH
cat > "$TMP/validator-invalid" <<'BASH'
#!/bin/bash
echo "Config invalid: required property 'embedding' is missing" >&2
exit 2
BASH
cat > "$TMP/validator-broken" <<'BASH'
#!/bin/bash
echo "docker: command not found" >&2
exit 3
BASH
chmod +x "$TMP/validator-ok" "$TMP/validator-invalid" "$TMP/validator-broken"

run_canary() {
    # Run the canary with an explicit validator (the real one, which
    # honors OPENCLAW_VALIDATE_CMD) and a chosen backend.
    local backend="$1" cfg="$2"
    OPENCLAW_CONFIG="$cfg" \
    OPENCLAW_VALIDATOR="$VALIDATOR" \
    OPENCLAW_VALIDATE_CMD="$backend" \
        "$CANARY"
}

# --- Case 1: valid config -> exit 0 + OK line on stdout ---------------
out_file="$TMP/case1.out"
err_file="$TMP/case1.err"
run_canary "$TMP/validator-ok" "$FAKE_CONFIG" >"$out_file" 2>"$err_file"
rc=$?
if [ "$rc" -eq 0 ]; then
    pass "valid config -> exit 0"
else
    fail "valid config -> exit 0" "rc=$rc stderr=$(cat "$err_file")"
fi
if grep -q "OK" "$out_file"; then
    pass "valid config -> stdout contains OK"
else
    fail "valid config -> stdout contains OK" "out=$(cat "$out_file")"
fi

# --- Case 2: schema-invalid -> exit 2 + SCHEMA_INVALID on stderr ------
out_file="$TMP/case2.out"
err_file="$TMP/case2.err"
run_canary "$TMP/validator-invalid" "$FAKE_CONFIG" >"$out_file" 2>"$err_file"
rc=$?
if [ "$rc" -eq 2 ]; then
    pass "invalid config -> exit 2"
else
    fail "invalid config -> exit 2" "rc=$rc"
fi
if grep -q "SCHEMA_INVALID" "$err_file"; then
    pass "invalid config -> stderr contains SCHEMA_INVALID"
else
    fail "invalid config -> stderr contains SCHEMA_INVALID" "err=$(cat "$err_file")"
fi
if grep -q "required property" "$err_file"; then
    pass "invalid config -> stderr relays validator detail"
else
    fail "invalid config -> stderr relays validator detail" "err=$(cat "$err_file")"
fi

# --- Case 3: missing config -> exit 3 + INVOCATION_FAILED -------------
out_file="$TMP/case3.out"
err_file="$TMP/case3.err"
OPENCLAW_CONFIG="$TMP/does-not-exist.json" \
OPENCLAW_VALIDATOR="$VALIDATOR" \
    "$CANARY" >"$out_file" 2>"$err_file"
rc=$?
if [ "$rc" -eq 3 ]; then
    pass "missing config -> exit 3"
else
    fail "missing config -> exit 3" "rc=$rc"
fi
if grep -q "INVOCATION_FAILED" "$err_file"; then
    pass "missing config -> stderr contains INVOCATION_FAILED"
else
    fail "missing config -> stderr contains INVOCATION_FAILED" "err=$(cat "$err_file")"
fi

# --- Case 4: missing validator -> exit 3 ------------------------------
out_file="$TMP/case4.out"
err_file="$TMP/case4.err"
OPENCLAW_CONFIG="$FAKE_CONFIG" \
OPENCLAW_VALIDATOR="$TMP/no-such-validator.sh" \
    "$CANARY" >"$out_file" 2>"$err_file"
rc=$?
if [ "$rc" -eq 3 ]; then
    pass "missing validator -> exit 3"
else
    fail "missing validator -> exit 3" "rc=$rc"
fi
if grep -q "INVOCATION_FAILED" "$err_file"; then
    pass "missing validator -> stderr contains INVOCATION_FAILED"
else
    fail "missing validator -> stderr contains INVOCATION_FAILED" "err=$(cat "$err_file")"
fi

# --- Case 5: validator invocation failure (exit 3 underneath) ---------
out_file="$TMP/case5.out"
err_file="$TMP/case5.err"
run_canary "$TMP/validator-broken" "$FAKE_CONFIG" >"$out_file" 2>"$err_file"
rc=$?
if [ "$rc" -eq 3 ]; then
    pass "validator rc=3 -> canary exit 3"
else
    fail "validator rc=3 -> canary exit 3" "rc=$rc"
fi
if grep -q "INVOCATION_FAILED" "$err_file"; then
    pass "validator rc=3 -> stderr contains INVOCATION_FAILED"
else
    fail "validator rc=3 -> stderr contains INVOCATION_FAILED" "err=$(cat "$err_file")"
fi

test_summary
