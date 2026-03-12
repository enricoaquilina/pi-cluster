#!/bin/sh
# Fix stale skill snapshots in OpenClaw sessions.
# Sessions created before the version system have version: undefined/missing,
# causing the refresh check to never trigger. This sets version: 1 on any
# session snapshot missing a version so the built-in refresh logic picks them up.

SESSIONS_DIR="/home/node/.openclaw/sessions"

if [ ! -d "$SESSIONS_DIR" ]; then
  echo "[fix-snapshots] No sessions directory found, skipping."
  exit 0
fi

fixed=0
for session_file in "$SESSIONS_DIR"/*.json; do
  [ -f "$session_file" ] || continue

  # Check if skillsSnapshot exists but has no version or version is 0/null
  if python3 -c "
import json, sys
try:
    data = json.loads(open('$session_file').read())
    snap = data.get('skillsSnapshot', {})
    if snap and not snap.get('version'):
        snap['version'] = 1
        data['skillsSnapshot'] = snap
        open('$session_file', 'w').write(json.dumps(data))
        sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
    fixed=$((fixed + 1))
  fi
done

echo "[fix-snapshots] Fixed $fixed session(s) with missing snapshot version."
