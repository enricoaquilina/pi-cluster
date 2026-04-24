#!/usr/bin/env python3
"""Install, uninstall, or check status of ~/life/ cross-platform adapters."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ADAPTERS_DIR = Path(__file__).resolve().parent
MANIFEST = ADAPTERS_DIR / "manifest.json"


def load_manifest() -> dict:
    with open(MANIFEST) as f:
        return json.load(f)


def cmd_list(args):
    manifest = load_manifest()
    for name, info in manifest["adapters"].items():
        status = info["status"]
        tier = info["tier"]
        desc = info["description"]
        print(f"  {name:15s} [{status:6s}] ({tier}) — {desc}")


def cmd_check(args):
    manifest = load_manifest()
    adapter = args.adapter
    if adapter not in manifest["adapters"]:
        print(f"Unknown adapter: {adapter}")
        print(f"Available: {', '.join(manifest['adapters'])}")
        sys.exit(1)

    info = manifest["adapters"][adapter]
    print(f"Adapter: {adapter}")
    print(f"Status:  {info['status']}")
    print(f"Tier:    {info['tier']}")

    if adapter == "claude-code":
        result = subprocess.run(
            ["claude", "mcp", "list"], capture_output=True, text=True
        )
        if "life-write" in result.stdout:
            print("MCP:     life-write registered")
        else:
            print("MCP:     life-write NOT registered")

    for cf in info.get("config_files", []):
        expanded = Path(cf).expanduser()
        exists = expanded.exists()
        print(f"Config:  {cf} {'EXISTS' if exists else 'MISSING'}")

    for hook in info.get("hooks", []):
        print(f"Hook:    {hook}")


def cmd_install(args):
    manifest = load_manifest()
    adapter = args.adapter
    if adapter not in manifest["adapters"]:
        print(f"Unknown adapter: {adapter}")
        sys.exit(1)

    info = manifest["adapters"][adapter]
    if info["status"] == "future":
        print(f"{adapter} is a future adapter — no automated install yet.")
        print(f"See {ADAPTERS_DIR / adapter / 'AGENTS.md'} for integration docs.")
        return

    for cmd in info.get("setup", []):
        print(f"Running: {cmd}")
        if not args.dry_run:
            subprocess.run(cmd, shell=True, check=True)

    print(f"Installed {adapter}.")


def main():
    parser = argparse.ArgumentParser(description="~/life/ adapter manager")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all adapters")

    check_p = sub.add_parser("check", help="Check adapter status")
    check_p.add_argument("adapter")

    install_p = sub.add_parser("install", help="Install an adapter")
    install_p.add_argument("adapter")
    install_p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"list": cmd_list, "check": cmd_check, "install": cmd_install}[args.command](args)


if __name__ == "__main__":
    main()
