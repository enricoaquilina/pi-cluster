"""Validate OpenClaw gateway docker-compose configuration on the cluster."""

import os
import re

import pytest
import yaml

requires_cluster = pytest.mark.skipif(
    not os.path.exists("/mnt/external/mission-control"),
    reason="Requires cluster environment (skipped in CI)",
)

# The active compose file — Docker runs from this path
COMPOSE_PATH = "/mnt/external/openclaw/docker-compose.yml"


def _load_gateway_config():
    """Load and return the openclaw-gateway service config from compose."""
    with open(COMPOSE_PATH) as f:
        compose = yaml.safe_load(f)
    return compose["services"]["openclaw-gateway"]


@requires_cluster
def test_gateway_has_node_options():
    """Gateway sets NODE_OPTIONS with --max-old-space-size >= 1024MB."""
    gw = _load_gateway_config()
    env = gw.get("environment", {})
    node_opts = env.get("NODE_OPTIONS", "")
    assert "--max-old-space-size=" in node_opts, (
        f"NODE_OPTIONS must include --max-old-space-size, got: {node_opts!r}"
    )
    match = re.search(r"--max-old-space-size=(\d+)", node_opts)
    assert match, f"Cannot parse max-old-space-size from: {node_opts!r}"
    heap_mb = int(match.group(1))
    assert heap_mb >= 1024, (
        f"max-old-space-size={heap_mb}MB is too low for gateway workload (need >= 1024)"
    )


@requires_cluster
def test_gateway_healthcheck_is_lightweight():
    """Gateway healthcheck uses curl or wget, not a node process."""
    gw = _load_gateway_config()
    hc = gw.get("healthcheck", {})
    test_cmd = hc.get("test", [])
    cmd_str = " ".join(test_cmd) if isinstance(test_cmd, list) else str(test_cmd)
    # Must not spawn a full node process
    assert "node" not in cmd_str.split(), (
        f"Healthcheck spawns node process (wastes ~40MB RAM): {cmd_str}"
    )
    # Must use a lightweight HTTP tool
    assert any(tool in cmd_str for tool in ["curl", "wget"]), (
        f"Healthcheck should use curl or wget, got: {cmd_str}"
    )


@requires_cluster
def test_gateway_mem_limit_exceeds_heap():
    """Container mem_limit provides >= 256MB headroom above heap size."""
    gw = _load_gateway_config()

    # Parse mem_limit to MB
    mem_str = str(gw.get("mem_limit", "0")).lower()
    if mem_str.endswith("g"):
        mem_limit_mb = int(float(mem_str[:-1]) * 1024)
    elif mem_str.endswith("m"):
        mem_limit_mb = int(float(mem_str[:-1]))
    else:
        mem_limit_mb = int(mem_str) // (1024 * 1024)  # bytes to MB

    # Parse max-old-space-size from NODE_OPTIONS
    env = gw.get("environment", {})
    node_opts = env.get("NODE_OPTIONS", "")
    match = re.search(r"--max-old-space-size=(\d+)", node_opts)
    assert match, "Cannot find max-old-space-size (test_gateway_has_node_options should catch this)"
    heap_mb = int(match.group(1))

    headroom = mem_limit_mb - heap_mb
    assert headroom >= 256, (
        f"Insufficient headroom: mem_limit={mem_limit_mb}MB - heap={heap_mb}MB = {headroom}MB "
        f"(need >= 256MB for V8 overhead, native buffers, OS)"
    )


@requires_cluster
def test_gateway_has_restart_policy():
    """Gateway has a restart policy for auto-recovery."""
    gw = _load_gateway_config()
    restart = gw.get("restart", "no")
    assert restart in ("always", "unless-stopped"), (
        f"Gateway restart policy is {restart!r}, expected 'always' or 'unless-stopped'"
    )


@requires_cluster
def test_gateway_on_mission_control_network():
    """Gateway joins mission-control_default network for MC API access."""
    gw = _load_gateway_config()
    networks = gw.get("networks", [])
    assert "mission-control_default" in networks, (
        f"Gateway must join mission-control_default network, has: {networks}"
    )


@requires_cluster
def test_gateway_has_mission_control_url():
    """Gateway sets MISSION_CONTROL_URL for the MC skill."""
    gw = _load_gateway_config()
    env = gw.get("environment", {})
    mc_url = env.get("MISSION_CONTROL_URL", "")
    assert mc_url, "MISSION_CONTROL_URL not set — mc.py defaults to unreachable 192.168.0.22:3000"
    assert "mission-control" in mc_url, (
        f"MISSION_CONTROL_URL should use Docker DNS name, got: {mc_url}"
    )


@requires_cluster
def test_mission_control_network_is_external():
    """mission-control_default declared as external network in compose."""
    with open(COMPOSE_PATH) as f:
        compose = yaml.safe_load(f)
    networks = compose.get("networks", {})
    mc_net = networks.get("mission-control_default", {})
    assert mc_net.get("external") is True, (
        "mission-control_default must be external: true (created by MC stack)"
    )


@requires_cluster
def test_node_services_have_restart_limits():
    """Node services have StartLimitBurst and RestartSec=30 to prevent infinite restart loops."""
    import subprocess
    for host in ["slave0", "slave1"]:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", host,
             "cat", "/etc/systemd/system/openclaw-node.service"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            pytest.skip(f"Cannot reach {host}")
        content = result.stdout
        assert "StartLimitBurst" in content, (
            f"{host} missing StartLimitBurst — can enter infinite restart loops"
        )
        assert "StartLimitIntervalSec" in content, (
            f"{host} missing StartLimitIntervalSec"
        )
        assert "RestartSec=30" in content, (
            f"{host} RestartSec must be 30s (gives gateway time to start up after recreate)"
        )


SMOKE_TEST_PATH = "/home/enrico/pi-cluster/scripts/system-smoke-test.sh"


@pytest.mark.skipif(
    not os.path.exists(SMOKE_TEST_PATH),
    reason="Smoke test script not found (skipped outside cluster)",
)
def test_smoke_test_recovery_uses_ssh():
    """Smoke test auto-recovery docker commands use SSH (script runs on master, containers on heavy)."""
    with open(SMOKE_TEST_PATH) as f:
        lines = f.readlines()
    bare_docker = []
    in_recovery = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if "Auto-Recovery" in stripped:
            in_recovery = True
        if not in_recovery:
            continue
        if stripped.startswith("#"):
            continue
        has_docker = any(cmd in stripped for cmd in ["docker compose", "docker inspect", "docker stats"])
        has_ssh = "timed_ssh" in stripped or "ssh " in stripped
        if has_docker and not has_ssh:
            bare_docker.append(f"line {i}: {stripped[:80]}")
    assert not bare_docker, (
        "Auto-recovery has bare docker commands (won't work from master, containers are on heavy):\n"
        + "\n".join(bare_docker[:5])
    )
