#!/usr/bin/env python3
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
