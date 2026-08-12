#!/usr/bin/env python3
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

"""
Trakt Authentication Helper.

Run this script to authenticate with Trakt using device code flow.
Tokens are saved to trakt.yml for future use.
"""

import argparse
import os
import sys
import time
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import load_config as _load_canonical_config
from utils.helpers import get_project_root
from utils.trakt import TraktAuthError, TraktClient, save_trakt_tokens


def get_config_dir():
    """Get the config directory path.

    Delegates to utils.helpers.get_project_root() - CURATARR_CONFIG_DIR
    override for Docker, ~/.curatarr for a frozen binary, repo root for
    a source checkout (see that function's own docstring) - instead of
    this script's own directory. This used to always resolve to
    dirname(dirname(__file__)), i.e. wherever trakt_auth.py itself
    lives on disk: correct for a source checkout, but /app in Docker -
    the code's fixed WORKDIR, not the separately mounted /data volume
    config/trakt.yml actually lives in (confirmed in a real container -
    the same code_root-vs-project_root divergence #260 already fixed
    for web/job_runner.py's JobManager, just never touched here). A
    `docker exec ... python3 -m utils.trakt_auth` before this fix would
    have silently read/written /app/config/trakt.yml - a path that
    isn't even bind-mounted, so it neither sees the real existing
    config nor persists whatever it saves past the container's life.
    """
    return os.path.join(get_project_root(), "config")


def load_config():
    """Load config via the canonical utils.config.load_config().

    Delegates to the same app-wide loader everything else uses (merges
    tuning.yml/trakt.yml/radarr.yml/sonarr.yml, auto-migrates legacy
    monolithic configs, and applies PLEX_URL/PLEX_TOKEN/TMDB_API_KEY
    env-var overrides) instead of a second, narrower yaml.safe_load of
    config.yml + trakt.yml - previously Trakt device-auth saw a
    different config than the rest of the app (missed env-var overrides
    and un-migrated legacy configs, wrong for Docker/env-var installs).
    """
    config_path = os.path.join(get_config_dir(), "config.yml")
    return _load_canonical_config(config_path)


def save_tokens(
    access_token: str,
    refresh_token: str,
    created_at: Optional[int] = None,
    expires_in: Optional[int] = None,
):
    """Save tokens to trakt.yml.

    Thin wrapper around utils.trakt.save_trakt_tokens - the SAME
    persistence TraktClient's own runtime token_callback uses when a
    token is refreshed mid-run (see utils/trakt.py's
    create_trakt_client) - so a manual `python3 utils/trakt_auth.py`
    re-auth and an automatic in-run refresh persist identically instead
    of two divergent implementations (this used to have its own
    separate yaml.safe_load/dump + harden_file_permissions copy).
    """
    trakt_path = os.path.join(get_config_dir(), "trakt.yml")
    save_trakt_tokens(trakt_path, access_token, refresh_token, created_at, expires_in)
    print("\033[92mTokens saved to config/trakt.yml\033[0m")


# How often poll_for_token's on_wait callback (utils/trakt.py) is
# allowed to print a "still waiting" progress line - throttled well
# below the polling interval itself (typically 5s from Trakt) so a
# ~10-minute wait doesn't spam ~120 lines, while still giving a user
# watching a log/SSH session something periodic to distinguish
# "working" from "hung" - see main()'s _on_wait below.
PROGRESS_PRINT_INTERVAL_SECONDS = 30


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Trakt device-code authentication for Curatarr")
    parser.add_argument(
        "--reauth",
        "--force",
        dest="reauth",
        action="store_true",
        help=(
            "Re-authenticate even if config/trakt.yml already has an access_token "
            "(e.g. after a refresh failure/rejected token) instead of requiring a "
            "manual hand-edit of the file first."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    """argv defaults to sys.argv[1:] (argparse's own default) - an
    explicit list (e.g. []) lets tests/callers invoke this without
    picking up whatever's actually in sys.argv (e.g. pytest's own CLI
    args when this is called directly from a test)."""
    args = parse_args(argv)

    # Line-buffer stdout so every print below (in particular the
    # device code/verification URL just before the blocking
    # poll_for_token call) reaches whoever's watching IMMEDIATELY,
    # rather than sitting in a buffer until the process eventually
    # exits. CPython fully block-buffers stdout whenever it isn't a
    # real terminal (`ssh host "python3 -m utils.trakt_auth --reauth" >
    # log` with no -t, `docker exec` without -t, any captured/piped
    # invocation) - that silently held the device code and activation
    # URL in memory for the entire (up to 10-minute) polling wait,
    # with nothing ever appearing for the user to act on. Reconfigured
    # here (the entry point), not via a global PYTHONUNBUFFERED/-u
    # requirement, so this is correct standalone regardless of how the
    # caller invokes it. hasattr-guarded since not every sys.stdout
    # stand-in (e.g. some test/capture harnesses) implements
    # reconfigure().
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    print("\033[96m=== Trakt Authentication ===\033[0m")
    print()

    # Load config
    try:
        config = load_config()
    except FileNotFoundError:
        print("\033[91mError: config/config.yml not found. Run ./run.sh first.\033[0m")
        sys.exit(1)

    trakt_config = config.get("trakt", {})

    if not trakt_config.get("enabled", False):
        print("\033[93mTrakt is disabled. Create config/trakt.yml to enable it.\033[0m")
        print("See config/trakt.example.yml for template.")
        sys.exit(1)

    client_id = trakt_config.get("client_id")
    client_secret = trakt_config.get("client_secret")

    if not client_id or not client_secret or client_id == "null" or client_secret == "null":
        print("\033[91mError: Trakt client_id or client_secret not configured.\033[0m")
        print("Add them to config/trakt.yml")
        sys.exit(1)

    # Check if already authenticated - skipped entirely with --reauth/--force
    # (e.g. after a refresh failure/rejected token: Docker/frozen-binary
    # users can't reasonably hand-edit trakt.yml and shell in to remove
    # access_token first, and even a source install shouldn't have to).
    if not args.reauth and trakt_config.get("access_token") and trakt_config.get("access_token") != "null":
        print("Already authenticated!")
        print("To re-authenticate, run: python3 -m utils.trakt_auth --reauth")
        sys.exit(0)

    # Create client
    client = TraktClient(client_id=client_id, client_secret=client_secret, token_callback=save_tokens)

    # Start device auth flow
    try:
        print("Requesting device code from Trakt...")
        device_info = client.get_device_code()

        print()
        print("\033[96m1. Go to: \033[93m" + device_info["verification_url"] + "\033[0m")
        print("\033[96m2. Enter code: \033[93m" + device_info["user_code"] + "\033[0m")
        print()
        print("Waiting for authorization...")
        print("(Press Ctrl+C to cancel)")
        print()

        # Periodic "still here" progress so a user watching this over
        # SSH/a log/docker logs can tell an in-progress wait apart from
        # a hung one - throttled to PROGRESS_PRINT_INTERVAL_SECONDS
        # (poll_for_token's own interval, normally 5s, would otherwise
        # print far too often over a wait that can last minutes).
        last_progress_at = time.monotonic()

        def _on_wait() -> None:
            nonlocal last_progress_at
            now = time.monotonic()
            if now - last_progress_at >= PROGRESS_PRINT_INTERVAL_SECONDS:
                print("...still waiting for approval")
                last_progress_at = now

        # Poll for token
        success = client.poll_for_token(
            device_code=device_info["device_code"],
            interval=device_info.get("interval", 5),
            expires_in=device_info.get("expires_in", 600),
            on_wait=_on_wait,
        )

        if success:
            print()
            print("\033[92m=== Authentication Successful! ===\033[0m")
            print()
            username = client.get_username()
            if username:
                print(f"Logged in as: {username}")
            print("Trakt integration is now ready to use.")
        else:
            print("\033[91mAuthentication failed or expired.\033[0m")
            print("Please try again.")
            sys.exit(1)

    except TraktAuthError as e:
        print(f"\033[91mAuthentication error: {e}\033[0m")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\033[93mCancelled.\033[0m")
        sys.exit(1)


if __name__ == "__main__":
    main()
