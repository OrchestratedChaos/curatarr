#!/bin/bash
# Curatarr Web UI launcher (macOS/Linux).
# Starts the local-only (127.0.0.1) Flask dashboard and opens it in
# your browser once it's listening. See web/app.py for the app itself.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Prefer a project-local virtualenv (./.venv, ./venv) or an already
# activated one over whatever `python3` PATH happens to point at - also
# used by run.sh. Must be sourced BEFORE pip-install.sh, whose
# curatarr_pip_install goes through the interpreter this resolves.
source "$SCRIPT_DIR/scripts/lib/python-env.sh"
curatarr_resolve_python "$SCRIPT_DIR"

# Shared "hash-verified lockfile, fall back to plain requirements" pip
# install helper - also used by run.sh.
source "$SCRIPT_DIR/scripts/lib/pip-install.sh"

if ! command -v "$CURATARR_PYTHON" &> /dev/null; then
    echo "Python 3 not found. Install Python 3.10+ first." >&2
    exit 1
fi

if [ -n "$CURATARR_VENV" ]; then
    echo "Using virtualenv: $CURATARR_VENV"
fi

# Python floor gate - same rationale as run.sh's check_and_install_dependencies:
# read the floor back out of requirements.lock's own header instead of a
# second hardcoded copy, so a version bump there can't silently drift out
# of sync with this script.
#
# This inline parser is intentionally NOT calling into
# utils/update_check.py's parse_version() (the app's canonical version
# parser - see run.sh's version_gt/version_ge for the full rationale,
# which applies here too): this check runs BEFORE install_deps() below,
# so importing utils.update_check (which pulls in utils/__init__.py's
# ~20 third-party-backed submodule imports) isn't safe on a fresh
# checkout, and REQUIRED_PYTHON is a 2-component "X.Y" string that
# parse_version's exactly-3-component anchoring would reject anyway.
PYTHON_VERSION="$("$CURATARR_PYTHON" --version | awk '{print $2}')"
if [ -f "requirements.lock" ]; then
    REQUIRED_PYTHON="$(grep -oE -- '--python-version [0-9]+\.[0-9]+' requirements.lock | head -1 | awk '{print $2}')"
    if [ -n "$REQUIRED_PYTHON" ] && ! "$CURATARR_PYTHON" -c "
import sys
def parse(v):
    return tuple(int(p) for p in v.strip().split('.'))
sys.exit(0 if parse(sys.argv[1]) >= parse(sys.argv[2]) else 1)
" "$PYTHON_VERSION" "$REQUIRED_PYTHON" 2>/dev/null; then
        echo "Python $PYTHON_VERSION found, but curatarr's web UI requires Python $REQUIRED_PYTHON+." >&2
        # Name the interpreter when it came from a venv - otherwise "Python
        # 3.9.6 found" is baffling on a machine whose `python3` is 3.12.
        if [ -n "$CURATARR_VENV" ]; then
            echo "That interpreter is the virtualenv at $CURATARR_VENV ($CURATARR_PYTHON)." >&2
            echo "Recreate it with a newer Python, or set CURATARR_NO_VENV=1 to ignore it." >&2
        fi
        echo "Upgrade Python, or use a standalone curatarr binary instead (bundles its own" >&2
        echo "Python + UI deps): https://github.com/OrchestratedChaos/curatarr/releases" >&2
        exit 1
    fi
fi

# Core deps (plexapi/requests/pyyaml - requirements.txt) plus the web
# UI's own deps (flask/ruamel.yaml - requirements-ui.txt). Prefer the
# hashed locks when present, same rationale as run.sh; fall back to the
# plain pinned files (still reproducible, just unverified) otherwise.
# The actual pip invocation/fallback is shared with run.sh - see
# scripts/lib/pip-install.sh (curatarr_pip_install), which installs via
# $CURATARR_PYTHON -m pip so the deps land in the same interpreter the
# import check below and the exec at the end of this file use.
_run_ui_on_hash_fail() {
    echo "Hash-verified install failed (hash/platform mismatch?) - falling back to a" >&2
    echo "normal pinned install (no hash verification) for this run." >&2
}

install_deps() {
    curatarr_pip_install "requirements.lock requirements-ui.lock" "requirements.txt requirements-ui.txt" _run_ui_on_hash_fail
}

if ! "$CURATARR_PYTHON" -c "import flask, ruamel.yaml" &> /dev/null; then
    echo "Installing web UI dependencies..."
    install_deps
fi

export CURATARR_UI_PORT="${CURATARR_UI_PORT:-8787}"
echo "Starting Curatarr web UI on http://127.0.0.1:${CURATARR_UI_PORT} (Ctrl+C to stop) ..."
exec "$CURATARR_PYTHON" -m web.app
