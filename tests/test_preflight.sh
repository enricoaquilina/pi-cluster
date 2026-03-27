#!/bin/bash
# Tests for openclaw-preflight.sh
# Validates the preflight script correctly detects misconfigurations.
# Uses a temp directory with mock .env and docker-compose.yml files.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFLIGHT="$SCRIPT_DIR/../scripts/openclaw-preflight.sh"
PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  PASS  $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL  $1: $2"; }

setup_mock_env() {
    local tmpdir="$1"
    # Minimal docker-compose.yml that references env vars
    cat > "$tmpdir/docker-compose.yml" <<'YAML'
services:
  test:
    image: alpine
    environment:
      OPENCLAW_GATEWAY_TOKEN: ${OPENCLAW_GATEWAY_TOKEN:-}
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}
      GOOGLE_AI_API_KEY: ${GOOGLE_AI_API_KEY:-}
YAML
    # Valid .env with all required keys
    cat > "$tmpdir/.env" <<'ENV'
OPENCLAW_GATEWAY_TOKEN=test-token-abc123
OPENROUTER_API_KEY=sk-or-test-key
GOOGLE_AI_API_KEY=AIzaSy-test-key
ENV
    chmod 600 "$tmpdir/.env"
    # Matching .env.cluster
    cp "$tmpdir/.env" "$tmpdir/.env.cluster"
    chmod 600 "$tmpdir/.env.cluster"
}

# All tests skip runtime checks (container + SSH) since we're testing config validation only
export OPENCLAW_PREFLIGHT_SKIP_RUNTIME=true

echo "=== Preflight Script Tests ==="

# ── Test 1: Passes with valid config ─────────────────────────────────────────
echo "1. Valid config passes"
tmpdir=$(mktemp -d)
setup_mock_env "$tmpdir"

output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
  bash "$PREFLIGHT" --ci 2>&1)
exit_code=$?
rm -rf "$tmpdir"

if [ "$exit_code" -eq 0 ]; then
    pass "Valid config exits 0"
else
    fail "Valid config" "exited $exit_code"
fi

# ── Test 2: Fails on missing required env var ────────────────────────────────
echo "2. Missing required env var"
tmpdir=$(mktemp -d)
setup_mock_env "$tmpdir"
# Remove OPENROUTER_API_KEY
sed -i '/OPENROUTER_API_KEY/d' "$tmpdir/.env"
sed -i '/OPENROUTER_API_KEY/d' "$tmpdir/.env.cluster"

output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
  bash "$PREFLIGHT" --ci 2>&1)
exit_code=$?
rm -rf "$tmpdir"

if [ "$exit_code" -ne 0 ]; then
    pass "Missing OPENROUTER_API_KEY detected (exit $exit_code)"
else
    fail "Missing env var" "should have failed but exited 0"
fi
if echo "$output" | grep -q "FAIL.*OPENROUTER_API_KEY"; then
    pass "Error message mentions OPENROUTER_API_KEY"
else
    fail "Error message" "doesn't mention the missing key"
fi

# ── Test 3: Fails on env var drift between .env and .env.cluster ─────────────
echo "3. Env var drift detection"
tmpdir=$(mktemp -d)
setup_mock_env "$tmpdir"
# Create .env.cluster with different token
cat > "$tmpdir/.env.cluster" <<'ENV'
OPENCLAW_GATEWAY_TOKEN=different-token-xyz
OPENROUTER_API_KEY=sk-or-test-key
ENV
chmod 600 "$tmpdir/.env.cluster"

output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
  bash "$PREFLIGHT" --ci 2>&1)
exit_code=$?
rm -rf "$tmpdir"

if [ "$exit_code" -ne 0 ]; then
    pass "Token drift detected (exit $exit_code)"
else
    fail "Token drift" "should have failed but exited 0"
fi
if echo "$output" | grep -q "FAIL.*drift"; then
    pass "Error message mentions drift"
else
    fail "Error message" "doesn't mention drift"
fi

# ── Test 4: Fails on bad file permissions ────────────────────────────────────
echo "4. Bad file permissions"
tmpdir=$(mktemp -d)
setup_mock_env "$tmpdir"
# Set world-readable (bad)
chmod 644 "$tmpdir/.env"

output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
  bash "$PREFLIGHT" --ci 2>&1)
exit_code=$?
rm -rf "$tmpdir"

if [ "$exit_code" -ne 0 ]; then
    pass "Bad permissions detected (exit $exit_code)"
else
    fail "Bad permissions" "should have failed but exited 0"
fi
if echo "$output" | grep -q "FAIL.*Perms"; then
    pass "Error message mentions permissions"
else
    fail "Error message" "doesn't mention permissions"
fi

# ── Test 5: Fails on missing .env file ───────────────────────────────────────
echo "5. Missing .env file"
tmpdir=$(mktemp -d)
# Only create docker-compose.yml, no .env
cat > "$tmpdir/docker-compose.yml" <<'YAML'
services:
  test:
    image: alpine
YAML

output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
  bash "$PREFLIGHT" --ci 2>&1)
exit_code=$?
rm -rf "$tmpdir"

if [ "$exit_code" -ne 0 ]; then
    pass "Missing .env detected (exit $exit_code)"
else
    fail "Missing .env" "should have failed but exited 0"
fi

# ── Test 6: Detects compose env var not covered in .env ──────────────────────
echo "6. Uncovered compose env var"
tmpdir=$(mktemp -d)
setup_mock_env "$tmpdir"
# Add an extra env var to compose that isn't in .env
cat >> "$tmpdir/docker-compose.yml" <<'YAML'
      NEW_REQUIRED_KEY: ${NEW_REQUIRED_KEY:-}
YAML

output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
  bash "$PREFLIGHT" --ci 2>&1)
exit_code=$?
rm -rf "$tmpdir"

if [ "$exit_code" -ne 0 ]; then
    pass "Uncovered env var detected (exit $exit_code)"
else
    fail "Uncovered env var" "should have failed but exited 0"
fi
if echo "$output" | grep -q "FAIL.*NEW_REQUIRED_KEY"; then
    pass "Error message mentions NEW_REQUIRED_KEY"
else
    fail "Error message" "doesn't mention the uncovered key"
fi

# ── Test 7: --fix-hints shows fix commands ───────────────────────────────────
echo "7. Fix hints"
tmpdir=$(mktemp -d)
setup_mock_env "$tmpdir"
chmod 644 "$tmpdir/.env"  # Bad perms to trigger a failure with fix hint

output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
  bash "$PREFLIGHT" --fix-hints 2>&1)
rm -rf "$tmpdir"

if echo "$output" | grep -q "Fix:"; then
    pass "Fix hints displayed"
else
    fail "Fix hints" "no 'Fix:' lines in output"
fi

# ── Tests 8-10: SSH-unavailable paths ────────────────────────────────────────
# Inject a fake ssh that always exits 1 (simulates unreachable host).
# Run preflight with SKIP_RUNTIME=false — valid config + unreachable SSH
# should warn on checks 8-10 but NOT fail (exit 0).
make_fake_ssh() {
    local bin="$1"
    mkdir -p "$bin"
    cat > "$bin/ssh" <<'EOF'
#!/bin/bash
exit 1
EOF
    chmod +x "$bin/ssh"
}

echo "8. SSH unreachable: exit 0 with warnings only"
tmpdir=$(mktemp -d); fakebin=$(mktemp -d)
setup_mock_env "$tmpdir"; make_fake_ssh "$fakebin"
# OPENCLAW_GATEWAY_CONTAINER=nonexistent so checks 4/5 (docker inspect) skip gracefully
output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
    OPENCLAW_GATEWAY_CONTAINER=nonexistent-test-container \
    OPENCLAW_PREFLIGHT_SKIP_RUNTIME=false \
    PATH="$fakebin:$PATH" \
    bash "$PREFLIGHT" --ci 2>&1)
exit_code=$?
rm -rf "$tmpdir" "$fakebin"

if [ "$exit_code" -eq 0 ]; then
    pass "SSH-unreachable: exits 0 (warnings only)"
else
    fail "SSH-unreachable exit code" "exited $exit_code (expected 0)"
fi
if echo "$output" | grep -q "^  FAIL"; then
    fail "SSH-unreachable output" "contains FAIL lines (should be WARN only): $(echo "$output" | grep '^  FAIL')"
else
    pass "SSH-unreachable: no FAIL lines in output"
fi

echo "9. SSH unreachable: UFW warns unreachable, not fails"
tmpdir=$(mktemp -d); fakebin=$(mktemp -d)
setup_mock_env "$tmpdir"; make_fake_ssh "$fakebin"
# No --ci flag so warn() messages are printed (--ci suppresses them)
output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
    OPENCLAW_GATEWAY_CONTAINER=nonexistent-test-container \
    OPENCLAW_PREFLIGHT_SKIP_RUNTIME=false \
    PATH="$fakebin:$PATH" \
    bash "$PREFLIGHT" 2>&1)
rm -rf "$tmpdir" "$fakebin"

if echo "$output" | grep -qiE "WARN.*UFW.*unreachable|WARN.*UFW check.*unreachable|WARN.*UFW.*skip"; then
    pass "UFW: warns unreachable"
else
    fail "UFW unreachable" "expected WARN about UFW/unreachable, got: $(echo "$output" | grep -i ufw)"
fi
if echo "$output" | grep -qE "FAIL.*UFW"; then
    fail "UFW unreachable" "FAIL line found for UFW (should be WARN)"
else
    pass "UFW: no FAIL line"
fi

echo "10. SSH unreachable: Cloudflared warns unreachable, not fails"
tmpdir=$(mktemp -d); fakebin=$(mktemp -d)
setup_mock_env "$tmpdir"; make_fake_ssh "$fakebin"
# No --ci flag so warn() messages are printed (--ci suppresses them)
output=$(OPENCLAW_COMPOSE_DIR="$tmpdir" OPENCLAW_ENV_CLUSTER="$tmpdir/.env.cluster" \
    OPENCLAW_GATEWAY_CONTAINER=nonexistent-test-container \
    OPENCLAW_PREFLIGHT_SKIP_RUNTIME=false \
    PATH="$fakebin:$PATH" \
    bash "$PREFLIGHT" 2>&1)
rm -rf "$tmpdir" "$fakebin"

if echo "$output" | grep -qiE "WARN.*Cloudflared.*unreachable|WARN.*Cloudflared.*skip"; then
    pass "Cloudflared: warns unreachable"
else
    fail "Cloudflared unreachable" "expected WARN about Cloudflared/unreachable, got: $(echo "$output" | grep -i cloudflare)"
fi
if echo "$output" | grep -qE "FAIL.*Cloudflared"; then
    fail "Cloudflared unreachable" "FAIL line found for Cloudflared (should be WARN)"
else
    pass "Cloudflared: no FAIL line"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "=== Preflight Test Results ==="
echo "Passed: $PASS"
echo "Failed: $FAIL"
echo "Total:  $((PASS + FAIL))"

[ "$FAIL" -eq 0 ]
