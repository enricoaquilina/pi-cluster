#!/usr/bin/env python3
"""Validate Jinja2 templates render without errors using test variables."""

import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"

# Test variables matching production inventory structure
TEST_VARS = {
    # openclaw-nodes vars
    "openclaw_node_name": "build",
    "openclaw_node_role": "coding",
    "openclaw_gateway_host": "192.168.0.1",
    "openclaw_gateway_port": 18789,
    "openclaw_exec_approvals": [
        "/usr/bin/git",
        "/usr/bin/python3",
        "/usr/bin/bash",
    ],
    # NFS vars
    "nfs_exports": [
        {"path": "/home/enrico/homelab", "mount": "/opt/workspace"},
        {"path": "/mnt/external", "mount": "/mnt/external"},
    ],
    # Monitoring vars
    "openclaw_watchdog_interval": 120,
    # Ansible built-ins
    "inventory_hostname": "slave0",
}

# Templates and which vars they need
TEMPLATE_TESTS = {
    "zeroclaw-node.service.j2": [
        "openclaw_node_name",
        "openclaw_gateway_host",
        "openclaw_gateway_port",
    ],
    "exec-approvals.json.j2": [
        "openclaw_node_name",
        "openclaw_node_role",
        "openclaw_exec_approvals",
    ],
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


def main():
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )

    all_passed = True
    tested = 0

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

    print(f"\n{tested} templates tested")

    if not all_passed:
        print("Some templates failed validation")
        sys.exit(1)

    if tested == 0:
        print("No templates found to test")
        sys.exit(1)

    print("All templates passed")


if __name__ == "__main__":
    main()
