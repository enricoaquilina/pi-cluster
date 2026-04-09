#!/bin/bash
# Tests for scripts/openclaw-secrets-sanity.sh.
#
# Regression guard for the 2026-04-09 hardcoded GEMINI_API_KEY finding:
# any future compose edit that bakes in a plaintext API key must trip
# this scanner.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SCANNER="$REPO_DIR/scripts/openclaw-secrets-sanity.sh"

# shellcheck source=scripts/lib/test-harness.sh
source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== openclaw-secrets-sanity.sh ==="

TMP=$(mktemp -d -t secrets-sanity.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# -- Fixture 1: clean compose (all env-substituted) --
cat > "$TMP/clean.yml" <<'YAML'
services:
  gw:
    image: example
    environment:
      GEMINI_API_KEY: ${GEMINI_API_KEY:-}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}
YAML

if "$SCANNER" "$TMP/clean.yml" >/tmp/secrets-scan-clean.out 2>&1; then
    pass "clean compose exits 0"
else
    fail "clean compose exits 0" "rc=$?, out: $(cat /tmp/secrets-scan-clean.out)"
fi

# -- Fixture 2: hardcoded Google/Gemini-shaped key --
# Synthetic (not a real API key): AIza prefix + 35 chars of test-only
# pattern, built at runtime so gitleaks / secret scanners don't flag
# this file. The scanner being tested only cares about the regex shape,
# not whether the key is real.
FAKE_GOOGLE_KEY="AIza"
FAKE_GOOGLE_KEY+="Sy"
FAKE_GOOGLE_KEY+=$(printf 'FAKETESTFIXTURE_%.0s' 1 2)
FAKE_GOOGLE_KEY+="1234567890"
cat > "$TMP/hardcoded-gemini.yml" <<YAML
services:
  gw:
    image: example
    environment:
      GEMINI_API_KEY: ${FAKE_GOOGLE_KEY}
      ANTHROPIC_API_KEY: \${ANTHROPIC_API_KEY:-}
YAML

"$SCANNER" "$TMP/hardcoded-gemini.yml" >/tmp/secrets-scan-gemini.out 2>&1
rc=$?
if [ "$rc" -eq 2 ]; then
    pass "hardcoded Gemini key exits 2"
else
    fail "hardcoded Gemini key exits 2" "rc=$rc"
fi
if grep -q "AIza" /tmp/secrets-scan-gemini.out; then
    pass "scanner output names the offending key"
else
    fail "scanner output names the offending key" "$(cat /tmp/secrets-scan-gemini.out)"
fi

# -- Fixture 3: hardcoded Anthropic-shaped key (synthetic) --
FAKE_ANTHROPIC_KEY="sk-"
FAKE_ANTHROPIC_KEY+="ant-"
FAKE_ANTHROPIC_KEY+="api03-"
FAKE_ANTHROPIC_KEY+="FAKETESTFIXTURE_abcdefghijklmnopqrstuv"
cat > "$TMP/hardcoded-anthropic.yml" <<YAML
services:
  gw:
    environment:
      ANTHROPIC_API_KEY: ${FAKE_ANTHROPIC_KEY}
YAML

"$SCANNER" "$TMP/hardcoded-anthropic.yml" >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 2 ]; then
    pass "hardcoded Anthropic key exits 2"
else
    fail "hardcoded Anthropic key exits 2" "rc=$rc"
fi

# -- Fixture 4: nonexistent path -> exit 3 --
"$SCANNER" "$TMP/does-not-exist.yml" >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 3 ]; then
    pass "missing compose exits 3"
else
    fail "missing compose exits 3" "rc=$rc"
fi

# -- Fixture 5: example placeholders are allowed --
cat > "$TMP/placeholders.yml" <<'YAML'
services:
  gw:
    environment:
      # example key: AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
      GEMINI_API_KEY: ${GEMINI_API_KEY:-}
YAML

if "$SCANNER" "$TMP/placeholders.yml" >/dev/null 2>&1; then
    pass "example placeholders in comments allowed"
else
    fail "example placeholders in comments allowed" "rc=$?"
fi

test_summary
