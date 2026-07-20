"""
Tests for utils/user_migration.py - Plex account rename detection and
migration of per-user config/cache/collection artifacts (issue #153).
"""

import json
import os

import plexapi.exceptions
import pytest
from unittest.mock import Mock, patch

from utils.user_migration import (
    load_user_id_map,
    save_user_id_map,
    get_live_plex_user_map,
    detect_renamed_users,
    rename_user_preferences_key,
    rename_user_in_users_list,
    migrate_cache_files,
    cleanup_orphaned_user_collections,
    migrate_renamed_plex_users,
)


SAMPLE_CONFIG_TEXT = """# Curatarr Configuration
# Core settings - see tuning.yml for display/scoring options

plex:
  url: https://example.plex.direct:32400
  token: test-token
  movie_library: Movies
  tv_library: TV Shows
tmdb:
  api_key: test-key
users:
  list: jasonsmith523, ericarutyunov, homehouse165
  preferences:
    jasonsmith523:
      display_name: Jason
      exclude_genres:
      - romance
      - children
    ericarutyunov:
      display_name: Eric
    homehouse165:
      display_name: Home
      # max_rating: PG-13  # Optional: filter out R, NC-17
general:
  confirm_operations: false
  plex_only: true

# Huntarr: Find missing/upcoming movies from collections
huntarr:
  sequel_huntarr: true
  horizon_huntarr: true
"""


# ---------------------------------------------------------------------------
# Stable id <-> username map persistence
# ---------------------------------------------------------------------------

class TestLoadSaveUserIdMap:
    def test_load_missing_file_returns_empty(self, tmp_path):
        result = load_user_id_map(str(tmp_path))
        assert result == {}

    def test_save_then_load_roundtrip(self, tmp_path):
        id_map = {"1": "jasonsmith523", "2": "ericarutyunov"}
        save_user_id_map(str(tmp_path), id_map)

        loaded = load_user_id_map(str(tmp_path))

        assert loaded == id_map

    def test_load_corrupt_json_returns_empty(self, tmp_path):
        path = tmp_path / "user_id_map.json"
        path.write_text("not valid json", encoding="utf-8")

        result = load_user_id_map(str(tmp_path))

        assert result == {}

    def test_load_non_dict_json_returns_empty(self, tmp_path):
        path = tmp_path / "user_id_map.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")

        result = load_user_id_map(str(tmp_path))

        assert result == {}

    def test_save_creates_cache_dir(self, tmp_path):
        cache_dir = tmp_path / "nested" / "cache"

        save_user_id_map(str(cache_dir), {"1": "user"})

        assert (cache_dir / "user_id_map.json").exists()


# ---------------------------------------------------------------------------
# Live Plex user resolution
# ---------------------------------------------------------------------------

class TestGetLivePlexUserMap:
    @patch('utils.user_migration.MyPlexAccount')
    def test_builds_map_from_account_and_users(self, mock_account_class):
        mock_account = Mock()
        mock_account.id = 1
        mock_account.username = "admin_user"

        mock_user = Mock()
        mock_user.id = 2
        mock_user.title = "renamed_user"
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        result = get_live_plex_user_map({'plex': {'token': 'tok'}})

        assert result == {"1": "admin_user", "2": "renamed_user"}

    @patch('utils.user_migration.MyPlexAccount')
    def test_returns_empty_on_api_exception(self, mock_account_class):
        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("auth failed")

        result = get_live_plex_user_map({'plex': {'token': 'bad'}})

        assert result == {}

    @patch('utils.user_migration.MyPlexAccount')
    def test_returns_empty_on_missing_config_key(self, mock_account_class):
        result = get_live_plex_user_map({'plex': {}})

        assert result == {}


# ---------------------------------------------------------------------------
# Rename detection
# ---------------------------------------------------------------------------

class TestDetectRenamedUsers:
    def test_detects_rename(self):
        previous = {"1": "oldname"}
        live = {"1": "newname"}

        result = detect_renamed_users(previous, live)

        assert result == {"oldname": "newname"}

    def test_no_op_when_username_unchanged(self):
        previous = {"1": "samename"}
        live = {"1": "samename"}

        result = detect_renamed_users(previous, live)

        assert result == {}

    def test_ignores_id_missing_from_live_map(self):
        """An id that disappeared from Plex (e.g. removed user) isn't a rename."""
        previous = {"1": "oldname"}
        live = {}

        result = detect_renamed_users(previous, live)

        assert result == {}

    def test_ignores_new_id_with_no_prior_history(self):
        previous = {}
        live = {"1": "brand_new_user"}

        result = detect_renamed_users(previous, live)

        assert result == {}

    def test_detects_multiple_renames(self):
        previous = {"1": "old1", "2": "old2", "3": "stable"}
        live = {"1": "new1", "2": "new2", "3": "stable"}

        result = detect_renamed_users(previous, live)

        assert result == {"old1": "new1", "old2": "new2"}


# ---------------------------------------------------------------------------
# config.yml text surgery
# ---------------------------------------------------------------------------

class TestRenameUserPreferencesKey:
    def test_renames_key_preserves_comments_and_formatting(self):
        new_text, changed = rename_user_preferences_key(
            SAMPLE_CONFIG_TEXT, "jasonsmith523", "jsmith_new"
        )

        assert changed is True
        assert "    jsmith_new:\n" in new_text
        assert "jasonsmith523:" not in new_text.split("preferences:")[1].split("general:")[0]
        # Everything else, including comments, must be untouched
        assert "# max_rating: PG-13  # Optional: filter out R, NC-17" in new_text
        assert "# Huntarr: Find missing/upcoming movies from collections" in new_text
        # display_name/exclude_genres values for the renamed user survive
        assert "display_name: Jason" in new_text
        assert "- romance" in new_text

    def test_other_users_preferences_untouched(self):
        new_text, changed = rename_user_preferences_key(
            SAMPLE_CONFIG_TEXT, "jasonsmith523", "jsmith_new"
        )

        assert changed is True
        assert "ericarutyunov:" in new_text
        assert "display_name: Eric" in new_text

    def test_no_change_when_user_not_present(self):
        new_text, changed = rename_user_preferences_key(
            SAMPLE_CONFIG_TEXT, "nonexistent_user", "somebody_new"
        )

        assert changed is False
        assert new_text == SAMPLE_CONFIG_TEXT

    def test_no_change_when_no_users_section(self):
        text = "plex:\n  url: http://x\n"

        new_text, changed = rename_user_preferences_key(text, "old", "new")

        assert changed is False
        assert new_text == text

    def test_no_change_when_no_preferences_block(self):
        text = "users:\n  list: alice, bob\n"

        new_text, changed = rename_user_preferences_key(text, "alice", "alicia")

        assert changed is False
        assert new_text == text

    def test_idempotent_when_already_renamed(self):
        new_text, changed = rename_user_preferences_key(
            SAMPLE_CONFIG_TEXT, "jasonsmith523", "jsmith_new"
        )
        second_text, second_changed = rename_user_preferences_key(
            new_text, "jasonsmith523", "jsmith_new"
        )

        assert second_changed is False
        assert second_text == new_text


class TestRenameUserInUsersList:
    def test_renames_comma_separated_list(self):
        new_text, changed = rename_user_in_users_list(
            SAMPLE_CONFIG_TEXT, "jasonsmith523", "jsmith_new"
        )

        assert changed is True
        list_line = [l for l in new_text.splitlines() if l.strip().startswith("list:")][0]
        assert "jsmith_new" in list_line
        assert "jasonsmith523" not in list_line
        # Other users on the same line preserved, formatting/commas intact
        assert "ericarutyunov" in list_line
        assert "homehouse165" in list_line

    def test_renames_yaml_sequence_list(self):
        text = (
            "users:\n"
            "  list:\n"
            "    - jasonsmith523\n"
            "    - ericarutyunov\n"
            "  preferences:\n"
            "    jasonsmith523:\n"
            "      display_name: Jason\n"
        )

        new_text, changed = rename_user_in_users_list(text, "jasonsmith523", "jsmith_new")

        assert changed is True
        assert "- jsmith_new\n" in new_text
        assert "- jasonsmith523\n" not in new_text
        assert "- ericarutyunov\n" in new_text

    def test_no_change_when_user_not_in_list(self):
        new_text, changed = rename_user_in_users_list(
            SAMPLE_CONFIG_TEXT, "nonexistent_user", "somebody_new"
        )

        assert changed is False
        assert new_text == SAMPLE_CONFIG_TEXT

    def test_does_not_partial_match_substring_username(self):
        """'jason' must not match inside 'jasonsmith523'."""
        new_text, changed = rename_user_in_users_list(SAMPLE_CONFIG_TEXT, "jason", "renamed")

        assert changed is False
        assert new_text == SAMPLE_CONFIG_TEXT


# ---------------------------------------------------------------------------
# Cache file migration
# ---------------------------------------------------------------------------

class TestMigrateCacheFiles:
    def test_renames_existing_cache_files(self, tmp_path):
        old_file = tmp_path / "watched_cache_plex_oldname.json"
        old_file.write_text('{"watched_count": 5}', encoding="utf-8")

        migrate_cache_files(str(tmp_path), "oldname", "newname")

        assert not old_file.exists()
        new_file = tmp_path / "watched_cache_plex_newname.json"
        assert new_file.exists()
        assert json.loads(new_file.read_text(encoding="utf-8")) == {"watched_count": 5}

    def test_migrates_multiple_known_patterns(self, tmp_path):
        for pattern in (
            "watched_cache_plex_oldname.json",
            "tv_watched_cache_plex_oldname.json",
            "external_recs_oldname_movies.json",
            "external_recs_oldname_shows.json",
        ):
            (tmp_path / pattern).write_text("{}", encoding="utf-8")

        migrate_cache_files(str(tmp_path), "oldname", "newname")

        for pattern in (
            "watched_cache_plex_newname.json",
            "tv_watched_cache_plex_newname.json",
            "external_recs_newname_movies.json",
            "external_recs_newname_shows.json",
        ):
            assert (tmp_path / pattern).exists()

    def test_removes_stale_when_new_already_exists(self, tmp_path):
        old_file = tmp_path / "watched_cache_plex_oldname.json"
        new_file = tmp_path / "watched_cache_plex_newname.json"
        old_file.write_text('{"stale": true}', encoding="utf-8")
        new_file.write_text('{"fresh": true}', encoding="utf-8")

        migrate_cache_files(str(tmp_path), "oldname", "newname")

        assert not old_file.exists()
        assert json.loads(new_file.read_text(encoding="utf-8")) == {"fresh": True}

    def test_noop_when_no_cache_files_exist(self, tmp_path):
        # Should not raise even if nothing to migrate
        migrate_cache_files(str(tmp_path), "oldname", "newname")

        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Orphaned collection cleanup
# ---------------------------------------------------------------------------

class TestCleanupOrphanedUserCollections:
    @patch('utils.user_migration.cleanup_old_collections')
    @patch('utils.user_migration.init_plex')
    def test_cleans_up_both_libraries(self, mock_init_plex, mock_cleanup):
        mock_plex = Mock()
        mock_movie_section = Mock()
        mock_tv_section = Mock()
        mock_plex.library.section.side_effect = lambda title: {
            'Movies': mock_movie_section,
            'TV Shows': mock_tv_section,
        }[title]
        mock_init_plex.return_value = mock_plex

        config = {'plex': {'movie_library': 'Movies', 'tv_library': 'TV Shows'}}

        cleanup_orphaned_user_collections(config, "oldname", "Old Display")

        # Called once per library section (display name != username -> also
        # cleaned up under the raw username pattern)
        assert mock_cleanup.call_count == 4

    @patch('utils.user_migration.init_plex')
    def test_handles_connection_failure_gracefully(self, mock_init_plex):
        mock_init_plex.side_effect = Exception("connection refused")

        # Should not raise
        cleanup_orphaned_user_collections({'plex': {}}, "oldname", "Old")

    @patch('utils.user_migration.cleanup_old_collections')
    @patch('utils.user_migration.init_plex')
    def test_handles_missing_library_gracefully(self, mock_init_plex, mock_cleanup):
        mock_plex = Mock()
        mock_plex.library.section.side_effect = plexapi.exceptions.PlexApiException("not found")
        mock_init_plex.return_value = mock_plex

        # Should not raise even though every section lookup fails
        cleanup_orphaned_user_collections({'plex': {}}, "oldname", "Old")

        mock_cleanup.assert_not_called()

    @patch('utils.user_migration.cleanup_old_collections')
    @patch('utils.user_migration.init_plex')
    def test_skips_duplicate_call_when_display_name_matches_username(self, mock_init_plex, mock_cleanup):
        mock_plex = Mock()
        mock_plex.library.section.return_value = Mock()
        mock_init_plex.return_value = mock_plex

        cleanup_orphaned_user_collections({'plex': {}}, "sameword", "sameword")

        # One call per library (2), not two per library
        assert mock_cleanup.call_count == 2


# ---------------------------------------------------------------------------
# Orchestrator: migrate_renamed_plex_users
# ---------------------------------------------------------------------------

class TestMigrateRenamedPlexUsers:
    def _write_config(self, tmp_path):
        config_path = tmp_path / "config.yml"
        config_path.write_text(SAMPLE_CONFIG_TEXT, encoding="utf-8")
        return str(config_path)

    @patch('utils.user_migration.cleanup_orphaned_user_collections')
    @patch('utils.user_migration.get_live_plex_user_map')
    def test_migrates_preferences_and_list_on_rename(self, mock_live_map, mock_cleanup, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "jasonsmith523"})
        mock_live_map.return_value = {"1": "jsmith_new"}

        config_path = self._write_config(tmp_path)
        import yaml
        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert renames == {"jasonsmith523": "jsmith_new"}

        new_text = open(config_path, encoding="utf-8").read()
        assert "jsmith_new:" in new_text
        assert "list: jsmith_new, ericarutyunov, homehouse165" in new_text

        updated_map = load_user_id_map(str(cache_dir))
        assert updated_map == {"1": "jsmith_new"}

        mock_cleanup.assert_called_once()
        call_args = mock_cleanup.call_args[0]
        assert call_args[1] == "jasonsmith523"

    @patch('utils.user_migration.cleanup_orphaned_user_collections')
    @patch('utils.user_migration.get_live_plex_user_map')
    def test_migrates_cache_files_on_rename(self, mock_live_map, mock_cleanup, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "jasonsmith523"})
        (cache_dir / "watched_cache_plex_jasonsmith523.json").write_text("{}", encoding="utf-8")
        mock_live_map.return_value = {"1": "jsmith_new"}

        config_path = self._write_config(tmp_path)
        import yaml
        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert not (cache_dir / "watched_cache_plex_jasonsmith523.json").exists()
        assert (cache_dir / "watched_cache_plex_jsmith_new.json").exists()

    @patch('utils.user_migration.cleanup_orphaned_user_collections')
    @patch('utils.user_migration.get_live_plex_user_map')
    def test_cleans_up_orphan_collection_for_old_name(self, mock_live_map, mock_cleanup, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "jasonsmith523"})
        mock_live_map.return_value = {"1": "jsmith_new"}

        config_path = self._write_config(tmp_path)
        import yaml
        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        mock_cleanup.assert_called_once()
        args = mock_cleanup.call_args[0]
        # (config, old_username, old_display_name)
        assert args[1] == "jasonsmith523"
        assert args[2] == "Jason"  # display_name captured before the rewrite

    @patch('utils.user_migration.cleanup_orphaned_user_collections')
    @patch('utils.user_migration.get_live_plex_user_map')
    def test_no_op_when_username_unchanged(self, mock_live_map, mock_cleanup, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "jasonsmith523"})
        mock_live_map.return_value = {"1": "jasonsmith523"}

        config_path = self._write_config(tmp_path)
        original_text = open(config_path, encoding="utf-8").read()
        import yaml
        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert renames == {}
        mock_cleanup.assert_not_called()
        assert open(config_path, encoding="utf-8").read() == original_text

    @patch('utils.user_migration.get_live_plex_user_map')
    def test_graceful_fallback_when_id_unavailable(self, mock_live_map, tmp_path):
        """If Plex ids can't be resolved this run, fall back to today's
        username-keyed behavior - no crash, nothing migrated."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        mock_live_map.return_value = {}

        config_path = self._write_config(tmp_path)
        original_text = open(config_path, encoding="utf-8").read()
        import yaml
        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert renames == {}
        assert open(config_path, encoding="utf-8").read() == original_text

    @patch('utils.user_migration.get_live_plex_user_map')
    def test_first_run_populates_map_without_migrating(self, mock_live_map, tmp_path):
        """With no prior map file, nothing is a 'rename' yet - just seed
        the map for future comparison."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        mock_live_map.return_value = {"1": "jasonsmith523"}

        config_path = self._write_config(tmp_path)
        import yaml
        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert renames == {}
        assert load_user_id_map(str(cache_dir)) == {"1": "jasonsmith523"}

    @patch('utils.user_migration.get_live_plex_user_map')
    def test_never_raises_on_unexpected_error(self, mock_live_map, tmp_path):
        mock_live_map.side_effect = RuntimeError("boom")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Should not raise
        renames = migrate_renamed_plex_users({'users': {}}, str(tmp_path / "config.yml"), str(cache_dir))

        assert renames == {}
