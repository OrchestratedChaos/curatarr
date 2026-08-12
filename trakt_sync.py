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
CLI entry point for Trakt watch history sync.
Called from run.sh/run.ps1 before recommenders run.

Lives at the project root (not utils/) because it's a CLI orchestrator
that reaches into the domain layer (recommenders.external), not a
shared utility - see curatarr_app.py, the other root-level entry point,
for the same rationale.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from recommenders.external import sync_watch_history_to_trakt
from utils.config import get_tmdb_config, load_config


def main():
    """Sync Plex watch history to Trakt."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(project_root, "config/config.yml")

    config = load_config(config_path)
    tmdb_api_key = get_tmdb_config(config)["api_key"]
    sync_watch_history_to_trakt(config, tmdb_api_key)


if __name__ == "__main__":
    main()
