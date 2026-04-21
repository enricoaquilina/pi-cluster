#!/bin/bash
# Install all git hooks via pre-commit framework.
# Run once after cloning: bash hooks/install.sh
set -euo pipefail

if ! command -v pre-commit &>/dev/null; then
    echo "Installing pre-commit..."
    pipx install pre-commit 2>/dev/null || pip install --user pre-commit
fi

pre-commit install
pre-commit install --hook-type commit-msg
pre-commit install --hook-type pre-push

echo "Hooks installed: pre-commit, commit-msg, pre-push"
