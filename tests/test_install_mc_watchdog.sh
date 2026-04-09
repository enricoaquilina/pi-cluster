#!/bin/bash
# Tests for scripts/install-mc-watchdog.sh.
#
# The primary regression guarded here is the "gate went silent" class:
# before PR #129, this installer deployed the watchdog but NOT the
# validator, so the watchdog's DEFAULT_VALIDATOR resolution found
# nothing and the #124 config-validation gate was a silent no-op in
# production. These tests verify that after running the installer:
#   1. The watchdog is deposited at the expected path.
#   2. The validator is deposited at the watchdog's DEFAULT_VALIDATOR
#      path — i.e. computed the same way the watchdog computes it.
#   3. The installer emits the "ACTIVATED" line on fresh installs and
#      the "already active" line on re-runs.
#   4. A drifted basename (validator renamed in source) causes the
#      post-install sanity check to fail loudly instead of succeeding
#      and leaving the gate dormant.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALLER="$REPO_DIR/scripts/install-mc-watchdog.sh"
WATCHDOG_SRC="$REPO_DIR/scripts/mission-control-watchdog.sh"

# shellcheck source=scripts/lib/test-harness.sh
source "$REPO_DIR/scripts/lib/test-harness.sh"

echo "=== install-mc-watchdog.sh ==="

TMP=$(mktemp -d -t install-mcwd.XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# --- Reproduce the watchdog's own DEFAULT_VALIDATOR computation -------
# This is the exact line we're defending: if someone renames the
# validator source or changes the default basename in the watchdog,
# this test must catch the drift. We parse the filename out of the
# source and compare it against the installer's target.
watchdog_default_basename=$(
    grep -E '^DEFAULT_VALIDATOR=' "$WATCHDOG_SRC" \
        | head -n1 \
        | sed -E 's|.*/([^/"]+)".*|\1|'
)
if [ -z "$watchdog_default_basename" ]; then
    fail "parse DEFAULT_VALIDATOR basename from watchdog source" \
        "no DEFAULT_VALIDATOR line found in $WATCHDOG_SRC"
else
    pass "parse DEFAULT_VALIDATOR basename from watchdog source ($watchdog_default_basename)"
fi

# --- Case 1: fresh install -> both files present, ACTIVATED line -----
case1_dir="$TMP/case1-bin"
mkdir -p "$case1_dir"
out_file="$TMP/case1.out"
MC_WATCHDOG_INSTALL_PATH="$case1_dir/mission-control-watchdog" \
    "$INSTALLER" >"$out_file" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
    pass "fresh install exits 0"
else
    fail "fresh install exits 0" "rc=$rc out=$(cat "$out_file")"
fi

if [ -x "$case1_dir/mission-control-watchdog" ]; then
    pass "fresh install deposits watchdog"
else
    fail "fresh install deposits watchdog" "not present at $case1_dir/mission-control-watchdog"
fi

# THIS is the assertion that would have caught the pre-PR-129 bug:
# validator must live alongside the watchdog under the exact basename
# the watchdog's runtime resolution will compute.
if [ -x "$case1_dir/$watchdog_default_basename" ]; then
    pass "fresh install deposits validator at watchdog's DEFAULT_VALIDATOR path"
else
    fail "fresh install deposits validator at watchdog's DEFAULT_VALIDATOR path" \
        "expected executable at $case1_dir/$watchdog_default_basename"
fi

if grep -q "ACTIVATED: PR #124" "$out_file"; then
    pass "fresh install logs ACTIVATED line"
else
    fail "fresh install logs ACTIVATED line" "out=$(cat "$out_file")"
fi

# --- Case 2: re-run on same directory -> idempotent, no ACTIVATED ----
out_file="$TMP/case2.out"
MC_WATCHDOG_INSTALL_PATH="$case1_dir/mission-control-watchdog" \
    "$INSTALLER" >"$out_file" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
    pass "re-run exits 0"
else
    fail "re-run exits 0" "rc=$rc out=$(cat "$out_file")"
fi
if grep -q "already active" "$out_file"; then
    pass "re-run logs 'already active' (not a second ACTIVATED)"
else
    fail "re-run logs 'already active'" "out=$(cat "$out_file")"
fi
if grep -q "ACTIVATED: PR #124" "$out_file"; then
    fail "re-run must NOT re-log ACTIVATED" "out=$(cat "$out_file")"
else
    pass "re-run does not re-log ACTIVATED"
fi

# --- Case 3: watchdog source drift -> installer fails loudly ---------
# Simulate someone renaming the validator in a future commit without
# updating the installer. We fake this by pointing the installer at a
# stubbed SCRIPT_DIR where the validator source is absent.
drift_dir="$TMP/drift-src"
mkdir -p "$drift_dir"
cp "$WATCHDOG_SRC" "$drift_dir/mission-control-watchdog.sh"
# Deliberately DO NOT copy the validator source — this is the drift.
cat > "$drift_dir/install-mc-watchdog.sh" <<EOF
#!/bin/bash
# stub that re-sources the real installer but with SCRIPT_DIR pinned
# to the drift dir (no validator source present)
exec env MC_WATCHDOG_INSTALL_PATH="$TMP/case3-bin/mission-control-watchdog" \\
    bash -c 'cd "$drift_dir" && exec "$INSTALLER"'
EOF
chmod +x "$drift_dir/install-mc-watchdog.sh"
# Simpler: just run the installer directly with BASH_SOURCE pointed at
# the drift dir. The installer computes SCRIPT_DIR from BASH_SOURCE[0],
# which is the symlink/path it was invoked as. We can fake the source
# dir by creating a symlink there.
ln -sf "$INSTALLER" "$drift_dir/install-mc-watchdog-stub.sh"
out_file="$TMP/case3.out"
mkdir -p "$TMP/case3-bin"
MC_WATCHDOG_INSTALL_PATH="$TMP/case3-bin/mission-control-watchdog" \
    bash "$drift_dir/install-mc-watchdog-stub.sh" >"$out_file" 2>&1
rc=$?
# With the validator source absent in the drift dir, preflight should
# catch it and exit 1 with "validator source missing".
if [ "$rc" -eq 1 ]; then
    pass "validator source drift causes install to fail"
else
    fail "validator source drift causes install to fail" \
        "rc=$rc out=$(cat "$out_file")"
fi
if grep -q "validator source missing" "$out_file"; then
    pass "validator source drift produces clear error message"
else
    fail "validator source drift produces clear error message" \
        "out=$(cat "$out_file")"
fi

test_summary
