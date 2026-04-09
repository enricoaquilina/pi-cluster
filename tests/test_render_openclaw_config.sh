#!/bin/bash
# Tests for scripts/render-openclaw-config.sh and
# scripts/install-openclaw-config.sh.
#
# Exercises the deterministic-render-from-template workflow that
# replaced the 2026-04-07 "root:root drifting JSON" failure mode.
#
# Contract under test:
#   - render with stdout mode substitutes allowlisted vars only
#   - render with -o writes atomic file, validates, propagates
#     validator exit code
#   - render refuses to expand stray ${non-allowlisted} references
#   - render with missing template / missing env file exits 3
#   - render with unreadable template / env file exits 3
#   - install script is idempotent (byte-identical render = no-op)
#   - install script dry-run never touches the live target
#   - install script backs up existing target before overwriting

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RENDER="$REPO_DIR/scripts/render-openclaw-config.sh"
INSTALL="$REPO_DIR/scripts/install-openclaw-config.sh"
TEMPLATE="$REPO_DIR/configs/openclaw/openclaw.json.template"

# shellcheck source=scripts/lib/test-harness.sh
source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== render/install-openclaw-config ==="

TMP=$(mktemp -d -t render-oc.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# --- Stub validator that always succeeds ------------------------------
# Both render and install delegate to the validator via OPENCLAW_VALIDATOR.
# We don't want tests to require docker, so we inject a no-op pass.
STUB_VALIDATOR="$TMP/stub-validator.sh"
cat > "$STUB_VALIDATOR" <<'BASH'
#!/bin/bash
# Accept --quiet and a path, always exit 0
exit 0
BASH
chmod +x "$STUB_VALIDATOR"

# And a deliberately-failing validator for the failure-path test
FAIL_VALIDATOR="$TMP/fail-validator.sh"
cat > "$FAIL_VALIDATOR" <<'BASH'
#!/bin/bash
echo "Config invalid: synthetic failure" >&2
exit 2
BASH
chmod +x "$FAIL_VALIDATOR"

# --- Fixture template -------------------------------------------------
# A minimal JSON template exercising every envsubst edge case that
# matters: an allowlisted var, a non-allowlisted var that MUST stay
# literal, and a non-tokenized string.
FIXTURE_TEMPLATE="$TMP/fixture.json.template"
cat > "$FIXTURE_TEMPLATE" <<'JSON'
{
  "channels": {
    "telegram": {
      "botToken": "${TELEGRAM_BOT_TOKEN}"
    }
  },
  "literal_dollar_brace": "keep me as ${SHOULD_NOT_EXPAND}",
  "literal_string": "hello"
}
JSON

FIXTURE_ENV="$TMP/fixture.env"
cat > "$FIXTURE_ENV" <<'ENV'
# comment line — ignored
TELEGRAM_BOT_TOKEN=fake-test-token-1234
SHOULD_NOT_EXPAND=this-must-not-leak-into-output
IRRELEVANT_OTHER_VAR=also-ignored
ENV

# --- Case 1: stdout mode substitutes only allowlisted vars ------------
out_file="$TMP/case1.out"
OPENCLAW_VALIDATOR="$STUB_VALIDATOR" \
    "$RENDER" --template "$FIXTURE_TEMPLATE" --env "$FIXTURE_ENV" \
    > "$out_file" 2>"$TMP/case1.err"
rc=$?
if [ "$rc" -eq 0 ]; then
    pass "stdout render exits 0"
else
    fail "stdout render exits 0" "rc=$rc err=$(cat "$TMP/case1.err")"
fi

if grep -q '"botToken": "fake-test-token-1234"' "$out_file"; then
    pass "allowlisted var substituted"
else
    fail "allowlisted var substituted" "out=$(cat "$out_file")"
fi

# Critical: the non-allowlisted placeholder must survive verbatim.
# If this ever fails, envsubst is leaking arbitrary env vars into
# user-controlled config strings.
if grep -q '"literal_dollar_brace": "keep me as ${SHOULD_NOT_EXPAND}"' "$out_file"; then
    pass "non-allowlisted dollar-brace kept literal"
else
    fail "non-allowlisted dollar-brace kept literal" "out=$(cat "$out_file")"
fi

# Sanity: output is valid JSON
if command -v jq >/dev/null 2>&1; then
    if jq -e . "$out_file" >/dev/null 2>&1; then
        pass "stdout render produces valid JSON"
    else
        fail "stdout render produces valid JSON" "out=$(cat "$out_file")"
    fi
fi

# --- Case 2: -o writes atomically and validates -----------------------
target="$TMP/case2-output.json"
OPENCLAW_VALIDATOR="$STUB_VALIDATOR" \
    "$RENDER" --template "$FIXTURE_TEMPLATE" --env "$FIXTURE_ENV" \
    -o "$target" >"$TMP/case2.out" 2>"$TMP/case2.err"
rc=$?
if [ "$rc" -eq 0 ] && [ -f "$target" ]; then
    pass "-o mode writes output file"
else
    fail "-o mode writes output file" "rc=$rc"
fi

# Verify perms are 0600 (contains a secret)
if [ "$(stat -c '%a' "$target" 2>/dev/null)" = "600" ]; then
    pass "-o mode output is mode 600"
else
    fail "-o mode output is mode 600" "perms=$(stat -c '%a' "$target" 2>/dev/null)"
fi

# --- Case 3: validator failure bubbles up ----------------------------
target3="$TMP/case3-output.json"
OPENCLAW_VALIDATOR="$FAIL_VALIDATOR" \
    "$RENDER" --template "$FIXTURE_TEMPLATE" --env "$FIXTURE_ENV" \
    -o "$target3" >"$TMP/case3.out" 2>"$TMP/case3.err"
rc=$?
if [ "$rc" -eq 2 ]; then
    pass "render exits 2 when validator fails"
else
    fail "render exits 2 when validator fails" "rc=$rc"
fi
if [ -f "$target3" ]; then
    fail "render does NOT create output on validator failure" "file exists at $target3"
else
    pass "render does NOT create output on validator failure"
fi

# --- Case 4: missing template -> exit 3 ------------------------------
OPENCLAW_VALIDATOR="$STUB_VALIDATOR" \
    "$RENDER" --template "$TMP/nope.template" --env "$FIXTURE_ENV" \
    >/dev/null 2>"$TMP/case4.err"
rc=$?
if [ "$rc" -eq 3 ]; then
    pass "missing template exits 3"
else
    fail "missing template exits 3" "rc=$rc"
fi
if grep -q "template not found" "$TMP/case4.err"; then
    pass "missing template error is informative"
else
    fail "missing template error is informative" "$(cat "$TMP/case4.err")"
fi

# --- Case 5: missing env file -> exit 3 ------------------------------
OPENCLAW_VALIDATOR="$STUB_VALIDATOR" \
    "$RENDER" --template "$FIXTURE_TEMPLATE" --env "$TMP/nope.env" \
    >/dev/null 2>"$TMP/case5.err"
rc=$?
if [ "$rc" -eq 3 ]; then
    pass "missing env file exits 3"
else
    fail "missing env file exits 3" "rc=$rc"
fi

# --- Case 6: install idempotent on byte-identical render --------------
# Pre-populate the target with exactly what the render will produce.
target6="$TMP/case6-target.json"
OPENCLAW_VALIDATOR="$STUB_VALIDATOR" \
    "$RENDER" --template "$FIXTURE_TEMPLATE" --env "$FIXTURE_ENV" \
    -o "$target6" >/dev/null 2>&1
# Now running install with the same inputs should detect
# byte-identity and not create a backup.
backup_count_before=$(find "$(dirname "$target6")" -name "$(basename "$target6").bak-*" | wc -l)
OPENCLAW_VALIDATOR="$STUB_VALIDATOR" \
OPENCLAW_CONFIG_TEMPLATE="$FIXTURE_TEMPLATE" \
    "$INSTALL" --env "$FIXTURE_ENV" --target "$target6" \
    >"$TMP/case6.out" 2>"$TMP/case6.err"
rc=$?
backup_count_after=$(find "$(dirname "$target6")" -name "$(basename "$target6").bak-*" | wc -l)
if [ "$rc" -eq 0 ]; then
    pass "install exits 0 on no-op"
else
    fail "install exits 0 on no-op" "rc=$rc err=$(cat "$TMP/case6.err")"
fi
if [ "$backup_count_after" -eq "$backup_count_before" ]; then
    pass "install does not back up on byte-identical render"
else
    fail "install does not back up on byte-identical render" \
        "before=$backup_count_before after=$backup_count_after"
fi
if grep -q "byte-identical" "$TMP/case6.out"; then
    pass "install logs 'byte-identical' on no-op"
else
    fail "install logs 'byte-identical' on no-op" "out=$(cat "$TMP/case6.out")"
fi

# --- Case 7: install backs up on drift --------------------------------
target7="$TMP/case7-target.json"
# Write a different starting content so the render produces a diff.
echo '{"drift": true}' > "$target7"
chmod 600 "$target7"
OPENCLAW_VALIDATOR="$STUB_VALIDATOR" \
OPENCLAW_CONFIG_TEMPLATE="$FIXTURE_TEMPLATE" \
    "$INSTALL" --env "$FIXTURE_ENV" --target "$target7" \
    >"$TMP/case7.out" 2>"$TMP/case7.err"
rc=$?
if [ "$rc" -eq 0 ]; then
    pass "install exits 0 on drift"
else
    fail "install exits 0 on drift" "rc=$rc err=$(cat "$TMP/case7.err")"
fi
backup_files=$(find "$(dirname "$target7")" -name "$(basename "$target7").bak-*")
if [ -n "$backup_files" ]; then
    pass "install created backup on drift"
else
    fail "install created backup on drift" "no backup found"
fi
# Verify the live file was replaced
if grep -q "fake-test-token-1234" "$target7"; then
    pass "install replaced live target with rendered output"
else
    fail "install replaced live target with rendered output" "$(cat "$target7")"
fi

# --- Case 8: install --dry-run does not touch target -----------------
target8="$TMP/case8-target.json"
echo '{"untouched": true}' > "$target8"
chmod 600 "$target8"
original_sha=$(sha256sum "$target8" | awk '{print $1}')
OPENCLAW_VALIDATOR="$STUB_VALIDATOR" \
OPENCLAW_CONFIG_TEMPLATE="$FIXTURE_TEMPLATE" \
    "$INSTALL" --dry-run --env "$FIXTURE_ENV" --target "$target8" \
    >/dev/null 2>"$TMP/case8.err"
rc=$?
after_sha=$(sha256sum "$target8" | awk '{print $1}')
if [ "$rc" -eq 0 ] && [ "$original_sha" = "$after_sha" ]; then
    pass "--dry-run leaves target untouched"
else
    fail "--dry-run leaves target untouched" "rc=$rc before=$original_sha after=$after_sha"
fi

# --- Case 9: real template parses and renders ------------------------
# Smoke-test the actual committed template against a synthetic env.
# This catches template-level syntax errors before deploy.
if [ -f "$TEMPLATE" ]; then
    real_env="$TMP/real-env"
    echo 'TELEGRAM_BOT_TOKEN=smoketest' > "$real_env"
    rendered="$TMP/real-rendered.json"
    OPENCLAW_VALIDATOR="$STUB_VALIDATOR" \
        "$RENDER" --template "$TEMPLATE" --env "$real_env" -o "$rendered" \
        >/dev/null 2>"$TMP/case9.err"
    rc=$?
    if [ "$rc" -eq 0 ] && jq -e . "$rendered" >/dev/null 2>&1; then
        pass "real committed template renders to valid JSON"
    else
        fail "real committed template renders to valid JSON" \
            "rc=$rc err=$(cat "$TMP/case9.err")"
    fi
    if jq -r '.channels.telegram.botToken' "$rendered" 2>/dev/null | grep -q "smoketest"; then
        pass "real template substitutes TELEGRAM_BOT_TOKEN"
    else
        fail "real template substitutes TELEGRAM_BOT_TOKEN" \
            "botToken=$(jq -r '.channels.telegram.botToken' "$rendered" 2>/dev/null)"
    fi
else
    warn "committed template not found at $TEMPLATE — skipping Case 9" "this is expected in PRs that add the template for the first time"
fi

test_summary
