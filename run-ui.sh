#!/bin/bash
# Curatarr Web UI launcher (macOS/Linux).
# Starts the local-only (127.0.0.1) Flask dashboard and opens it in
# your browser once it's listening. See web/app.py for the app itself.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found. Install Python 3.8+ first." >&2
    exit 1
fi

if ! python3 -c "import flask" &> /dev/null; then
    echo "Installing web UI dependencies..."
    pip3 install -r requirements.txt --quiet
fi

export CURATARR_UI_PORT="${CURATARR_UI_PORT:-8787}"
echo "Starting Curatarr web UI on http://127.0.0.1:${CURATARR_UI_PORT} (Ctrl+C to stop) ..."
exec python3 -m web.app
