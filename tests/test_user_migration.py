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
Tests for utils/user_migration.py - Plex account rename detection and
migration of per-user config/cache/collection artifacts (issue #153).
"""

import json
from unittest.mock import Mock, patch

import plexapi.exceptions

from utils.user_migration import (
    cleanup_orphaned_user_collections,
    compute_rename_transitions,
    detect_renamed_users,
    get_live_plex_user_map,
    load_user_id_map,
    migrate_cache_files,
    migrate_renamed_plex_users,
    rename_user_in_managed_users,
    rename_user_in_users_list,
    rename_user_preferences_key,
    save_user_id_map,
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
  list: testuser_alpha, testuser_bravo, testuser_charlie
  preferences:
    testuser_alpha:
      display_name: Alpha
      exclude_genres:
      - romance
      - children
    testuser_bravo:
      display_name: Bravo
    testuser_charlie:
      display_name: Charlie
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
        id_map = {
            "1": {"username": "testuser_alpha", "pending": None},
            "2": {"username": "testuser_bravo", "pending": "testuser_bravo_new"},
        }
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

        save_user_id_map(str(cache_dir), {"1": {"username": "user", "pending": None}})

        assert (cache_dir / "user_id_map.json").exists()

    def test_legacy_flat_format_upgrades_on_load(self, tmp_path):
        """#352: a pre-#352 file stored this flat ({id: username}, no
        debounce state) - loading it must upgrade every entry in place
        rather than dropping the install's existing rename history."""
        path = tmp_path / "user_id_map.json"
        path.write_text('{"1": "testuser_alpha", "2": "testuser_bravo"}', encoding="utf-8")

        result = load_user_id_map(str(tmp_path))

        assert result == {
            "1": {"username": "testuser_alpha", "pending": None},
            "2": {"username": "testuser_bravo", "pending": None},
        }

    def test_malformed_entries_are_dropped_not_fatal(self, tmp_path):
        path = tmp_path / "user_id_map.json"
        path.write_text(
            '{"1": {"username": "alice", "pending": null}, "2": "", "3": {"pending": "x"}, "4": 42}',
            encoding="utf-8",
        )

        result = load_user_id_map(str(tmp_path))

        assert result == {"1": {"username": "alice", "pending": None}}


# ---------------------------------------------------------------------------
# Live Plex user resolution
# ---------------------------------------------------------------------------


class TestGetLivePlexUserMap:
    @patch("utils.user_migration.MyPlexAccount")
    def test_builds_map_from_account_and_users(self, mock_account_class):
        mock_account = Mock()
        mock_account.id = 1
        mock_account.username = "admin_user"

        mock_user = Mock()
        mock_user.id = 2
        mock_user.title = "renamed_user"
        mock_account.users.return_value = [mock_user]
        mock_account_class.return_value = mock_account

        result = get_live_plex_user_map({"plex": {"token": "tok"}})

        assert result == {"1": "admin_user", "2": "renamed_user"}

    @patch("utils.user_migration.MyPlexAccount")
    def test_returns_empty_on_api_exception(self, mock_account_class):
        mock_account_class.side_effect = plexapi.exceptions.PlexApiException("auth failed")

        result = get_live_plex_user_map({"plex": {"token": "bad"}})

        assert result == {}

    @patch("utils.user_migration.MyPlexAccount")
    def test_returns_empty_on_missing_config_key(self, mock_account_class):
        result = get_live_plex_user_map({"plex": {}})

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


class TestComputeRenameTransitions:
    """#352: the debounced rename detector migrate_renamed_plex_users
    actually calls - a single differing observation is remembered as
    "pending" but never confirmed until the SAME new value is seen on a
    second, later run."""

    def test_first_observation_is_not_a_rename(self):
        previous = {"1": {"username": "alexpigot", "pending": None}}
        live = {"1": "Alex Pigot"}

        confirmed, updated = compute_rename_transitions(previous, live)

        assert confirmed == {}
        assert updated == {"1": {"username": "alexpigot", "pending": "Alex Pigot"}}

    def test_second_matching_observation_confirms_the_rename(self):
        """Simulates two consecutive runs: run 1 sees the flap and
        records it pending (previous state here already reflects that),
        run 2 sees the identical new value again."""
        previous = {"1": {"username": "alexpigot", "pending": "Alex Pigot"}}
        live = {"1": "Alex Pigot"}

        confirmed, updated = compute_rename_transitions(previous, live)

        assert confirmed == {"alexpigot": "Alex Pigot"}
        assert updated == {"1": {"username": "Alex Pigot", "pending": None}}

    def test_a_flap_back_to_baseline_clears_pending_without_renaming(self):
        """alexpigot -> Alex Pigot (pending) -> alexpigot again: back to
        the original value, never confirmed, and the flap is forgotten
        rather than lingering to falsely corroborate a later run."""
        previous = {"1": {"username": "alexpigot", "pending": "Alex Pigot"}}
        live = {"1": "alexpigot"}

        confirmed, updated = compute_rename_transitions(previous, live)

        assert confirmed == {}
        assert updated == {"1": {"username": "alexpigot", "pending": None}}

    def test_a_third_different_value_restarts_the_pending_count(self):
        """A first flap to "Alex Pigot" must not let a later, DIFFERENT
        flap to "AlexP" get corroborated against the stale pending value
        - two DIFFERENT wrong guesses must never compound into a
        confirmed rename."""
        previous = {"1": {"username": "alexpigot", "pending": "Alex Pigot"}}
        live = {"1": "AlexP"}

        confirmed, updated = compute_rename_transitions(previous, live)

        assert confirmed == {}
        assert updated == {"1": {"username": "alexpigot", "pending": "AlexP"}}

    def test_no_op_when_username_unchanged(self):
        previous = {"1": {"username": "samename", "pending": None}}
        live = {"1": "samename"}

        confirmed, updated = compute_rename_transitions(previous, live)

        assert confirmed == {}
        assert updated == {"1": {"username": "samename", "pending": None}}

    def test_id_missing_from_live_map_is_carried_over_unchanged(self):
        """An id that disappeared from Plex this run (API hiccup, or a
        genuine departure - #351/#354 handle that separately) must not
        lose its history, including any not-yet-corroborated pending
        value."""
        previous = {"1": {"username": "oldname", "pending": "maybe_new"}}

        confirmed, updated = compute_rename_transitions(previous, {})

        assert confirmed == {}
        assert updated == previous
        assert updated is not previous  # never mutates the caller's dict in place

    def test_new_id_with_no_prior_history_seeds_baseline_without_renaming(self):
        confirmed, updated = compute_rename_transitions({}, {"1": "brand_new_user"})

        assert confirmed == {}
        assert updated == {"1": {"username": "brand_new_user", "pending": None}}

    def test_legacy_flat_entry_shape_from_load_user_id_map_still_works(self):
        """previous_map here is exactly what load_user_id_map returns,
        including its own back-compat upgrade of a pre-#352 flat file -
        confirms the two functions' shapes actually agree."""
        previous = {"1": {"username": "alexpigot", "pending": None}}

        confirmed, updated = compute_rename_transitions(previous, {"1": "alexpigot"})

        assert confirmed == {}
        assert updated == previous


# ---------------------------------------------------------------------------
# config.yml text surgery
# ---------------------------------------------------------------------------


class TestRenameUserPreferencesKey:
    def test_renames_key_preserves_comments_and_formatting(self):
        new_text, changed = rename_user_preferences_key(SAMPLE_CONFIG_TEXT, "testuser_alpha", "jsmith_new")

        assert changed is True
        assert "    jsmith_new:\n" in new_text
        assert "testuser_alpha:" not in new_text.split("preferences:")[1].split("general:")[0]
        # Everything else, including comments, must be untouched
        assert "# max_rating: PG-13  # Optional: filter out R, NC-17" in new_text
        assert "# Huntarr: Find missing/upcoming movies from collections" in new_text
        # display_name/exclude_genres values for the renamed user survive
        assert "display_name: Alpha" in new_text
        assert "- romance" in new_text

    def test_other_users_preferences_untouched(self):
        new_text, changed = rename_user_preferences_key(SAMPLE_CONFIG_TEXT, "testuser_alpha", "jsmith_new")

        assert changed is True
        assert "testuser_bravo:" in new_text
        assert "display_name: Bravo" in new_text

    def test_no_change_when_user_not_present(self):
        new_text, changed = rename_user_preferences_key(SAMPLE_CONFIG_TEXT, "nonexistent_user", "somebody_new")

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
        new_text, changed = rename_user_preferences_key(SAMPLE_CONFIG_TEXT, "testuser_alpha", "jsmith_new")
        second_text, second_changed = rename_user_preferences_key(new_text, "testuser_alpha", "jsmith_new")

        assert second_changed is False
        assert second_text == new_text


class TestRenameUserInUsersList:
    def test_renames_comma_separated_list(self):
        new_text, changed = rename_user_in_users_list(SAMPLE_CONFIG_TEXT, "testuser_alpha", "jsmith_new")

        assert changed is True
        list_line = [line for line in new_text.splitlines() if line.strip().startswith("list:")][0]
        assert "jsmith_new" in list_line
        assert "testuser_alpha" not in list_line
        # Other users on the same line preserved, formatting/commas intact
        assert "testuser_bravo" in list_line
        assert "testuser_charlie" in list_line

    def test_renames_yaml_sequence_list(self):
        text = (
            "users:\n"
            "  list:\n"
            "    - testuser_alpha\n"
            "    - testuser_bravo\n"
            "  preferences:\n"
            "    testuser_alpha:\n"
            "      display_name: Alpha\n"
        )

        new_text, changed = rename_user_in_users_list(text, "testuser_alpha", "jsmith_new")

        assert changed is True
        assert "- jsmith_new\n" in new_text
        assert "- testuser_alpha\n" not in new_text
        assert "- testuser_bravo\n" in new_text

    def test_no_change_when_user_not_in_list(self):
        new_text, changed = rename_user_in_users_list(SAMPLE_CONFIG_TEXT, "nonexistent_user", "somebody_new")

        assert changed is False
        assert new_text == SAMPLE_CONFIG_TEXT

    def test_does_not_partial_match_substring_username(self):
        """'testuser' must not match inside 'testuser_alpha' (or any of
        the other testuser_* fixture usernames) - only a full,
        word-boundary-delimited username may ever match."""
        new_text, changed = rename_user_in_users_list(SAMPLE_CONFIG_TEXT, "testuser", "renamed")

        assert changed is False
        assert new_text == SAMPLE_CONFIG_TEXT


class TestRenameUserInManagedUsers:
    """#352: legacy plex.managed_users comma-string counterpart to
    TestRenameUserInUsersList - needed once get_configured_users() stops
    re-substituting Plex's live title over the config text every run
    (see utils.plex.get_configured_users), since config.yml text itself
    is now the only place that spelling ever gets updated."""

    LEGACY_CONFIG_TEXT = (
        "plex:\n"
        "  url: https://example.plex.direct:32400\n"
        "  token: test-token\n"
        "  managed_users: alexpigot, testjunk326\n"
        "  movie_library: Movies\n"
        "tmdb:\n"
        "  api_key: test-key\n"
    )

    def test_renames_entry_in_managed_users(self):
        new_text, changed = rename_user_in_managed_users(self.LEGACY_CONFIG_TEXT, "alexpigot", "Alex Pigot")

        assert changed is True
        managed_line = [line for line in new_text.splitlines() if line.strip().startswith("managed_users:")][0]
        assert "Alex Pigot" in managed_line
        assert "alexpigot" not in managed_line
        # Other entry on the same line preserved
        assert "testjunk326" in managed_line

    def test_other_lines_untouched(self):
        new_text, changed = rename_user_in_managed_users(self.LEGACY_CONFIG_TEXT, "alexpigot", "Alex Pigot")

        assert changed is True
        assert "token: test-token" in new_text
        assert "movie_library: Movies" in new_text

    def test_no_change_when_user_not_in_managed_users(self):
        new_text, changed = rename_user_in_managed_users(self.LEGACY_CONFIG_TEXT, "nonexistent_user", "somebody_new")

        assert changed is False
        assert new_text == self.LEGACY_CONFIG_TEXT

    def test_no_change_when_no_plex_section(self):
        text = "users:\n  list: alice, bob\n"

        new_text, changed = rename_user_in_managed_users(text, "alice", "alicia")

        assert changed is False
        assert new_text == text

    def test_no_change_when_no_managed_users_key(self):
        text = "plex:\n  url: http://x\n  token: t\n"

        new_text, changed = rename_user_in_managed_users(text, "alice", "alicia")

        assert changed is False
        assert new_text == text

    def test_does_not_partial_match_substring_username(self):
        new_text, changed = rename_user_in_managed_users(self.LEGACY_CONFIG_TEXT, "alex", "renamed")

        assert changed is False
        assert new_text == self.LEGACY_CONFIG_TEXT

    def test_idempotent_when_already_renamed(self):
        new_text, changed = rename_user_in_managed_users(self.LEGACY_CONFIG_TEXT, "alexpigot", "Alex Pigot")
        second_text, second_changed = rename_user_in_managed_users(new_text, "alexpigot", "Alex Pigot")

        assert second_changed is False
        assert second_text == new_text


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
    @patch("utils.user_migration.cleanup_old_collections")
    @patch("utils.user_migration.init_plex")
    def test_cleans_up_both_libraries(self, mock_init_plex, mock_cleanup):
        mock_plex = Mock()
        mock_movie_section = Mock()
        mock_tv_section = Mock()
        mock_plex.library.section.side_effect = lambda title: {
            "Movies": mock_movie_section,
            "TV Shows": mock_tv_section,
        }[title]
        mock_init_plex.return_value = mock_plex

        config = {"plex": {"movie_library": "Movies", "tv_library": "TV Shows"}}

        cleanup_orphaned_user_collections(config, "oldname", "Old Display")

        # Called once per library section (display name != username -> also
        # cleaned up under the raw username pattern)
        assert mock_cleanup.call_count == 4

    @patch("utils.user_migration.init_plex")
    def test_handles_connection_failure_gracefully(self, mock_init_plex):
        mock_init_plex.side_effect = Exception("connection refused")

        # Should not raise
        cleanup_orphaned_user_collections({"plex": {}}, "oldname", "Old")

    @patch("utils.user_migration.cleanup_old_collections")
    @patch("utils.user_migration.init_plex")
    def test_handles_missing_library_gracefully(self, mock_init_plex, mock_cleanup):
        mock_plex = Mock()
        mock_plex.library.section.side_effect = plexapi.exceptions.PlexApiException("not found")
        mock_init_plex.return_value = mock_plex

        # Should not raise even though every section lookup fails
        cleanup_orphaned_user_collections({"plex": {}}, "oldname", "Old")

        mock_cleanup.assert_not_called()

    @patch("utils.user_migration.cleanup_old_collections")
    @patch("utils.user_migration.init_plex")
    def test_skips_duplicate_call_when_display_name_matches_username(self, mock_init_plex, mock_cleanup):
        mock_plex = Mock()
        mock_plex.library.section.return_value = Mock()
        mock_init_plex.return_value = mock_plex

        cleanup_orphaned_user_collections({"plex": {}}, "sameword", "sameword")

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

    @patch("utils.user_migration.cleanup_orphaned_user_collections")
    @patch("utils.user_migration.get_live_plex_user_map")
    def test_migrates_preferences_and_list_on_rename(self, mock_live_map, mock_cleanup, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "testuser_alpha"})
        mock_live_map.return_value = {"1": "jsmith_new"}

        config_path = self._write_config(tmp_path)
        import yaml

        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        # #352: a single differing observation only records it pending -
        # migration requires a second, later run corroborating it.
        first_renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))
        assert first_renames == {}
        mock_cleanup.assert_not_called()

        renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert renames == {"testuser_alpha": "jsmith_new"}

        new_text = open(config_path, encoding="utf-8").read()
        assert "jsmith_new:" in new_text
        assert "list: jsmith_new, testuser_bravo, testuser_charlie" in new_text

        updated_map = load_user_id_map(str(cache_dir))
        assert updated_map == {"1": {"username": "jsmith_new", "pending": None}}

        mock_cleanup.assert_called_once()
        call_args = mock_cleanup.call_args[0]
        assert call_args[1] == "testuser_alpha"

    @patch("utils.user_migration.cleanup_orphaned_user_collections")
    @patch("utils.user_migration.get_live_plex_user_map")
    def test_migrates_cache_files_on_rename(self, mock_live_map, mock_cleanup, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "testuser_alpha"})
        (cache_dir / "watched_cache_plex_testuser_alpha.json").write_text("{}", encoding="utf-8")
        mock_live_map.return_value = {"1": "jsmith_new"}

        config_path = self._write_config(tmp_path)
        import yaml

        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        migrate_renamed_plex_users(root_config, config_path, str(cache_dir))
        assert (cache_dir / "watched_cache_plex_testuser_alpha.json").exists(), "acted on an unconfirmed rename"

        migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert not (cache_dir / "watched_cache_plex_testuser_alpha.json").exists()
        assert (cache_dir / "watched_cache_plex_jsmith_new.json").exists()

    @patch("utils.user_migration.cleanup_orphaned_user_collections")
    @patch("utils.user_migration.get_live_plex_user_map")
    def test_cleans_up_orphan_collection_for_old_name(self, mock_live_map, mock_cleanup, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "testuser_alpha"})
        mock_live_map.return_value = {"1": "jsmith_new"}

        config_path = self._write_config(tmp_path)
        import yaml

        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        migrate_renamed_plex_users(root_config, config_path, str(cache_dir))
        migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        mock_cleanup.assert_called_once()
        args = mock_cleanup.call_args[0]
        # (config, old_username, old_display_name)
        assert args[1] == "testuser_alpha"
        assert args[2] == "Alpha"  # display_name captured before the rewrite

    @patch("utils.user_migration.cleanup_orphaned_user_collections")
    @patch("utils.user_migration.get_live_plex_user_map")
    def test_single_flap_never_migrates_anything(self, mock_live_map, mock_cleanup, tmp_path):
        """#352's core requirement: one differing observation must never
        touch config.yml, cache files, or Plex - only a corroborated
        (two-run) rename may."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "alexpigot"})
        (cache_dir / "watched_cache_plex_alexpigot.json").write_text("{}", encoding="utf-8")
        mock_live_map.return_value = {"1": "Alex Pigot"}

        config_path = self._write_config(tmp_path)
        import yaml

        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))
        original_text = open(config_path, encoding="utf-8").read()

        renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert renames == {}
        mock_cleanup.assert_not_called()
        assert open(config_path, encoding="utf-8").read() == original_text
        assert (cache_dir / "watched_cache_plex_alexpigot.json").exists()

        # The flap is remembered, though - a second run repeating it does
        # migrate (proven above by test_migrates_preferences_and_list_on_rename).
        assert load_user_id_map(str(cache_dir)) == {"1": {"username": "alexpigot", "pending": "Alex Pigot"}}

    @patch("utils.user_migration.cleanup_orphaned_user_collections")
    @patch("utils.user_migration.get_live_plex_user_map")
    def test_flap_reverting_to_baseline_never_migrates(self, mock_live_map, mock_cleanup, tmp_path):
        """alexpigot -> Alex Pigot -> alexpigot across three runs must
        never migrate anything - the third observation matches the
        ORIGINAL baseline, not the pending value, so it is never
        corroborated."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "alexpigot"})

        config_path = self._write_config(tmp_path)
        import yaml

        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))
        original_text = open(config_path, encoding="utf-8").read()

        mock_live_map.return_value = {"1": "Alex Pigot"}
        migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        mock_live_map.return_value = {"1": "alexpigot"}
        renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert renames == {}
        mock_cleanup.assert_not_called()
        assert open(config_path, encoding="utf-8").read() == original_text
        assert load_user_id_map(str(cache_dir)) == {"1": {"username": "alexpigot", "pending": None}}

    @patch("utils.user_migration.cleanup_orphaned_user_collections")
    @patch("utils.user_migration.get_live_plex_user_map")
    def test_no_op_when_username_unchanged(self, mock_live_map, mock_cleanup, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        save_user_id_map(str(cache_dir), {"1": "testuser_alpha"})
        mock_live_map.return_value = {"1": "testuser_alpha"}

        config_path = self._write_config(tmp_path)
        original_text = open(config_path, encoding="utf-8").read()
        import yaml

        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert renames == {}
        mock_cleanup.assert_not_called()
        assert open(config_path, encoding="utf-8").read() == original_text

    @patch("utils.user_migration.get_live_plex_user_map")
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

    @patch("utils.user_migration.get_live_plex_user_map")
    def test_first_run_populates_map_without_migrating(self, mock_live_map, tmp_path):
        """With no prior map file, nothing is a 'rename' yet - just seed
        the map for future comparison."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        mock_live_map.return_value = {"1": "testuser_alpha"}

        config_path = self._write_config(tmp_path)
        import yaml

        root_config = yaml.safe_load(open(config_path, encoding="utf-8"))

        renames = migrate_renamed_plex_users(root_config, config_path, str(cache_dir))

        assert renames == {}
        assert load_user_id_map(str(cache_dir)) == {"1": {"username": "testuser_alpha", "pending": None}}

    @patch("utils.user_migration.get_live_plex_user_map")
    def test_never_raises_on_unexpected_error(self, mock_live_map, tmp_path):
        mock_live_map.side_effect = RuntimeError("boom")
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Should not raise
        renames = migrate_renamed_plex_users({"users": {}}, str(tmp_path / "config.yml"), str(cache_dir))

        assert renames == {}
