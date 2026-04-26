#!/bin/bash
# Checks: OpenClaw version consistency across nodes
# Catches: version drift when deploy misses a node

_VERSION_DATA=""

_fetch_version_data() {
    [ -n "$_VERSION_DATA" ] && return
    local vars_file="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}/../../vars/openclaw-nodes.yml"
    local pinned
    pinned=$(grep '^openclaw_version:' "$vars_file" 2>/dev/null | sed 's/.*"\(.*\)".*/\1/')
    if [ -z "$pinned" ]; then
        _VERSION_DATA="no_pin"
        return
    fi

    local result="pinned ${pinned}"$'\n'
    for host in heavy master slave0 slave1; do
        local ver
        if [ "$host" = "heavy" ]; then
            ver=$(openclaw --version 2>/dev/null | grep -oP '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
            if [ -z "$ver" ]; then
                ver=$(node /opt/openclaw/dist/index.js --version 2>/dev/null | grep -oP '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
            fi
        else
            ver=$(timed_ssh 8 "$host" "openclaw --version 2>/dev/null || node /opt/openclaw/dist/index.js --version 2>/dev/null" 2>/dev/null | grep -oP '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        fi

        if [ -z "$ver" ]; then
            result+="${host} ssh_failed -"$'\n'
        else
            result+="${host} ${ver} ok"$'\n'
        fi
    done

    _VERSION_DATA="${result%$'\n'}"
}

check_version_consistency() {
    _fetch_version_data

    if [ "$_VERSION_DATA" = "no_pin" ]; then
        check_service "version-consistency" "degraded" "Cannot read pinned version from vars"
        return
    fi

    local pinned
    pinned=$(echo "$_VERSION_DATA" | head -1 | awk '{print $2}')

    local mismatches="" ssh_fails="" all_ok=true
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        local host ver status
        host=$(echo "$line" | awk '{print $1}')
        ver=$(echo "$line" | awk '{print $2}')
        status=$(echo "$line" | awk '{print $3}')

        [ "$host" = "pinned" ] && continue

        if [ "$status" = "-" ]; then
            ssh_fails+="${host} "
            all_ok=false
        elif [ "$ver" != "$pinned" ]; then
            mismatches+="${host}:${ver} "
            all_ok=false
        fi
    done <<< "$_VERSION_DATA"

    if $all_ok; then
        check_service "version-consistency" "up"
    elif [ -n "$mismatches" ]; then
        check_service "version-consistency" "degraded" "Version mismatch (pinned ${pinned}): ${mismatches}"
    else
        check_service "version-consistency" "degraded" "Cannot check: SSH failed to ${ssh_fails}"
    fi
}
