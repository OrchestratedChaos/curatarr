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

"""Dumps the PrivateCollection_* labels actually present on Plex
collections next to the exclude-filter strings actually stored on each
shared account, so a label/filter mismatch (see utils/plex_policy.py's
apply_user_label_restrictions) is visible at a glance instead of having
to cross-reference Plex's web UI and the Plex API by hand.

Read-only: makes GET requests to the configured Plex server and to
plex.tv/api/users only. Never writes to Plex (no label/collection/filter
changes) and never writes to config/config.yml or anywhere else on disk.

Run from the curatarr project root with the project venv active:

    python scripts/diagnose_private_labels.py

The output includes real Plex usernames and display names for every
account on the server (to line them up against each account's exclude
filters) - scrub those before pasting the output publicly (e.g. into a
GitHub issue). The Plex token itself is never printed.
"""

import os
import sys
import xml.etree.ElementTree as ET

import plexapi.exceptions
import requests
import yaml
from plexapi.server import PlexServer

# plex.tv's own HTTP timeout has no documented upper bound worth trusting -
# this just keeps a dead/unreachable plex.tv from hanging the script forever.
REQUEST_TIMEOUT_SECONDS = 30

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config", "config.yml")


def _load_config():
    """Load config/config.yml and return (config, error_message). Exactly
    one of the two is non-None - never raises."""
    if not os.path.exists(CONFIG_PATH):
        return None, f"Error: config file not found at {CONFIG_PATH} - run this from the curatarr project root."

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        return None, f"Error: could not read {CONFIG_PATH}: {e}"

    if not isinstance(config, dict):
        return None, f"Error: {CONFIG_PATH} did not parse as a YAML mapping."

    return config, None


def _connect_plex(config):
    """Validate plex.url/plex.token and connect. Returns (plex, movie_lib,
    tv_lib, error_message); exactly one of (plex, ...) / error_message is
    non-None - never raises."""
    plex_config = config.get("plex")
    if not isinstance(plex_config, dict):
        return None, None, None, f"Error: {CONFIG_PATH} has no 'plex:' section."

    url = plex_config.get("url")
    token = plex_config.get("token")
    if not url:
        return None, None, None, f"Error: {CONFIG_PATH}'s 'plex:' section has no 'url' set."
    if not token:
        return None, None, None, f"Error: {CONFIG_PATH}'s 'plex:' section has no 'token' set."

    try:
        plex = PlexServer(url, token)
    except Exception as e:  # noqa: BLE001 - plexapi/requests can raise many types on connect
        return None, None, None, f"Error: could not connect to Plex server at {url}: {e}"

    movie_lib = plex_config.get("movie_library", "Movies")
    tv_lib = plex_config.get("tv_library", "TV Shows")
    return plex, movie_lib, tv_lib, None


def _print_collection_labels(plex, libraries):
    """Prints every PrivateCollection_* label found on collections in the
    given library names, grouped by label. A library that doesn't exist on
    the server is reported and skipped, not fatal - the other library (and
    the account filter dump) can still be useful."""
    print("=== Labels actually on collections ===")

    collection_labels = {}
    for lib_name in libraries:
        try:
            section = plex.library.section(lib_name)
        except plexapi.exceptions.NotFound:
            print(f"  Error: library '{lib_name}' not found on this Plex server - skipping")
            continue

        for c in section.collections():
            for label in c.labels:
                if label.tag.startswith("PrivateCollection"):
                    collection_labels.setdefault(label.tag, []).append(f"{lib_name}/{c.title}")

    if not collection_labels:
        print("  (none found)")
    for label, cols in sorted(collection_labels.items()):
        print(f"  {label!r} <- {cols}")


def _print_account_filters(token):
    """Prints filterMovies/filterTelevision for every account on
    plex.tv/api/users. Prints a one-line error (no traceback) instead of
    raising on a network failure or a non-200 response."""
    print("\n=== Live exclude filters per account ===")

    try:
        resp = requests.get(
            "https://plex.tv/api/users",
            params={"X-Plex-Token": token},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        print(f"  Error: could not reach plex.tv/api/users: {e}")
        return False

    if resp.status_code != 200:
        print(f"  Error: plex.tv/api/users returned HTTP {resp.status_code} (expected 200)")
        return False

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"  Error: could not parse plex.tv/api/users response: {e}")
        return False

    for u in root.findall(".//User"):
        print(f"  id={u.get('id')} title={u.get('title')!r} username={u.get('username')!r}")
        print(f"      filterMovies     = {u.get('filterMovies', '')!r}")
        print(f"      filterTelevision = {u.get('filterTelevision', '')!r}")

    return True


def main():
    config, error = _load_config()
    if error:
        print(error, file=sys.stderr)
        return 1

    plex, movie_lib, tv_lib, error = _connect_plex(config)
    if error:
        print(error, file=sys.stderr)
        return 1

    _print_collection_labels(plex, (movie_lib, tv_lib))
    ok = _print_account_filters(config["plex"]["token"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
