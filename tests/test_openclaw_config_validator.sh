#!/bin/bash
# Tests for scripts/openclaw-config-validate.sh.
#
# The validator is a thin wrapper around `openclaw doctor` inside a throwaway
# container. Tests shim `docker` so we never need the real image here; the
# shim emits pre-canned doctor output and the validator parses it.
#
# Contract under test:
#   - exit 0 when the config is schema-valid (no "Config invalid" or
#     "required property" errors)
#   - exit 2 when the config is schema-invalid (lancedb missing embedding,
#     etc.) — stderr must name the problem
#   - exit 0 on legacy-key warnings only (they're warnings, not errors)
#   - exit 3 on invocation failure (docker missing, image missing, etc.)
#   - --quiet suppresses stdout but still writes errors to stderr and sets
#     the correct exit code

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VALIDATOR="$REPO_DIR/scripts/openclaw-config-validate.sh"
FIXTURES="$SCRIPT_DIR/fixtures/openclaw"

# shellcheck source=scripts/lib/test-harness.sh
source "$REPO_DIR/scripts/lib/test-harness.sh"
# shellcheck source=tests/lib/watchdog-shims.sh
source "$SCRIPT_DIR/lib/watchdog-shims.sh"

echo "=== openclaw-config-validate.sh ==="

# Install a docker shim that inspects the bind-mount target and emits canned
# doctor output depending on which fixture the validator mounted. We parse
# the `-v <host>:/home/node/.openclaw:ro` flag the validator is expected to
# use.
_install_docker_shim() {
    shims_set_script docker <<'BASH'
# Parse the -v host:/home/node/.openclaw:ro argument to find the workdir
# the validator copied the config into. Then read its openclaw.json and
# decide which canned doctor output to produce based on plugin content.
host_path=""
while [ $# -gt 0 ]; do
    case "$1" in
        -v) shift; host_path="${1%%:*}" ;;
    esac
    shift || true
done

config_file="$host_path/openclaw.json"
if [ ! -f "$config_file" ]; then
    echo "shim: no openclaw.json at $host_path" >&2
    exit 99
fi

if grep -q '"memory-lancedb"' "$config_file"; then
    echo "┌  OpenClaw doctor" >&2
    echo "Config invalid" >&2
    echo "File: ~/.openclaw/openclaw.json" >&2
    echo "Problem:" >&2
    echo "  - plugins.entries.memory-lancedb.config.embedding: invalid config: must have required property 'embedding'" >&2
    exit 1
fi

if grep -q '"streamMode"' "$config_file"; then
    echo "┌  OpenClaw doctor"
    echo "│  Config OK"
    echo "│  Legacy config keys detected:" >&2
    echo "│    - channels.telegram: streamMode, chunkMode, blockStreaming are legacy; use channels.telegram.streaming.{mode,chunkMode,preview.chunk,block.enabled}" >&2
    echo "│  Run \"openclaw doctor --fix\"." >&2
    exit 0
fi

echo "┌  OpenClaw doctor"
echo "│  Config OK"
echo "│  Gateway token configured."
exit 0
BASH
}

# -- T1: valid config -> exit 0 --
run_t1() {
    shims_init
    _install_docker_shim
    # Validator is expected to run `docker run --rm -v <path>:/home/node/.openclaw:ro ... openclaw doctor`
    if out=$("$VALIDATOR" "$FIXTURES/valid.json" 2>&1); then
        pass "valid config exits 0"
    else
        fail "valid config exits 0" "actual exit=$?, output: $out"
    fi
    shims_cleanup
}

# -- T2: invalid lancedb -> exit 2 with informative stderr --
run_t2() {
    shims_init
    _install_docker_shim
    out=$("$VALIDATOR" "$FIXTURES/invalid-lancedb.json" 2>&1)
    rc=$?
    if [ "$rc" -eq 2 ]; then
        pass "invalid-lancedb exits 2"
    else
        fail "invalid-lancedb exits 2" "actual exit=$rc, output: $out"
    fi
    if echo "$out" | grep -q "memory-lancedb"; then
        pass "invalid-lancedb stderr names memory-lancedb"
    else
        fail "invalid-lancedb stderr names memory-lancedb" "output: $out"
    fi
    shims_cleanup
}

# -- T3: legacy telegram warnings -> exit 0 (warnings are not errors) --
run_t3() {
    shims_init
    _install_docker_shim
    out=$("$VALIDATOR" "$FIXTURES/legacy-telegram.json" 2>&1)
    rc=$?
    if [ "$rc" -eq 0 ]; then
        pass "legacy-telegram exits 0 (warn, not fail)"
    else
        fail "legacy-telegram exits 0" "actual exit=$rc, output: $out"
    fi
    shims_cleanup
}

# -- T4: --quiet suppresses stdout but still sets exit code --
run_t4() {
    shims_init
    _install_docker_shim
    stdout=$("$VALIDATOR" --quiet "$FIXTURES/invalid-lancedb.json" 2>/dev/null)
    rc=$?
    if [ "$rc" -eq 2 ] && [ -z "$stdout" ]; then
        pass "--quiet suppresses stdout and exits 2 on invalid"
    else
        fail "--quiet suppresses stdout and exits 2 on invalid" "rc=$rc stdout='$stdout'"
    fi
    shims_cleanup
}

# -- T5: nonexistent config path -> exit 3 --
run_t5() {
    shims_init
    _install_docker_shim
    out=$("$VALIDATOR" "$FIXTURES/nope.json" 2>&1)
    rc=$?
    if [ "$rc" -eq 3 ]; then
        pass "missing config path exits 3"
    else
        fail "missing config path exits 3" "actual exit=$rc, output: $out"
    fi
    shims_cleanup
}

# -- T6: OPENCLAW_VALIDATE_CMD hook -> delegates to the injected command --
run_t6() {
    shims_init
    # Inject a deterministic command that returns exit 2.
    cat > "$SHIM_BIN/canned-invalid" <<'BASH'
#!/bin/bash
echo "injected: invalid" >&2
exit 2
BASH
    chmod +x "$SHIM_BIN/canned-invalid"
    OPENCLAW_VALIDATE_CMD="$SHIM_BIN/canned-invalid" "$VALIDATOR" "$FIXTURES/valid.json" 2>/dev/null
    rc=$?
    if [ "$rc" -eq 2 ]; then
        pass "OPENCLAW_VALIDATE_CMD delegation respects exit code"
    else
        fail "OPENCLAW_VALIDATE_CMD delegation respects exit code" "rc=$rc"
    fi
    shims_cleanup
}

run_t1
run_t2
run_t3
run_t4
run_t5
run_t6

test_summary
