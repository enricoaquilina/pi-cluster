#!/bin/bash
# Auto-recovery: OpenClaw Gateway restart + OOM / memory pressure recovery
# Sourced in cron mode by system-smoke-test.sh

# ── Gateway restart (Telegram/connectivity failures) ─────────────────────────

openclaw_tg_fails=$(cat "$FAIL_COUNT_DIR/openclaw-telegram.count" 2>/dev/null || echo "0")
openclaw_gw_fails=$(cat "$FAIL_COUNT_DIR/openclaw-gateway.count" 2>/dev/null || echo "0")

if [[ "$openclaw_tg_fails" -ge 3 ]] || [[ "$openclaw_gw_fails" -ge 3 ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping Telegram auto-recovery" >> "$LOG_FILE"
    else
        send_alert "AUTO-RECOVERY: Restarting OpenClaw gateway after ${openclaw_tg_fails} consecutive Telegram failures"
        timed_ssh 15 ${HEAVY_HOST} "cd /mnt/external/openclaw && docker compose restart openclaw-gateway" 2>/dev/null
        sleep 30
        # Re-check
        if timed_ssh 8 ${HEAVY_HOST} "docker exec openclaw-openclaw-gateway-1 getent hosts api.telegram.org" >/dev/null 2>&1; then
            send_alert "AUTO-RECOVERY SUCCESS: OpenClaw gateway restarted, Telegram DNS resolving"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-telegram.count"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-gateway.count"
            echo "up" > "$STATE_DIR/openclaw-telegram.status"
            echo "up" > "$STATE_DIR/openclaw-gateway.status"
            # Re-pair nodes only if disconnected (gateway restart invalidates session tokens)
            sleep 20
            _nodes_raw=$(curl -sf --max-time 5 "http://${HEAVY_IP}:8520/nodes" 2>/dev/null)
            _disconnected=$(echo "$_nodes_raw" | python3 -c "
import json,sys
data=json.load(sys.stdin)
bad=[n['name'] for n in data.get('nodes',[]) if not n.get('connected',False) and n['name'] in ('build','light','heavy')]
print(' '.join(bad))
" 2>/dev/null)
            if [[ -n "$_disconnected" ]]; then
                send_alert "AUTO-RECOVERY: Nodes disconnected after gateway restart (${_disconnected}) — re-pairing"
                timed_ssh 60 ${HEAVY_HOST} "bash /home/enrico/pi-cluster/scripts/openclaw-pair-nodes.sh" >> "$LOG_FILE" 2>&1 || true
            fi
        else
            send_alert "AUTO-RECOVERY FAILED: OpenClaw gateway still can't resolve Telegram API after restart"
        fi
    fi
fi

# ── Gateway OOM / Memory Pressure ────────────────────────────────────────────

gw_mem_fails=$(cat "$FAIL_COUNT_DIR/openclaw-gateway-memory.count" 2>/dev/null || echo "0")

if [[ "$gw_mem_fails" -ge 2 ]]; then
    restart_count=$(timed_ssh 8 ${HEAVY_HOST} "docker inspect --format '{{.RestartCount}}' openclaw-openclaw-gateway-1" 2>/dev/null || echo "0")
    if [[ "$restart_count" -gt 5 ]] && check_circuit_breaker; then
        send_alert "AUTO-RECOVERY: Gateway OOM loop detected (${restart_count} restarts, memory failures: ${gw_mem_fails}) — recreating container"
        timed_ssh 30 ${HEAVY_HOST} "cd /mnt/external/openclaw && docker compose up -d --force-recreate openclaw-gateway" 2>/dev/null
        sleep 45
        if curl -sf --max-time 5 "http://${HEAVY_IP}:18789/healthz" >/dev/null 2>&1; then
            send_alert "AUTO-RECOVERY SUCCESS: Gateway recreated and healthy"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-gateway-memory.count"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-gateway.count"
            echo "up" > "$STATE_DIR/openclaw-gateway-memory.status"
            echo "up" > "$STATE_DIR/openclaw-gateway.status"
            # Re-pair nodes only if disconnected (force-recreate always invalidates session tokens)
            sleep 20
            _nodes_raw=$(curl -sf --max-time 5 "http://${HEAVY_IP}:8520/nodes" 2>/dev/null)
            _disconnected=$(echo "$_nodes_raw" | python3 -c "
import json,sys
data=json.load(sys.stdin)
bad=[n['name'] for n in data.get('nodes',[]) if not n.get('connected',False) and n['name'] in ('build','light','heavy')]
print(' '.join(bad))
" 2>/dev/null)
            if [[ -n "$_disconnected" ]]; then
                send_alert "AUTO-RECOVERY: Nodes disconnected after gateway recreate (${_disconnected}) — re-pairing"
                timed_ssh 60 ${HEAVY_HOST} "bash /home/enrico/pi-cluster/scripts/openclaw-pair-nodes.sh" >> "$LOG_FILE" 2>&1 || true
            fi
        else
            send_alert "AUTO-RECOVERY FAILED: Gateway still unhealthy after recreate — check NODE_OPTIONS and mem_limit"
        fi
    fi
fi
