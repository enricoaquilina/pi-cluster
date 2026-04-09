#!/bin/bash
# render-openclaw-config.sh — render openclaw.json from its checked-in
# template plus an env file, substituting ONLY the explicit allowlist
# of variables.
set -euo pipefail
#
# Why this exists: before PR #132, `~/.openclaw/openclaw.json` was the
# sole copy and it drifted in three problematic ways over time —
#   1. 2026-04-07 incident: `sudo docker exec doctor --fix` overwrote
#      it as root:root because the bind mount wasn't :ro.
#   2. Secret `channels.telegram.botToken` was embedded directly,
#      with no mechanism to rotate it without hand-editing JSON.
#   3. No git history on a 23kB JSON file meant any change (intentional
#      or accidental) was invisible to review.
#
# After this script: the canonical config is the template under
# pi-cluster/configs/openclaw/, rendered deterministically via this
# script into whatever location the operator (or install script)
# chooses. Secrets live only in $ENV_FILE (~/openclaw/.env by default).
#
# Substitution uses `envsubst` with an EXPLICIT ALLOWLIST of variables
# (`$KNOWN_VARS`). Any `${...}` reference in the template that isn't
# on the allowlist is passed through literally. This prevents stray
# dollar-brace sequences in user-facing config strings (model prompts,
# command names, etc.) from getting silently expanded at render time.
#
# Exit codes:
#   0 — rendered successfully AND (unless --no-validate) schema-valid
#   2 — rendered output failed schema validation
#   3 — invocation failure (missing inputs, unwritable output, etc.)
#
# Usage:
#   render-openclaw-config.sh                     # render to stdout
#   render-openclaw-config.sh -o <path>           # render to file
#   render-openclaw-config.sh --no-validate       # skip schema check
#   render-openclaw-config.sh --env <path>        # override env file
#   render-openclaw-config.sh --template <path>   # override template

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TEMPLATE="${OPENCLAW_CONFIG_TEMPLATE:-$REPO_DIR/configs/openclaw/openclaw.json.template}"
ENV_FILE="${OPENCLAW_CONFIG_ENV_FILE:-$HOME/openclaw/.env}"
OUTPUT=""
VALIDATE=true
VALIDATOR="${OPENCLAW_VALIDATOR:-$SCRIPT_DIR/openclaw-config-validate.sh}"

# Allowlist of variables permitted for substitution. Add new entries
# here (and to the template) when the canonical config grows a new
# secret reference. Everything NOT listed here is passed through
# literally, including stray `${foo}` strings in prompts or examples.
KNOWN_VARS='${TELEGRAM_BOT_TOKEN}'

log_err() { echo "$*" >&2; }

while [ $# -gt 0 ]; do
    case "$1" in
        -o|--output) shift; OUTPUT="${1:-}"; shift ;;
        --env) shift; ENV_FILE="${1:-}"; shift ;;
        --template) shift; TEMPLATE="${1:-}"; shift ;;
        --no-validate) VALIDATE=false; shift ;;
        -h|--help)
            grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0
            ;;
        *) log_err "unknown flag: $1"; exit 3 ;;
    esac
done

if ! command -v envsubst >/dev/null 2>&1; then
    log_err "envsubst not found on PATH (install gettext-base)"
    exit 3
fi
if [ ! -f "$TEMPLATE" ]; then
    log_err "template not found: $TEMPLATE"
    exit 3
fi
if [ ! -r "$TEMPLATE" ]; then
    log_err "template not readable: $TEMPLATE"
    exit 3
fi
if [ ! -f "$ENV_FILE" ]; then
    log_err "env file not found: $ENV_FILE"
    exit 3
fi
if [ ! -r "$ENV_FILE" ]; then
    log_err "env file not readable: $ENV_FILE"
    exit 3
fi

# Load env vars from $ENV_FILE without executing shell code in them.
# We parse KEY=VALUE lines, strip surrounding quotes, and export each
# variable into the environment so envsubst can pick it up. This is
# deliberately NOT `source "$ENV_FILE"` — that would execute any shell
# metacharacters in values, which we never want for a secrets file.
_loaded_any_allowlisted=false
while IFS= read -r raw || [ -n "$raw" ]; do
    # Strip leading/trailing whitespace
    line="${raw#"${raw%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    # Skip blanks and comments
    [ -z "$line" ] && continue
    case "$line" in \#*) continue ;; esac
    # Only handle simple KEY=VALUE
    case "$line" in
        *=*) : ;;
        *) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    # Reject keys that don't look like identifiers
    case "$key" in
        [A-Za-z_][A-Za-z_0-9]*) : ;;
        *) continue ;;
    esac
    # Strip one layer of matching surrounding quotes on the value
    case "$val" in
        \"*\") val="${val#\"}"; val="${val%\"}" ;;
        \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    # Only export variables that appear in the allowlist. Everything
    # else is ignored — we don't want the entire .env contaminating
    # the render-script's environment.
    case "$KNOWN_VARS" in
        *'${'"$key"'}'*)
            export "$key=$val"
            _loaded_any_allowlisted=true
            ;;
    esac
done < "$ENV_FILE"

if ! $_loaded_any_allowlisted; then
    log_err "warning: no allowlisted variables found in $ENV_FILE"
    log_err "         allowlist: $KNOWN_VARS"
    log_err "         rendered output will leave placeholders literal"
fi

# Render. `envsubst "$KNOWN_VARS"` is the critical invocation: the
# positional arg to envsubst is a whitelist of which variables to
# substitute. Unlisted `${...}` references are passed through as-is.
if [ -n "$OUTPUT" ]; then
    tmp="$(mktemp -t openclaw-render.XXXXXX)"
    trap 'rm -f "$tmp"' EXIT
    if ! envsubst "$KNOWN_VARS" < "$TEMPLATE" > "$tmp"; then
        log_err "envsubst failed"
        exit 3
    fi
    # Sanity: the rendered file must still parse as JSON.
    if command -v jq >/dev/null 2>&1; then
        if ! jq -e . "$tmp" >/dev/null 2>&1; then
            log_err "rendered output is not valid JSON"
            exit 3
        fi
    fi
    # Validation (optional).
    if $VALIDATE; then
        if [ ! -x "$VALIDATOR" ]; then
            log_err "validator not executable: $VALIDATOR"
            exit 3
        fi
        if ! "$VALIDATOR" --quiet "$tmp"; then
            rc=$?
            log_err "rendered config failed validation (rc=$rc)"
            "$VALIDATOR" "$tmp" >&2 || true
            exit 2
        fi
    fi
    # Atomic install of the rendered file.
    mkdir -p "$(dirname "$OUTPUT")"
    mv "$tmp" "$OUTPUT"
    chmod 600 "$OUTPUT"
    trap - EXIT
    echo "rendered: $OUTPUT"
else
    # Stream to stdout. Validation is skipped in this mode because
    # there's no file to hand to the validator.
    envsubst "$KNOWN_VARS" < "$TEMPLATE"
fi
