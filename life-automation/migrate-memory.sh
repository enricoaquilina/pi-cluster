#!/bin/bash
set -euo pipefail

readonly LIFE="$HOME/life"
readonly MEM="$HOME/.claude/projects/-home-enrico/memory"

copy_with_frontmatter() {
    local src="$1" dst="$2" ftype="$3" fname="$4"
    [[ -f "$dst" ]] && { echo "[migrate] exists: $dst"; return; }
    {
        printf -- '---\ntype: %s\nname: %s\ncreated: %s\nlast-updated: %s\nstatus: active\n---\n\n' \
            "$ftype" "$fname" "$(date +%Y-%m-%d)" "$(date +%Y-%m-%d)"
        # Strip existing frontmatter from source if present
        awk '/^---$/{if(++n==2){found=1;next}} found{print}' "$src"
    } > "$dst"
    echo "[migrate] created: $dst"
}

# pi-cluster summary
copy_with_frontmatter \
    "$MEM/project_pi_cluster.md" \
    "$LIFE/Projects/pi-cluster/summary.md" \
    "project" "Pi Cluster"

# cloudflare resource
copy_with_frontmatter \
    "$MEM/reference_cf_access_setup.md" \
    "$LIFE/Resources/cloudflare/summary.md" \
    "resource" "Cloudflare"

# pi-cluster items.json (manually structured from infra improvements narrative)
if [[ ! -f "$LIFE/Projects/pi-cluster/items.json" ]]; then
    cat > "$LIFE/Projects/pi-cluster/items.json" <<'EOF'
[
  {"date":"2026-03-27","fact":"Heavy /home/enrico/pi-cluster/ is a SEPARATE git clone (not NFS). Master repo is /home/enrico/homelab/. NFS mount is at /opt/workspace/.","category":"configuration","source":"memory/project_infra_improvements","confidence":"confirmed"},
  {"date":"2026-03-27","fact":"MC deployed from /home/enrico/mission-control/ (3rd copy, manually maintained). Symlink to pi-cluster clone eliminates drift. Phase 1a VALIDATED 7/7.","category":"deployment","source":"memory/project_infra_improvements","confidence":"confirmed"},
  {"date":"2026-03-27","fact":"auto-deploy.sh runs on MASTER, not heavy. Needs SSH to heavy for MC rebuild.","category":"configuration","source":"memory/project_infra_improvements","confidence":"confirmed"},
  {"date":"2026-03-27","fact":"Gateway CLI channels status takes 16+ seconds — too slow for testing. Use log-based checks instead.","category":"lesson","source":"memory/project_infra_improvements","confidence":"confirmed"},
  {"date":"2026-03-27","fact":"Docker-published ports bypass UFW (iptables DOCKER chain). Only systemd services (e.g. Router API 8520) need UFW rules.","category":"configuration","source":"memory/project_infra_improvements","confidence":"confirmed"},
  {"date":"2026-03-27","fact":"Phase 1b (secret rotation playbook) BLOCKED: vault vars vault_mc_* need populating before it can run.","category":"pending","source":"memory/project_infra_improvements","confidence":"confirmed"}
]
EOF
    echo "[migrate] created: pi-cluster/items.json"
fi

# Update MEMORY.md with redirect notice (prepend only if not already done)
if ! grep -q "MIGRATED TO ~/life/" "$MEM/MEMORY.md" 2>/dev/null; then
    {
        printf '> **MIGRATED TO ~/life/** (PARA knowledge system)\n'
        printf '> Claude should read ~/life/Areas/about-me/hard-rules.md and workflow-habits.md at session start.\n'
        printf '> Active knowledge lives in ~/life/ — files below are read-only legacy.\n\n'
        cat "$MEM/MEMORY.md"
    } > "$MEM/MEMORY.md.tmp" && mv "$MEM/MEMORY.md.tmp" "$MEM/MEMORY.md"
    echo "[migrate] updated MEMORY.md with redirect"
fi

echo "[migrate] Done."
