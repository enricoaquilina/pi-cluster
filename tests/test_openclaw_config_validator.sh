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

# -- T7: unreadable config -> exit 3 + stderr names ownership --
# Regression guard for the 2026-04-09 finding: heavy had
# ~/.openclaw/openclaw.json owned by root:root from an earlier
# `sudo docker exec doctor --fix` incident. Pre-fix, the validator's
# cp silently failed, doctor ran against an empty mount, and the
# script cheerfully reported "config valid". That silent lie would
# have turned the #124 gate into a structural no-op once activated.
# Now: any config the validator can't read must exit 3, and the
# error message must name the owner so the operator knows the fix.
run_t7() {
    shims_init
    _install_docker_shim
    tmp=$(mktemp -d -t validator-perm.XXXXXX)
    cp "$FIXTURES/valid.json" "$tmp/openclaw.json"
    chmod 000 "$tmp/openclaw.json"
    out=$("$VALIDATOR" "$tmp/openclaw.json" 2>&1)
    rc=$?
    # Restore perms so the tmpdir rm-rf in shims_cleanup doesn't fail.
    chmod 600 "$tmp/openclaw.json"
    rm -rf "$tmp"
    if [ "$rc" -eq 3 ]; then
        pass "unreadable config exits 3 (not silent 'valid')"
    else
        fail "unreadable config exits 3 (not silent 'valid')" \
            "rc=$rc out=$out — this is the silent-failure regression"
    fi
    if echo "$out" | grep -qE "not readable|mode 000"; then
        pass "unreadable config error names the permission problem"
    else
        fail "unreadable config error names the permission problem" "out=$out"
    fi
    shims_cleanup
}

# -- T8: cp-to-workdir silent failure regression -----------------------
# Even if the upfront -r check is removed in the future, a cp failure
# must NOT be ignored. We simulate by pointing the validator at a
# path that exists, is readable, but can't be copied for a different
# reason — using a FIFO (named pipe) which will cause cp to hang or
# produce an empty file. The validator must detect the empty staged
# copy and refuse to proceed.
run_t8() {
    shims_init
    _install_docker_shim
    tmp=$(mktemp -d -t validator-empty.XXXXXX)
    # Create an empty file as the "config" — cp succeeds but produces
    # a 0-byte staged copy. The validator must catch this and exit 3
    # instead of letting docker doctor run against an empty JSON.
    : > "$tmp/openclaw.json"
    out=$("$VALIDATOR" "$tmp/openclaw.json" 2>&1)
    rc=$?
    rm -rf "$tmp"
    if [ "$rc" -eq 3 ]; then
        pass "empty config staged copy exits 3 (not silent 'valid')"
    else
        fail "empty config staged copy exits 3 (not silent 'valid')" \
            "rc=$rc out=$out"
    fi
    shims_cleanup
}

run_t1
run_t2
run_t3
run_t4
run_t5
run_t6
run_t7
run_t8

test_summary
