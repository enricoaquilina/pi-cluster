#!/usr/bin/env bash
# CI configuration quality tests
# Run from repo root: bash tests/test_ci_config.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PASS=0
FAIL=0
ok()   { echo "PASS [$((++PASS))]: $1"; }
fail() { echo "FAIL [$((++FAIL))]: $1"; }

# Test 1: No mutable action tags (@main/@latest/@master) in uses: lines
mutable=$(grep -rE '^\s+uses:\s+.*@(main|latest|master)' .github/workflows/ 2>/dev/null || true)
if [[ -z "$mutable" ]]; then
  ok "No mutable action tags"
else
  fail "Mutable action tags found: $mutable"
fi

# Test 2: No hardcoded 'testpassword' in workflows
hardcoded=$(grep -rn 'testpassword' .github/workflows/ 2>/dev/null || true)
if [[ -z "$hardcoded" ]]; then
  ok "No hardcoded testpassword in workflows"
else
  fail "Hardcoded testpassword found: $hardcoded"
fi

# Test 3: .pr_agent.toml exists
if [[ ! -f ".pr_agent.toml" ]]; then
  fail ".pr_agent.toml missing"
else
  ok ".pr_agent.toml exists"

  # Test 4: valid TOML syntax
  if python3 -c "
import tomllib
with open('.pr_agent.toml', 'rb') as f:
    tomllib.load(f)
" 2>/dev/null; then
    ok ".pr_agent.toml valid TOML syntax"
  else
    fail ".pr_agent.toml has TOML syntax errors"
  fi

  # Test 5: has [config] section
  if grep -qE '^\[config\]' .pr_agent.toml; then
    ok ".pr_agent.toml has [config] section"
  else
    fail ".pr_agent.toml missing [config] section"
  fi

  # Test 6: base model is set
  if grep -qE '^model\s*=' .pr_agent.toml; then
    ok ".pr_agent.toml has model = (base model)"
  else
    fail ".pr_agent.toml missing base model ="
  fi

  # Test 7: model is NOT MiniMax
  if grep -qiE '^model\s*=.*minimax' .pr_agent.toml; then
    fail "pr_agent.toml still uses MiniMax model"
  else
    ok "pr_agent.toml model is not MiniMax"
  fi

  # Test 8: best_practices_path configured
  if grep -q 'best_practices_path' .pr_agent.toml; then
    ok "best_practices_path configured in .pr_agent.toml"
  else
    fail "best_practices_path not set in .pr_agent.toml"
  fi
fi

# Test 9: .github/best_practices.md exists and non-empty
if [[ -s ".github/best_practices.md" ]]; then
  ok ".github/best_practices.md exists and non-empty"
else
  fail ".github/best_practices.md missing or empty"
fi

# Test 10: pr-review.yml inline config.model removed (now in TOML)
if grep -q 'config\.model' .github/workflows/pr-review.yml; then
  fail "pr-review.yml still has inline config.model (move to .pr_agent.toml)"
else
  ok "pr-review.yml inline config.model removed"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
