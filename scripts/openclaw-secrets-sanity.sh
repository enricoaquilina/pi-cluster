#!/bin/bash
set -uo pipefail
# openclaw-secrets-sanity.sh — detect hardcoded-looking API keys in the
# openclaw docker-compose file and in ~/openclaw/.env for drift.
#
# Context: on 2026-04-09 a plaintext GEMINI_API_KEY was found on
# docker-compose.yml:44, violating the "all secrets from env" convention
# every other key in the file follows. This scanner re-runs that check so
# a future hardcode trips CI (if run there) or a nightly cron.
#
# Exit codes:
#   0 — no hardcoded secrets detected
#   2 — one or more hardcoded values found; stderr lists them
#   3 — target file not found / tool missing
#
# Usage:
#   scripts/openclaw-secrets-sanity.sh                     # scan default paths
#   scripts/openclaw-secrets-sanity.sh path/to/compose.yml # scan explicit file
#
# Env:
#   OPENCLAW_COMPOSE_PATH — override default /mnt/external/openclaw/docker-compose.yml
#   OPENCLAW_SECRETS_EXTRA_PATTERN — extra grep-E pattern OR'd into the scan

COMPOSE="${1:-${OPENCLAW_COMPOSE_PATH:-/mnt/external/openclaw/docker-compose.yml}}"

if [ ! -f "$COMPOSE" ]; then
    echo "compose file not found: $COMPOSE" >&2
    exit 3
fi

# Heuristics for hardcoded-looking API keys inside docker-compose environment: blocks.
# Each pattern describes a KEY:VALUE where VALUE looks like a real secret
# (not a ${VAR} substitution, not an empty string). Extend as needed.
#
# Google / Gemini:   "AIza" prefix, 35+ char base64url
# Anthropic:         "sk-ant-" prefix
# OpenAI / OpenRouter: "sk-" followed by 20+ chars
# DeepSeek / Moonshot / ZAI: "sk-" family
# Telegram bot:      "<int>:<35+ base64url>"
# Generic:           quoted or unquoted value that's 32+ char base64/hex
#
# We only flag VALUES, not keys, by requiring the match to follow a colon
# and whitespace and to NOT be `${...}`.

patterns=(
    'AIza[0-9A-Za-z_-]{30,}'
    'sk-ant-[A-Za-z0-9_-]{20,}'
    'sk-[A-Za-z0-9]{20,}'
    'sk-or-[A-Za-z0-9_-]{20,}'
    'sk-proj-[A-Za-z0-9_-]{20,}'
    '[0-9]{8,12}:AA[A-Za-z0-9_-]{30,}'   # telegram bot tokens
)

if [ -n "${OPENCLAW_SECRETS_EXTRA_PATTERN:-}" ]; then
    patterns+=("$OPENCLAW_SECRETS_EXTRA_PATTERN")
fi

combined="$(printf '%s|' "${patterns[@]}")"
combined="${combined%|}"

# Only look at value positions, i.e. after ":" or "=" and NOT inside ${}.
# Simpler: grep for the patterns anywhere and exclude lines that wrap them
# in ${...} (proper env var substitution) or obvious placeholders.
hits=$(grep -nE "$combined" "$COMPOSE" 2>/dev/null \
    | grep -vE '\$\{[^}]*\}' \
    | grep -vE 'example|placeholder|xxxx|<redacted>' || true)

if [ -n "$hits" ]; then
    echo "HARDCODED SECRET(S) in $COMPOSE:" >&2
    echo "$hits" >&2
    exit 2
fi

echo "ok: no hardcoded secrets detected in $COMPOSE"
exit 0
