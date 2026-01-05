"""Tests for utils/trakt_sync.py"""

import pytest
from unittest.mock import Mock, patch, MagicMock


class TestTraktSyncMain:
    """Tests for trakt_sync main function."""

    @patch('utils.trakt_sync.sync_watch_history_to_trakt')
    @patch('utils.trakt_sync.get_tmdb_config')
    @patch('utils.trakt_sync.load_config')
    def test_main_loads_config_and_syncs(self, mock_load, mock_get_tmdb, mock_sync):
        """Test main function loads config and calls sync."""
        from utils.trakt_sync import main

        mock_load.return_value = {'trakt': {'enabled': True}}
        mock_get_tmdb.return_value = {'api_key': 'test_key'}

        main()

        mock_load.assert_called_once()
        mock_get_tmdb.assert_called_once()
        mock_sync.assert_called_once()

    @patch('utils.trakt_sync.sync_watch_history_to_trakt')
    @patch('utils.trakt_sync.get_tmdb_config')
    @patch('utils.trakt_sync.load_config')
    def test_main_passes_correct_args(self, mock_load, mock_get_tmdb, mock_sync):
        """Test main passes correct arguments to sync function."""
        from utils.trakt_sync import main

        config = {'trakt': {'enabled': True}, 'plex': {}}
        mock_load.return_value = config
        mock_get_tmdb.return_value = {'api_key': 'my_api_key'}

        main()

        mock_sync.assert_called_once_with(config, 'my_api_key')
