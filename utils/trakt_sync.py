#!/usr/bin/env python3
"""
CLI entry point for Trakt watch history sync.
Called from run.sh before recommenders run.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import load_config, get_tmdb_config
from recommenders.external import sync_watch_history_to_trakt


def main():
    """Sync Plex watch history to Trakt."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, 'config/config.yml')

    config = load_config(config_path)
    tmdb_api_key = get_tmdb_config(config)['api_key']
    sync_watch_history_to_trakt(config, tmdb_api_key)


if __name__ == "__main__":
    main()
