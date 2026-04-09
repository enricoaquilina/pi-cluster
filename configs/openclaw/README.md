# configs/openclaw

Canonical source of truth for `~/.openclaw/openclaw.json` on heavy.

This directory holds the checked-in template, plus the operational
recipe for how it reaches production.

## Files

- `openclaw.json.template` — 963-line canonical config. The only
  tokenization point is `channels.telegram.botToken`, which becomes
  `${TELEGRAM_BOT_TOKEN}` at render time. Everything else is
  non-secret config shape (model providers, plugin enablement,
  telegram allowlist IDs, etc.) and is safely diff-able in PRs.

## Render → install workflow

On heavy:

```bash
cd ~/pi-cluster
scripts/install-openclaw-config.sh --dry-run   # shows diff, touches nothing
scripts/install-openclaw-config.sh             # render + validate + atomic mv
```

The installer:
1. Renders `configs/openclaw/openclaw.json.template` via
   `scripts/render-openclaw-config.sh` using `~/openclaw/.env` for
   `${TELEGRAM_BOT_TOKEN}` substitution (envsubst with an explicit
   allowlist — stray `${...}` stays literal).
2. Runs `scripts/openclaw-config-validate.sh` against the rendered
   output. Validation failure aborts without touching the live file.
3. If byte-identical to the existing target → no-op, no backup.
4. Otherwise → timestamped backup + atomic `mv` into the target dir
   (`mktemp -p "$(dirname "$TARGET")"` guarantees same-filesystem
   rename, not cross-mount copy).
5. Post-install re-validation from the final path.

The running gateway container is **not** affected — config is loaded
at startup, and the watchdog's PR #124 validation gate will re-validate
at the next restart event.

## Required compose overlay: per-file `:ro` bind mount

The running gateway reads `openclaw.json` from the host via a bind
mount. Before 2026-04-09, the mount was a plain rw bind on the whole
`$OPENCLAW_CONFIG_DIR`, which meant `sudo docker exec ... openclaw
doctor --fix` could overwrite the file with container-root
ownership. That's the exact incident we're structurally preventing:
2026-04-07 root:root clobber, 2026-04-09 10-hour restart loop.

The fix is a **per-file bind mount** overlaid on top of the parent
dir mount, making just `openclaw.json` read-only from inside the
container while leaving the rest of `~/.openclaw/` writable (cron/,
delivery-queue/, devices/, nodes/, update-check.json, and the
rolling `openclaw.json.bak.N` backups all stay normal).

### YAML snippet to add to `docker-compose.yml`

For **every** service that bind-mounts `$OPENCLAW_CONFIG_DIR`
(currently `openclaw-gateway` and `openclaw-cli`), add the
`openclaw.json:/home/node/.openclaw/openclaw.json:ro` line
**directly after** the parent directory mount:

```yaml
    volumes:
      - ${OPENCLAW_CONFIG_DIR}:/home/node/.openclaw
      # openclaw.json is rendered via pi-cluster's
      # scripts/install-openclaw-config.sh from
      # configs/openclaw/openclaw.json.template. Re-mount it :ro on top
      # of the parent $OPENCLAW_CONFIG_DIR so neither the gateway nor
      # `docker exec ... openclaw doctor --fix` can mutate it from
      # inside the container. Structural prevention of the 2026-04-07
      # root:root incident where doctor --fix overwrote the file with
      # container-root ownership. The rest of ~/.openclaw stays rw for
      # legit state (cron/, delivery-queue/, devices/, nodes/, etc.).
      - ${OPENCLAW_CONFIG_DIR}/openclaw.json:/home/node/.openclaw/openclaw.json:ro
      - ${OPENCLAW_WORKSPACE_DIR}:/home/node/.openclaw/workspace
```

### Verification after applying

```bash
# Host side: file should still be writable (so install script works)
test -w ~/.openclaw/openclaw.json && echo "host writable OK"

# Container side: writes should all fail with EROFS
docker exec -u 0 openclaw-openclaw-gateway-1 sh -c '
    touch /home/node/.openclaw/openclaw.json 2>&1
    echo >> /home/node/.openclaw/openclaw.json 2>&1
    mv /home/node/.openclaw/openclaw.json /tmp/attack 2>&1
'
# Expected output: "Read-only file system" or "Device or resource busy"
# on all three attempts.

# Gateway should still read the config and start normally
curl -sS -o /dev/null -w "/healthz: %{http_code}\n" http://localhost:18789/healthz

# Parent dir should still be writable (other openclaw state continues to work)
docker exec -u 0 openclaw-openclaw-gateway-1 sh -c '
    touch /home/node/.openclaw/test-parent-write.tmp && echo parent_rw_ok && \
    rm /home/node/.openclaw/test-parent-write.tmp
'
```

### Why the compose file isn't in this repo

The `docker-compose.yml` that runs on heavy lives inside the
upstream openclaw repo at `/mnt/external/openclaw/docker-compose.yml`
(tracked against `https://github.com/openclaw/openclaw.git` main),
with Enrico's host-specific modifications layered on top as local
uncommitted changes. We don't mirror it into pi-cluster because:

1. The upstream repo is where new upstream changes arrive via
   `git pull`, and rebasing local mods against pull is simpler
   than syncing a fork.
2. The compose references hard-coded host paths
   (`/mnt/external/openclaw-custom/...`, etc.) that don't generalise.

This README + the YAML snippet above are how the intent survives in
git even though the applied file doesn't.

## Related

- #124 — watchdog config-validation gate
- #127 — hourly schema canary
- #131 — validator refuses to silently report "valid" on unreadable
- #132 — config-as-code (this template + installer)
- #133 — restart-count alerter
- This readme — compose :ro overlay, deployed 2026-04-09
