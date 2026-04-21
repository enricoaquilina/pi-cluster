#!/bin/bash
# Auto-recovery: Disconnected OpenClaw nodes (slave0/slave1)
# Sourced in cron mode by system-smoke-test.sh

_node_repaired=0  # prevent calling pair-nodes twice if both nodes are down
for node_entry in slave0:build slave1:light; do
    host="${node_entry%%:*}"
    display="${node_entry##*:}"
    fails=$(cat "$FAIL_COUNT_DIR/openclaw-${host}.count" 2>/dev/null || echo "0")
    if [[ "$fails" -ge 3 ]] && check_circuit_breaker; then
        send_alert "AUTO-RECOVERY: Node ${display} (${host}) disconnected for 15+ min — restarting service"
        timed_ssh 10 "$host" "sudo systemctl restart openclaw-node" 2>/dev/null || true
        sleep 15
        # Check reconnection using same JSON pattern as _fetch_openclaw_node_status()
        node_status=$(curl -sf --max-time 5 "http://${HEAVY_IP}:8520/nodes" 2>/dev/null | \
            python3 -c "import json,sys; d=json.load(sys.stdin); nodes={n['name']:n.get('connected',False) for n in d.get('nodes',[])}; print('connected' if nodes.get('$display') else 'disconnected')" 2>/dev/null)
        if [[ "$node_status" == "connected" ]]; then
            send_alert "AUTO-RECOVERY SUCCESS: Node ${display} reconnected after service restart"
            echo "0" > "$FAIL_COUNT_DIR/openclaw-${host}.count"
            echo "up" > "$STATE_DIR/openclaw-${host}.status"
        elif [[ "$_node_repaired" -eq 0 ]]; then
            # Escalate: device_token_mismatch — re-pair needed (run at most once per cycle)
            send_alert "AUTO-RECOVERY: Node ${display} still disconnected — escalating to re-pair"
            timed_ssh 60 ${HEAVY_HOST} "bash /home/enrico/pi-cluster/scripts/openclaw-pair-nodes.sh" >> "$LOG_FILE" 2>&1 || true
            _node_repaired=1
        else
            send_alert "AUTO-RECOVERY: Node ${display} still disconnected — re-pair already ran this cycle"
        fi
    fi
done
