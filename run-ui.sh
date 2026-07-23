#!/bin/bash
# Curatarr Web UI launcher (macOS/Linux).
# Starts the local-only (127.0.0.1) Flask dashboard and opens it in
# your browser once it's listening. See web/app.py for the app itself.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found. Install Python 3.10+ first." >&2
    exit 1
fi

# Python floor gate - same rationale as run.sh's check_and_install_dependencies:
# read the floor back out of requirements.lock's own header instead of a
# second hardcoded copy, so a version bump there can't silently drift out
# of sync with this script.
PYTHON_VERSION="$(python3 --version | awk '{print $2}')"
if [ -f "requirements.lock" ]; then
    REQUIRED_PYTHON="$(grep -oE -- '--python-version [0-9]+\.[0-9]+' requirements.lock | head -1 | awk '{print $2}')"
    if [ -n "$REQUIRED_PYTHON" ] && ! python3 -c "
import sys
def parse(v):
    return tuple(int(p) for p in v.strip().split('.'))
sys.exit(0 if parse(sys.argv[1]) >= parse(sys.argv[2]) else 1)
" "$PYTHON_VERSION" "$REQUIRED_PYTHON" 2>/dev/null; then
        echo "Python $PYTHON_VERSION found, but curatarr's web UI requires Python $REQUIRED_PYTHON+." >&2
        echo "Upgrade Python, or use a standalone curatarr binary instead (bundles its own" >&2
        echo "Python + UI deps): https://github.com/OrchestratedChaos/curatarr/releases" >&2
        exit 1
    fi
fi

# Core deps (plexapi/requests/pyyaml - requirements.txt) plus the web
# UI's own deps (flask/ruamel.yaml - requirements-ui.txt). Prefer the
# hashed locks when present, same rationale as run.sh; fall back to the
# plain pinned files (still reproducible, just unverified) otherwise.
install_deps() {
    if [ -f "requirements.lock" ] && [ -f "requirements-ui.lock" ]; then
        pip3 install --require-hashes -r requirements.lock -r requirements-ui.lock --quiet && return
        echo "Hash-verified install failed (hash/platform mismatch?) - falling back to a" >&2
        echo "normal pinned install (no hash verification) for this run." >&2
    fi
    pip3 install -r requirements.txt -r requirements-ui.txt --quiet
}

if ! python3 -c "import flask, ruamel.yaml" &> /dev/null; then
    echo "Installing web UI dependencies..."
    install_deps
fi

export CURATARR_UI_PORT="${CURATARR_UI_PORT:-8787}"
echo "Starting Curatarr web UI on http://127.0.0.1:${CURATARR_UI_PORT} (Ctrl+C to stop) ..."
exec python3 -m web.app
