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

"""Tests for trakt_sync.py (project-root CLI orchestrator - moved out of
utils/ since it reaches into the domain layer, recommenders.external)."""

from unittest.mock import patch


class TestTraktSyncMain:
    """Tests for trakt_sync main function."""

    @patch("trakt_sync.sync_watch_history_to_trakt")
    @patch("trakt_sync.get_tmdb_config")
    @patch("trakt_sync.load_config")
    def test_main_loads_config_and_syncs(self, mock_load, mock_get_tmdb, mock_sync):
        """Test main function loads config and calls sync."""
        from trakt_sync import main

        mock_load.return_value = {"trakt": {"enabled": True}}
        mock_get_tmdb.return_value = {"api_key": "test_key"}

        main()

        mock_load.assert_called_once()
        mock_get_tmdb.assert_called_once()
        mock_sync.assert_called_once()

    @patch("trakt_sync.sync_watch_history_to_trakt")
    @patch("trakt_sync.get_tmdb_config")
    @patch("trakt_sync.load_config")
    def test_main_passes_correct_args(self, mock_load, mock_get_tmdb, mock_sync):
        """Test main passes correct arguments to sync function."""
        from trakt_sync import main

        config = {"trakt": {"enabled": True}, "plex": {}}
        mock_load.return_value = config
        mock_get_tmdb.return_value = {"api_key": "my_api_key"}

        main()

        mock_sync.assert_called_once_with(config, "my_api_key")
