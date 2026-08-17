#!/usr/bin/env bash
# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Runs verify_collections.py wherever Plex is actually reachable.
#
# Usage: ./run_remote.sh [--user NAME] [--media movie|tv]
#
# Environment (only needed when Plex is NOT reachable from here):
#   CURATARR_PLEX_SSH_HOST     - SSH alias/host of the machine running
#                                Plex, which also has a checkout of this
#                                repo. No default; set it in your shell
#                                profile.
#   CURATARR_PLEX_SSH_REPO_DIR - absolute path to that checkout. Defaults
#                                to ~/dev/curatarr on that host.
#
# Probe-then-delegate rather than pattern-matching a hostname, mirroring
# scripts/release.sh's own origin-reachability check - and for the same
# reason it was rewritten that way (see CHANGELOG 2.19.1): guessing from
# config values gets it wrong on exactly the shared-checkout setups this
# is for. `plex.url` is commonly a 127-0-0-1.<hash>.plex.direct hostname,
# which resolves to 127.0.0.1 everywhere and therefore says nothing about
# WHERE Plex is.
#
# Read-only: verify_collections.py never writes to Plex or to the cache.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REL_PATH=".claude/skills/verify-recommendations/verify_collections.py"

plex_port_open() {
  # `plex.url`'s host, whatever it is, on its port - a 3s TCP probe.
  local host port
  host=$(python3 -c "
import sys, yaml, urllib.parse
u = yaml.safe_load(open('$REPO_DIR/config/config.yml'))['plex']['url']
p = urllib.parse.urlparse(u)
print(p.hostname or '', p.port or (443 if p.scheme == 'https' else 80))
" 2>/dev/null) || return 1
  port=${host##* }
  host=${host%% *}
  [ -n "$host" ] || return 1
  nc -z -G 3 "$host" "$port" >/dev/null 2>&1
}

if plex_port_open; then
  echo "==> Plex reachable from here - running locally"
  PY="$REPO_DIR/.venv/bin/python"
  [ -x "$PY" ] || PY=python3
  exec "$PY" "$REPO_DIR/$REL_PATH" "$@"
fi

if [ -z "${CURATARR_PLEX_SSH_HOST:-}" ]; then
  echo "ERROR: Plex isn't reachable from here and CURATARR_PLEX_SSH_HOST is unset." >&2
  echo "       Set it to the SSH alias/host of the machine running Plex (which also" >&2
  echo "       has a checkout of this repo), e.g. in ~/.zshrc:" >&2
  echo "         export CURATARR_PLEX_SSH_HOST=my-plex-box" >&2
  exit 1
fi

REMOTE_DIR="${CURATARR_PLEX_SSH_REPO_DIR:-~/dev/curatarr}"
echo "==> Plex not reachable from here - delegating to $CURATARR_PLEX_SSH_HOST"

# Args are re-quoted individually rather than pasted in as $*, so a value
# containing a space can't split into two arguments on the remote side.
REMOTE_ARGS=""
for arg in "$@"; do
  REMOTE_ARGS="$REMOTE_ARGS $(printf '%q' "$arg")"
done

# The interpreter is chosen into a variable on the REMOTE side, not
# selected here: the remote venv is the one built on that machine and so
# matches its architecture, whereas a venv on a shared mount belongs to
# whichever machine created it and will not run on the other. $PY is
# escaped so it expands there, not here. The remote login shell may be
# zsh, so this stays POSIX - no bashisms, no brace groups as commands.
exec ssh "$CURATARR_PLEX_SSH_HOST" \
  "cd $REMOTE_DIR && PY=./.venv/bin/python; [ -x \"\$PY\" ] || PY=python3; \"\$PY\" $REL_PATH$REMOTE_ARGS"
