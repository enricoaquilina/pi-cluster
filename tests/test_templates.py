#!/usr/bin/env python3
"""Validate Jinja2 templates render without errors using test variables."""

import json
import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"

# Test variables matching production inventory/vars structure
TEST_VARS = {
    # openclaw-nodes vars
    "openclaw_node_name": "build",
    "openclaw_node_role": "coding",
    "openclaw_gateway_host": "192.168.0.22",
    "openclaw_gateway_port": 18789,
    "openclaw_gateway_token": "test-token-abcdef1234567890",
    "openclaw_exec_approvals_token": "auto-approve",
    # NFS vars
    "nfs_exports": [
        {"path": "/home/enrico/homelab", "mount": "/opt/workspace"},
        {"path": "/mnt/external", "mount": "/mnt/external"},
    ],
    # Monitoring vars
    "openclaw_watchdog_interval": 120,
    # Ansible built-ins
    "inventory_hostname": "slave0",
    # Pihole/keepalived vars
    "keepalived_state": "MASTER",
    "keepalived_priority": 150,
    "pihole_vip": "192.168.0.53",
    "pihole_vip_interface": "eth0",
    "keepalived_auth_pass": "test-pass",
    "gravity_sync_remote_host": "192.168.0.4",
    "gravity_sync_user": "enrico",
}

# Templates and which vars they need
TEMPLATE_TESTS = {
    "openclaw-node.service.j2": [
        "openclaw_node_name",
        "openclaw_gateway_host",
        "openclaw_gateway_port",
        "openclaw_gateway_token",
    ],
    "exec-approvals.json.j2": [],
    "nfs-exports.j2": [
        "nfs_exports",
    ],
    "openclaw-watchdog.sh.j2": [
        "nfs_exports",
        "inventory_hostname",
    ],
}


def test_template(env, template_name, required_vars):
    """Render a template and verify it produces non-empty output."""
    errors = []

    try:
        template = env.get_template(template_name)
    except Exception as e:
        return [f"LOAD ERROR: {e}"]

    try:
        rendered = template.render(TEST_VARS)
    except Exception as e:
        return [f"RENDER ERROR: {e}"]

    if not rendered.strip():
        errors.append("rendered output is empty")

    # Check that key variables were actually interpolated (not left as literals)
    for var in required_vars:
        val = TEST_VARS.get(var)
        if isinstance(val, str) and val not in rendered:
            errors.append(f"expected value '{val}' for {var} not found in output")

    return errors


def test_exec_approvals_json():
    """Validate exec-approvals.json.j2 produces valid JSON with required keys."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    errors = []
    try:
        template = env.get_template("exec-approvals.json.j2")
        rendered = template.render(TEST_VARS)
        data = json.loads(rendered)

        # Validate structure
        if "version" not in data:
            errors.append("missing 'version' key")
        if "defaults" not in data:
            errors.append("missing 'defaults' key")
        elif data["defaults"].get("security") != "full":
            errors.append("defaults.security should be 'full'")
        elif data["defaults"].get("ask") != "off":
            errors.append("defaults.ask should be 'off'")
        if "agents" not in data:
            errors.append("missing 'agents' key")
    except json.JSONDecodeError as e:
        errors.append(f"invalid JSON: {e}")
    except Exception as e:
        errors.append(f"render error: {e}")

    return errors


def test_node_service():
    """Validate openclaw-node.service.j2 has required systemd fields."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    errors = []
    try:
        template = env.get_template("openclaw-node.service.j2")
        rendered = template.render(TEST_VARS)

        required = ["[Unit]", "[Service]", "[Install]", "ExecStart=",
                     "openclaw node run", "--host 192.168.0.22",
                     "--port 18789", "--display-name build",
                     "OPENCLAW_GATEWAY_TOKEN="]
        for field in required:
            if field not in rendered:
                errors.append(f"missing required field: {field}")

        if "Restart=always" not in rendered:
            errors.append("missing Restart=always (node should auto-restart)")

    except Exception as e:
        errors.append(f"render error: {e}")

    return errors


def test_nfs_exports():
    """Validate NFS exports template has correct security options."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    errors = []
    try:
        template = env.get_template("nfs-exports.j2")
        rendered = template.render(TEST_VARS)

        if "all_squash" not in rendered:
            errors.append("missing all_squash (required for cross-node UID consistency)")
        if "anonuid=1000" not in rendered:
            errors.append("missing anonuid=1000")
        if "anongid=1000" not in rendered:
            errors.append("missing anongid=1000")
        if "/home/enrico/homelab" not in rendered:
            errors.append("missing homelab export path")
        if "/mnt/external" not in rendered:
            errors.append("missing external mount export path")

    except Exception as e:
        errors.append(f"render error: {e}")

    return errors


def main():
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )

    all_passed = True
    tested = 0

    # Basic template rendering tests
    print("=== Template Rendering ===")
    for template_name, required_vars in TEMPLATE_TESTS.items():
        template_path = TEMPLATES_DIR / template_name
        if not template_path.exists():
            print(f"SKIP  {template_name} (file not found)")
            continue

        errors = test_template(env, template_name, required_vars)
        tested += 1

        if errors:
            all_passed = False
            print(f"FAIL  {template_name}")
            for err in errors:
                print(f"      - {err}")
        else:
            print(f"PASS  {template_name}")

    # Structural validation tests
    print("\n=== Structural Validation ===")
    structural_tests = {
        "exec-approvals.json valid JSON + required keys": test_exec_approvals_json,
        "openclaw-node.service systemd fields": test_node_service,
        "nfs-exports security options": test_nfs_exports,
    }

    for name, test_fn in structural_tests.items():
        errors = test_fn()
        tested += 1
        if errors:
            all_passed = False
            print(f"FAIL  {name}")
            for err in errors:
                print(f"      - {err}")
        else:
            print(f"PASS  {name}")

    print(f"\n{tested} tests run")

    if not all_passed:
        print("Some tests failed")
        sys.exit(1)

    if tested == 0:
        print("No tests found to run")
        sys.exit(1)

    print("All tests passed")


if __name__ == "__main__":
    main()
