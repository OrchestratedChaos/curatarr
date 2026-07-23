#!/bin/bash
# One-time setup for local git hooks (currently: secret-scan pre-push gate).
# Run: bash scripts/setup-hooks.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "=== Curatarr Local Hooks Setup ==="

# 1. Set git hooks path to hooks/ directory (tracked in repo)
git config core.hooksPath hooks
echo "Git hooks path set to hooks/"

# 2. Make hook scripts executable
chmod +x hooks/pre-push
chmod +x scripts/hooks/*.sh
echo "Hook scripts made executable"

# 3. Check gitleaks
if command -v gitleaks &>/dev/null; then
    echo "gitleaks $(gitleaks version 2>&1 | awk '{print $NF}')"
else
    echo "ERROR: Install gitleaks:"
    echo "  macOS:   brew install gitleaks"
    echo "  Linux:   see https://github.com/gitleaks/gitleaks#installing"
    echo "  Windows: scoop install gitleaks  (or download from the releases page)"
    exit 1
fi

echo ""
echo "Setup complete! Hooks are active."
echo ""
echo "Usage:"
echo "  git push -> runs the secret-scan gate (gitleaks) against the commits"
echo "              about to be pushed"
