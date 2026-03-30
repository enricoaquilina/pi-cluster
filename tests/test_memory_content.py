"""Memory content validation tests.

Ensures OpenClaw workspace files and Claude Code memories stay in sync
with the actual cluster state. Prevents drift between documentation
and reality.
"""

import os
import re
import subprocess

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# MC reads from the NFS-mounted workspace (heavy's external drive).
# The local ~/.openclaw/workspace/ is a separate copy used by the local
# OpenClaw instance and may differ.
OPENCLAW_WORKSPACE_NFS = "/mnt/external/openclaw/workspace"
OPENCLAW_WORKSPACE_LOCAL = os.path.expanduser("~/.openclaw/workspace")
CLAUDE_MEMORY_DIR = os.path.expanduser(
    "~/.claude/projects/-home-enrico/memory"
)


def _openclaw_workspace():
    """Return the best available OpenClaw workspace path."""
    if os.path.isdir(OPENCLAW_WORKSPACE_NFS):
        return OPENCLAW_WORKSPACE_NFS
    return OPENCLAW_WORKSPACE_LOCAL


requires_cluster = pytest.mark.skipif(
    not os.path.isdir(OPENCLAW_WORKSPACE_NFS)
    and not os.path.isdir(OPENCLAW_WORKSPACE_LOCAL),
    reason="Requires cluster environment with OpenClaw workspace",
)


def _read(path: str) -> str:
    with open(path) as f:
        return f.read()


def _ssh_cmd(host: str, cmd: str, timeout: int = 10) -> str:
    """Run a command on a remote host via SSH, return stdout."""
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# CI-safe tests (no cluster access needed)
# ---------------------------------------------------------------------------


def test_ansible_vars_valid_yaml():
    """vars/openclaw-nodes.yml must parse as valid YAML."""
    path = os.path.join(REPO_ROOT, "vars", "openclaw-nodes.yml")
    with open(path) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "Expected YAML dict"
    assert "openclaw_version" in data, "Missing openclaw_version key"


def test_ansible_version_format():
    """openclaw_version must match YYYY.M.DD or YYYY.M.DD-N pattern."""
    path = os.path.join(REPO_ROOT, "vars", "openclaw-nodes.yml")
    with open(path) as f:
        data = yaml.safe_load(f)
    version = data["openclaw_version"]
    assert re.match(
        r"^\d{4}\.\d{1,2}\.\d{1,2}(-\d+)?$", version
    ), f"Bad version format: {version}"


# ---------------------------------------------------------------------------
# Cluster-only: polymarket-bot.md
# ---------------------------------------------------------------------------


@requires_cluster
def test_polymarket_bot_md_traders():
    """polymarket-bot.md must list HedgeMaster88 as an enabled trader."""
    content = _read(os.path.join(_openclaw_workspace(), "polymarket-bot.md"))
    assert "HedgeMaster88" in content, "HedgeMaster88 missing from polymarket-bot.md"


@requires_cluster
def test_polymarket_bot_md_order_size():
    """polymarket-bot.md must reflect current order size ($5.54), not stale $25."""
    content = _read(os.path.join(_openclaw_workspace(), "polymarket-bot.md"))
    assert "$5.54" in content or "5.54" in content, (
        "Current order size $5.54 not found in polymarket-bot.md"
    )


@requires_cluster
def test_polymarket_bot_md_systemctl():
    """polymarket-bot.md must use system service commands, not --user."""
    content = _read(os.path.join(_openclaw_workspace(), "polymarket-bot.md"))
    assert "systemctl --user" not in content, (
        "polymarket-bot.md still references 'systemctl --user' — "
        "bot is a system service, should use 'sudo systemctl'"
    )


@requires_cluster
def test_polymarket_bot_md_venv():
    """polymarket-bot.md must reference the correct venv path."""
    content = _read(os.path.join(_openclaw_workspace(), "polymarket-bot.md"))
    assert "polymarket-venv" in content or "/home/enrico/polymarket-venv" in content, (
        "polymarket-bot.md still references old venv path"
    )


# ---------------------------------------------------------------------------
# Cluster-only: spreadbot.md
# ---------------------------------------------------------------------------


@requires_cluster
def test_spreadbot_md_base_spread():
    """spreadbot.md config table must show base_spread as 0.02 (2%), not 0.03."""
    content = _read(os.path.join(_openclaw_workspace(), "spreadbot.md"))
    # The config table has a row like: | `base_spread` | 0.02 (2%) |
    # Must not show the old 0.03 (3%) value in the base_spread row
    assert "0.03 (3%)" not in content, (
        "spreadbot.md still shows stale base_spread 0.03 (3%)"
    )
    assert "0.02 (2%)" in content, (
        "spreadbot.md missing updated base_spread 0.02 (2%)"
    )


# ---------------------------------------------------------------------------
# Cluster-only: OpenClaw MEMORY.md
# ---------------------------------------------------------------------------


@requires_cluster
def test_openclaw_memory_4_nodes():
    """OpenClaw MEMORY.md must reference 4 nodes, not 3."""
    content = _read(os.path.join(_openclaw_workspace(), "MEMORY.md"))
    assert "3 nodes" not in content.lower(), (
        "MEMORY.md still says '3 nodes' — cluster has 4"
    )
    assert "4" in content, "MEMORY.md should mention 4 nodes"


@requires_cluster
def test_openclaw_memory_heavy_role():
    """OpenClaw MEMORY.md must show heavy running gateway/MC, not master."""
    content = _read(os.path.join(_openclaw_workspace(), "MEMORY.md"))
    # heavy should be associated with gateway/MC duties
    heavy_idx = content.lower().find("heavy")
    assert heavy_idx != -1, "MEMORY.md doesn't mention heavy node"


@requires_cluster
def test_openclaw_memory_no_zeroclaw():
    """OpenClaw MEMORY.md infrastructure section must not reference orphaned ZeroClaw."""
    content = _read(os.path.join(_openclaw_workspace(), "MEMORY.md"))
    # ZeroClaw may appear in "Lessons Learned" as historical context, but NOT
    # in the infrastructure or services sections as if it's still running.
    infra_end = content.find("## Lessons Learned")
    if infra_end == -1:
        infra_end = len(content)
    infra_section = content[:infra_end]
    assert "ZeroClaw" not in infra_section and "zeroclaw" not in infra_section.lower(), (
        "MEMORY.md infrastructure/services still references orphaned ZeroClaw"
    )


@requires_cluster
def test_openclaw_memory_12_personas():
    """OpenClaw MEMORY.md dispatch table must list all 12 personas from MC."""
    content = _read(os.path.join(_openclaw_workspace(), "MEMORY.md"))
    personas = [
        "Archie", "Pixel", "Harbor", "Sentinel",
        "Docsworth", "Stratton", "Quill",
        "Flux", "Chroma", "Sigil",
        "Scout", "Ledger",
    ]
    missing = [p for p in personas if p not in content]
    assert not missing, f"MEMORY.md missing personas: {missing}"


@requires_cluster
def test_openclaw_memory_model():
    """OpenClaw MEMORY.md must mention the primary model."""
    content = _read(os.path.join(_openclaw_workspace(), "MEMORY.md"))
    assert "gemini-2.5-flash" in content, (
        "MEMORY.md doesn't mention primary model (gemini-2.5-flash)"
    )


# ---------------------------------------------------------------------------
# Cluster-only: Claude Code memories
# ---------------------------------------------------------------------------


@requires_cluster
def test_claude_memory_frontmatter():
    """Claude Code memory files must have valid YAML frontmatter."""
    memory_dir = CLAUDE_MEMORY_DIR
    if not os.path.isdir(memory_dir):
        pytest.skip("Claude Code memory directory not found")

    for fname in os.listdir(memory_dir):
        if not fname.endswith(".md") or fname == "MEMORY.md":
            continue
        path = os.path.join(memory_dir, fname)
        content = _read(path)
        assert content.startswith("---"), f"{fname}: missing frontmatter delimiter"
        end = content.index("---", 3)
        fm = yaml.safe_load(content[3:end])
        assert isinstance(fm, dict), f"{fname}: frontmatter is not a dict"
        for field in ("name", "description", "type"):
            assert field in fm, f"{fname}: missing frontmatter field '{field}'"
        assert fm["type"] in (
            "user", "feedback", "project", "reference"
        ), f"{fname}: invalid type '{fm['type']}'"


@requires_cluster
def test_claude_memory_no_secrets():
    """Claude Code memory files must not contain API keys or tokens."""
    memory_dir = CLAUDE_MEMORY_DIR
    if not os.path.isdir(memory_dir):
        pytest.skip("Claude Code memory directory not found")

    secret_patterns = [
        r"sk-[a-zA-Z0-9]{20,}",          # Anthropic/OpenAI keys
        r"AIza[a-zA-Z0-9_-]{35}",         # Google API keys
        r"ghp_[a-zA-Z0-9]{36}",           # GitHub PATs
        r"ghu_[a-zA-Z0-9]{36}",           # GitHub user tokens
        r"[a-f0-9]{48}",                  # 48-char hex tokens (like gateway token)
    ]
    for fname in os.listdir(memory_dir):
        if not fname.endswith(".md"):
            continue
        content = _read(os.path.join(memory_dir, fname))
        for pattern in secret_patterns:
            matches = re.findall(pattern, content)
            # Filter out git commit hashes (40 chars) which are expected
            matches = [m for m in matches if len(m) > 40 or not re.match(r"^[a-f0-9]+$", m)]
            assert not matches, (
                f"{fname}: possible secret found matching pattern {pattern}"
            )


# ---------------------------------------------------------------------------
# Cluster-only: version consistency
# ---------------------------------------------------------------------------


@requires_cluster
def test_ansible_version_matches_gateway():
    """Ansible version pin should match the running gateway version."""
    path = os.path.join(REPO_ROOT, "vars", "openclaw-nodes.yml")
    with open(path) as f:
        data = yaml.safe_load(f)
    pin = data["openclaw_version"]

    try:
        gateway_version = _ssh_cmd(
            "heavy",
            "docker exec openclaw-openclaw-gateway-1 openclaw --version 2>/dev/null",
        )
        # Extract version number (e.g. "OpenClaw 2026.3.24" → "2026.3.24")
        match = re.search(r"[\d.]+", gateway_version)
        if match:
            actual = match.group()
            assert pin == actual, (
                f"Ansible pins {pin} but gateway runs {actual}"
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("Cannot SSH to heavy to verify gateway version")
