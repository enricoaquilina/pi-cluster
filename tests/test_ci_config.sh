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

# Test 11: pr_actions includes synchronize (fix for force-push review skip)
if grep -q 'synchronize' .pr_agent.toml; then
  ok "pr_actions includes synchronize"
else
  fail "pr_actions missing synchronize — force-push reviews will be skipped"
fi

# Test 12: ticket compliance disabled (removes noise)
if grep -P -q '^require_ticket_analysis_review\s*=\s*false' .pr_agent.toml; then
  ok "ticket compliance disabled"
else
  fail "require_ticket_analysis_review not set to false"
fi

# Test 13: auto_improve explicitly enabled
if grep -q 'auto_improve.*=.*true' .pr_agent.toml; then
  ok "auto_improve explicitly enabled"
else
  fail "auto_improve not explicitly set to true"
fi

# Test 14: best_practices.md contains key enforcement patterns
for pattern in "&&" "||" "grep -E" "testpassword" "503" "psycopg2"; do
  if grep -q "$pattern" .github/best_practices.md; then
    ok "best_practices.md documents '$pattern' pattern"
  else
    fail "best_practices.md missing '$pattern' pattern"
  fi
done

# Test 15: codex-review.yml exists and uses OpenRouter
if [[ -f ".github/workflows/codex-review.yml" ]]; then
  ok "codex-review.yml exists"
  if grep -q 'openrouter.ai/api/v1' .github/workflows/codex-review.yml; then
    ok "codex-review.yml uses OpenRouter API"
  else
    fail "codex-review.yml not using OpenRouter"
  fi
  if grep -q 'openai/gpt-5.4' .github/workflows/codex-review.yml; then
    ok "codex-review.yml targets GPT-5.4"
  else
    fail "codex-review.yml not targeting GPT-5.4"
  fi
else
  fail "codex-review.yml missing"
fi

# Test 16: push-policy-check.yml exists with Telegram alert
if [[ -f ".github/workflows/push-policy-check.yml" ]]; then
  ok "push-policy-check.yml exists"
  if grep -q 'TELEGRAM_BOT_TOKEN' .github/workflows/push-policy-check.yml; then
    ok "push-policy-check.yml has Telegram alert"
  else
    fail "push-policy-check.yml missing Telegram alert"
  fi
else
  fail "push-policy-check.yml missing"
fi

# Test 17: pr-size-check.yml exists
if [[ -f ".github/workflows/pr-size-check.yml" ]]; then
  ok "pr-size-check.yml exists"
else
  fail "pr-size-check.yml missing"
fi

# Test 18: enable-automerge.yml enables auto-merge for all PRs
if ! grep -q "startsWith.*feat" .github/workflows/enable-automerge.yml; then
  ok "enable-automerge.yml has no feat: exclusion (full auto-merge)"
else
  fail "enable-automerge.yml still has feat: gate (should be removed)"
fi

# Test 19: pre-push hook has push policy
if [[ -f "hooks/pre-push" ]]; then
  if grep -q 'pushing_to_master' hooks/pre-push; then
    ok "pre-push hook has push policy enforcement"
  else
    fail "pre-push hook missing push policy"
  fi
else
  fail "hooks/pre-push missing"
fi

# Test 20: CLAUDE.md exists with push policy docs
if [[ -f "CLAUDE.md" ]]; then
  if grep -q 'Push Policy' CLAUDE.md; then
    ok "CLAUDE.md documents push policy"
  else
    fail "CLAUDE.md missing push policy section"
  fi
else
  fail "CLAUDE.md missing"
fi

# Test 21: claude-fix.yml prompts instruct "Do NOT push"
no_push_count=$(grep -c 'Do NOT push' .github/workflows/claude-fix.yml 2>/dev/null || echo "0")
if [[ "$no_push_count" -ge 4 ]]; then
  ok "claude-fix.yml prompts all say 'Do NOT push' (test gate enforced)"
else
  fail "claude-fix.yml missing 'Do NOT push' in prompts (found $no_push_count, expected 4)"
fi

# Test 22: No YAML lines > 200 chars (ansible-lint enforces this)
long_lines=$(awk 'length > 200 {print FILENAME":"NR": "length" chars"}' .github/workflows/*.yml 2>/dev/null || true)
if [[ -z "$long_lines" ]]; then
  ok "No workflow YAML lines exceed 200 chars"
else
  fail "Lines > 200 chars (ansible-lint will fail): $long_lines"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
