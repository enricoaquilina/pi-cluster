#!/bin/bash
# Post GitHub comment on PR that caused a deploy failure.
# Requires GITHUB_PAT in .env.cluster (fine-grained PAT, issues:write scope).

deploy_notify_pr() {
    local commit_msg="$1" body="$2"
    local repo="enricoaquilina/pi-cluster"

    [ -z "${GITHUB_PAT:-}" ] && return 0

    local pr_number
    pr_number=$(echo "$commit_msg" | grep -oP '\(#\K[0-9]+(?=\))' | tail -1)
    [ -z "$pr_number" ] && return 0

    curl -sf --max-time 10 \
        -X POST "https://api.github.com/repos/${repo}/issues/${pr_number}/comments" \
        -H "Authorization: token ${GITHUB_PAT}" \
        -H "Accept: application/vnd.github+json" \
        -d "{\"body\":$(printf '%s' "$body" | jq -Rs .)}" \
        >/dev/null 2>&1 || true
}
