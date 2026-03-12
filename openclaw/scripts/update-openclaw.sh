#!/bin/bash
set -euo pipefail

# OpenClaw Gateway Update Script
# Usage: update-openclaw.sh [--rollback] [--dry-run]

OPENCLAW_DIR="/mnt/external/openclaw"
CUSTOM_DIR="/mnt/external/openclaw-custom/openclaw"
COMPOSE_DIR="/mnt/external/openclaw"
MIN_DISK_MB=5000

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Parse args
DRY_RUN=false
ROLLBACK=false
for arg in "$@"; do
    case "$arg" in
        --dry-run)  DRY_RUN=true ;;
        --rollback) ROLLBACK=true ;;
        *)          error "Unknown argument: $arg"; exit 1 ;;
    esac
done

# Rollback mode
if [ "$ROLLBACK" = true ]; then
    SAVED_ID="/tmp/openclaw-prev-image-id"
    if [ ! -f "$SAVED_ID" ]; then
        error "No saved image ID found at $SAVED_ID. Cannot rollback."
        exit 1
    fi
    PREV_ID=$(cat "$SAVED_ID")
    info "Rolling back to image: $PREV_ID"
    docker tag "$PREV_ID" openclaw-custom:local
    cd "$COMPOSE_DIR"
    docker compose up -d openclaw-gateway
    sleep 5
    docker compose logs --tail 5 openclaw-gateway
    info "Rollback complete."
    exit 0
fi

# 1. Check disk space
AVAIL_MB=$(df -BM /mnt/external --output=avail | tail -1 | tr -d ' M')
if [ "$AVAIL_MB" -lt "$MIN_DISK_MB" ]; then
    error "Only ${AVAIL_MB}MB free on /mnt/external (need ${MIN_DISK_MB}MB for build)."
    exit 1
fi
info "Disk space OK: ${AVAIL_MB}MB available."

# 2. Fetch latest
cd "$OPENCLAW_DIR"
CURRENT_VERSION=$(python3 -c "import json; print(json.load(open('package.json'))['version'])" 2>/dev/null || echo "unknown")
info "Current version: $CURRENT_VERSION"

git fetch origin main

# 3. Show changelog
DIFF_LOG=$(git log --oneline HEAD..origin/main 2>/dev/null || echo "(no new commits)")
echo ""
info "Changes since $CURRENT_VERSION:"
echo "$DIFF_LOG"
echo ""

if [ "$DRY_RUN" = true ]; then
    NEW_VERSION=$(git show origin/main:package.json | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])" 2>/dev/null || echo "unknown")
    info "[DRY RUN] Would update from $CURRENT_VERSION to $NEW_VERSION"
    exit 0
fi

# 4. Confirm
read -p "Proceed with update? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    info "Aborted."
    exit 0
fi

# 5. Save old image ID for rollback
OLD_IMAGE_ID=$(docker inspect --format='{{.Id}}' openclaw-custom:local 2>/dev/null || echo "")
if [ -n "$OLD_IMAGE_ID" ]; then
    echo "$OLD_IMAGE_ID" > /tmp/openclaw-prev-image-id
    info "Saved old image ID for rollback."
fi

# 6. Pull
info "Pulling latest..."
git pull origin main

# 7. Build base image
info "Building base openclaw:local image (this may take 10-15 min on Pi)..."
docker build -t openclaw:local -f Dockerfile .

# 8. Build custom layer
info "Building openclaw-custom:local..."
docker build -t openclaw-custom:local "$CUSTOM_DIR"

# 9. Restart gateway
info "Restarting gateway..."
cd "$COMPOSE_DIR"
docker compose up -d openclaw-gateway

# 10. Verify
sleep 10
info "Gateway logs (last 5 lines):"
docker compose logs --tail 5 openclaw-gateway

NEW_VERSION=$(python3 -c "import json; print(json.load(open('$OPENCLAW_DIR/package.json'))['version'])" 2>/dev/null || echo "unknown")
echo ""
info "Update complete: $CURRENT_VERSION → $NEW_VERSION"
info "To rollback: $(realpath "$0") --rollback"
