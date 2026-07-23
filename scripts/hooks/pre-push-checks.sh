#!/bin/bash
# Pre-push checks.
# Currently just the secret-scan gate; add more checks here as the repo
# grows local pre-push tooling (see hooks/pre-push for the git-hook entry
# point and how it's wired up).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if ! command -v gitleaks >/dev/null 2>&1; then
    echo "FAILED: gitleaks not found -- run scripts/setup-hooks.sh for install instructions"
    exit 1
fi

echo "-- Secret scan --"

# Scans only the commits about to be pushed (not full history), so it can
# never be permanently red over something already on main. Mirrors the CI
# `secret-scan` job's range logic (.github/workflows/tests.yml).
RANGE="HEAD"
git rev-parse --verify origin/main >/dev/null 2>&1 && RANGE="origin/main..HEAD"

if ! gitleaks detect --source . --log-opts="$RANGE" --config .gitleaks.toml --redact --exit-code 1; then
    echo ""
    echo "FAILED: secret(s) detected above (file:line + rule) -- remove them,"
    echo "move the value into config/*.yml (gitignored) or an environment"
    echo "variable, and amend the commit before pushing."
    exit 1
fi

echo "Secret scan OK"
