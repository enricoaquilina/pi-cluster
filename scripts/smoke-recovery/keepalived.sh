#!/bin/bash
# Auto-recovery: Keepalived HA (VIP failover for Pi-hole DNS)
# Sourced in cron mode by system-smoke-test.sh

_ka_fails=$(cat "$FAIL_COUNT_DIR/keepalived.count" 2>/dev/null || echo "0")
_ka_status=$(cat "$STATE_DIR/keepalived.status" 2>/dev/null || echo "up")
_ka_error="${ERRORS[keepalived]:-}"

# Degraded persistence check (fail counts only increment for down, not degraded)
_ka_degraded_persistent=false
if [[ "$_ka_status" == "degraded" ]]; then
    _ka_since=$(cat "$STATE_DIR/keepalived.since" 2>/dev/null || echo "$TIMESTAMP")
    [[ $(( TIMESTAMP - _ka_since )) -ge 900 ]] && _ka_degraded_persistent=true
fi

# ── Down scenarios (fail count >= 3 = 15+ min) ──────────────────────────────

if [[ "$_ka_fails" -ge 3 ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping keepalived recovery" >> "$LOG_FILE"
    elif [[ "$_ka_error" == *"SPLIT-BRAIN"* ]]; then
        send_alert "AUTO-RECOVERY: Keepalived SPLIT-BRAIN — restarting BACKUP (slave1)"
        timed_ssh 10 slave1 "sudo systemctl restart keepalived" 2>/dev/null || true
        sleep 5
        _ka_s1_active=$(timed_ssh 5 slave1 "systemctl is-active keepalived" 2>/dev/null || echo "inactive")
        _ka_s0_vip=$(timed_ssh 5 slave0 "ip -4 addr show eth0 | grep -c 192.168.0.53" 2>/dev/null | tr -d '[:space:]')
        _ka_s1_vip=$(timed_ssh 5 slave1 "ip -4 addr show eth0 | grep -c 192.168.0.53" 2>/dev/null | tr -d '[:space:]')
        : "${_ka_s0_vip:=0}" "${_ka_s1_vip:=0}"
        if [[ "$_ka_s1_active" == "active" && "$_ka_s0_vip" -gt 0 && "$_ka_s1_vip" -eq 0 ]]; then
            send_alert "AUTO-RECOVERY SUCCESS: Split-brain resolved — VIP on MASTER"
            echo "0" > "$FAIL_COUNT_DIR/keepalived.count"
            echo "up" > "$STATE_DIR/keepalived.status"
        else
            send_alert "AUTO-RECOVERY FAILED: Split-brain not resolved"
        fi
    elif [[ "$_ka_error" == *"Both nodes"* ]]; then
        send_alert "AUTO-RECOVERY: Both keepalived nodes down — restarting"
        timed_ssh 10 slave0 "sudo systemctl restart keepalived" 2>/dev/null || true
        sleep 3
        timed_ssh 10 slave1 "sudo systemctl restart keepalived" 2>/dev/null || true
        sleep 5
        _ka_s0_active=$(timed_ssh 5 slave0 "systemctl is-active keepalived" 2>/dev/null || echo "inactive")
        _ka_s1_active=$(timed_ssh 5 slave1 "systemctl is-active keepalived" 2>/dev/null || echo "inactive")
        _ka_s0_vip=$(timed_ssh 5 slave0 "ip -4 addr show eth0 | grep -c 192.168.0.53" 2>/dev/null | tr -d '[:space:]')
        : "${_ka_s0_vip:=0}"
        if [[ "$_ka_s0_active" == "active" && "$_ka_s1_active" == "active" && "$_ka_s0_vip" -gt 0 ]]; then
            send_alert "AUTO-RECOVERY SUCCESS: Both keepalived nodes restored"
            echo "0" > "$FAIL_COUNT_DIR/keepalived.count"
            echo "up" > "$STATE_DIR/keepalived.status"
        else
            send_alert "AUTO-RECOVERY FAILED: Keepalived still not healthy"
        fi
    fi
fi

# ── Degraded scenarios (persistent 15+ min via .since timestamp) ─────────────

if [[ "$_ka_degraded_persistent" == true ]]; then
    if ! check_circuit_breaker; then
        echo "[${NOW}] Circuit breaker tripped — skipping keepalived degraded recovery" >> "$LOG_FILE"
    elif [[ "$_ka_error" == *"not running"* ]]; then
        _ka_down_node="slave0"
        [[ "$_ka_error" == *"slave1"* ]] && _ka_down_node="slave1"
        send_alert "AUTO-RECOVERY: ${_ka_down_node} keepalived down 15+ min — restarting"
        timed_ssh 10 "$_ka_down_node" "sudo systemctl restart keepalived" 2>/dev/null || true
        sleep 5
        _ka_verify=$(timed_ssh 5 "$_ka_down_node" "systemctl is-active keepalived" 2>/dev/null || echo "inactive")
        if [[ "$_ka_verify" == "active" ]]; then
            send_alert "AUTO-RECOVERY SUCCESS: ${_ka_down_node} keepalived restored"
            echo "up" > "$STATE_DIR/keepalived.status"
        else
            send_alert "AUTO-RECOVERY FAILED: ${_ka_down_node} keepalived still down"
        fi
    elif [[ "$_ka_error" == *"VIP"*"not found"* ]]; then
        send_alert "AUTO-RECOVERY: VIP missing — restarting keepalived on both nodes"
        timed_ssh 10 slave0 "sudo systemctl restart keepalived" 2>/dev/null || true
        sleep 3
        timed_ssh 10 slave1 "sudo systemctl restart keepalived" 2>/dev/null || true
        sleep 5
        _ka_s0_vip=$(timed_ssh 5 slave0 "ip -4 addr show eth0 | grep -c 192.168.0.53" 2>/dev/null | tr -d '[:space:]')
        _ka_s1_vip=$(timed_ssh 5 slave1 "ip -4 addr show eth0 | grep -c 192.168.0.53" 2>/dev/null | tr -d '[:space:]')
        : "${_ka_s0_vip:=0}" "${_ka_s1_vip:=0}"
        if [[ $(( _ka_s0_vip + _ka_s1_vip )) -eq 1 ]]; then
            send_alert "AUTO-RECOVERY SUCCESS: VIP restored"
            echo "up" > "$STATE_DIR/keepalived.status"
        else
            send_alert "AUTO-RECOVERY FAILED: VIP still not assigned"
        fi
    fi
fi
