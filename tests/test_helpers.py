"""
Tests for utils/helpers.py - Miscellaneous helper functions.
"""

import pytest
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch
from utils.config import MAX_LOG_FILE_BYTES
from utils.helpers import (
    normalize_title, map_path, cleanup_old_logs, compute_profile_hash,
    get_project_root, migrate_legacy_cache_dir, no_window_kwargs,
    TITLE_SUFFIXES_TO_STRIP,
)


class TestNormalizeTitle:
    """Tests for normalize_title() function."""

    def test_strip_4k_suffix(self):
        """Test stripping 4K suffix."""
        assert normalize_title("Avatar 4K") == "Avatar"
        assert normalize_title("Avatar 4k") == "Avatar"

    def test_strip_hd_suffix(self):
        """Test stripping HD suffix."""
        assert normalize_title("Movie HD") == "Movie"
        assert normalize_title("Movie hd") == "Movie"

    def test_strip_uhd_suffix(self):
        """Test stripping UHD suffix."""
        assert normalize_title("Film UHD") == "Film"
        assert normalize_title("Film uhd") == "Film"

    def test_strip_extended_suffix(self):
        """Test stripping Extended suffix."""
        assert normalize_title("Lord of the Rings Extended") == "Lord of the Rings"
        assert normalize_title("Movie EXTENDED") == "Movie"

    def test_strip_directors_cut(self):
        """Test stripping Director's Cut suffix."""
        assert normalize_title("Blade Runner Director's Cut") == "Blade Runner"
        assert normalize_title("Blade Runner Directors Cut") == "Blade Runner"

    def test_strip_theatrical(self):
        """Test stripping Theatrical suffix."""
        assert normalize_title("Movie Theatrical") == "Movie"

    def test_strip_unrated(self):
        """Test stripping Unrated suffix."""
        assert normalize_title("Comedy Unrated") == "Comedy"
        assert normalize_title("Comedy UNRATED") == "Comedy"

    def test_strip_remastered(self):
        """Test stripping Remastered suffix."""
        assert normalize_title("Classic Remastered") == "Classic"
        assert normalize_title("Classic REMASTERED") == "Classic"

    def test_strip_special_edition(self):
        """Test stripping Special Edition suffix."""
        assert normalize_title("Star Wars Special Edition") == "Star Wars"

    def test_strip_imax(self):
        """Test stripping IMAX suffix."""
        assert normalize_title("Dune IMAX") == "Dune"

    def test_strip_3d(self):
        """Test stripping 3D suffix."""
        assert normalize_title("Avatar 3D") == "Avatar"
        assert normalize_title("Avatar 3d") == "Avatar"

    def test_no_suffix_unchanged(self):
        """Test that titles without suffixes are unchanged."""
        assert normalize_title("The Matrix") == "The Matrix"
        assert normalize_title("Inception") == "Inception"

    def test_empty_string(self):
        """Test handling of empty string."""
        assert normalize_title("") == ""

    def test_none_input(self):
        """Test handling of None input."""
        assert normalize_title(None) is None

    def test_whitespace_handling(self):
        """Test that whitespace is handled properly."""
        assert normalize_title("  Avatar 4K  ") == "Avatar"
        assert normalize_title("Movie   ") == "Movie"

    def test_suffix_only_at_end(self):
        """Test that suffixes are only stripped from end."""
        # "4K Movie" should not have 4K stripped (it's at the start)
        assert normalize_title("4K is Great") == "4K is Great"


class TestMapPath:
    """Tests for map_path() function."""

    def test_path_mapping_applied(self):
        """Test that path mapping is applied correctly."""
        mappings = {"/media/movies": "/mnt/plex/movies"}
        result = map_path("/media/movies/Action/Movie.mkv", mappings)

        assert result == "/mnt/plex/movies/Action/Movie.mkv"

    def test_no_matching_mapping(self):
        """Test path unchanged when no mapping matches."""
        mappings = {"/media/movies": "/mnt/plex/movies"}
        result = map_path("/other/path/file.mkv", mappings)

        assert result == "/other/path/file.mkv"

    def test_empty_mappings(self):
        """Test path unchanged with empty mappings."""
        result = map_path("/media/movies/file.mkv", {})

        assert result == "/media/movies/file.mkv"

    def test_none_mappings(self):
        """Test path unchanged with None mappings."""
        result = map_path("/media/movies/file.mkv", None)

        assert result == "/media/movies/file.mkv"

    def test_multiple_mappings_first_match(self):
        """Test that first matching mapping is used."""
        mappings = {
            "/media": "/mnt/media",
            "/media/movies": "/mnt/movies"
        }
        # The first matching prefix should be used
        result = map_path("/media/movies/file.mkv", mappings)

        # Depending on dict order, either could match first
        assert result in ["/mnt/media/movies/file.mkv", "/mnt/movies/file.mkv"]

    def test_only_replaces_once(self):
        """Test that mapping only replaces the first occurrence."""
        mappings = {"/media": "/mnt"}
        result = map_path("/media/media/file.mkv", mappings)

        # Should only replace the first /media
        assert result == "/mnt/media/file.mkv"

    def test_windows_style_paths(self):
        """Test Windows-style path mappings."""
        mappings = {"C:\\Media": "/media"}
        result = map_path("C:\\Media\\Movies\\file.mkv", mappings)

        assert result == "/media\\Movies\\file.mkv"


class TestTitleSuffixesConstant:
    """Tests for TITLE_SUFFIXES_TO_STRIP constant."""

    def test_suffixes_include_common_variants(self):
        """Test that common suffixes are included."""
        assert ' 4K' in TITLE_SUFFIXES_TO_STRIP
        assert ' 4k' in TITLE_SUFFIXES_TO_STRIP
        assert ' HD' in TITLE_SUFFIXES_TO_STRIP
        assert ' Extended' in TITLE_SUFFIXES_TO_STRIP
        assert " Director's Cut" in TITLE_SUFFIXES_TO_STRIP
        assert ' IMAX' in TITLE_SUFFIXES_TO_STRIP
        assert ' 3D' in TITLE_SUFFIXES_TO_STRIP

    def test_suffixes_have_leading_space(self):
        """Test that all suffixes have leading space (for word boundary)."""
        for suffix in TITLE_SUFFIXES_TO_STRIP:
            assert suffix.startswith(' '), f"Suffix '{suffix}' should start with space"


class TestCleanupOldLogs:
    """Tests for cleanup_old_logs() function."""

    def test_zero_retention_keeps_all(self):
        """Test that retention_days=0 keeps all files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a log file
            log_path = os.path.join(tmpdir, "test.log")
            with open(log_path, 'w') as f:
                f.write("log content")

            cleanup_old_logs(tmpdir, retention_days=0)

            # File should still exist
            assert os.path.exists(log_path)

    def test_negative_retention_keeps_all(self):
        """Test that negative retention_days keeps all files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "test.log")
            with open(log_path, 'w') as f:
                f.write("log content")

            cleanup_old_logs(tmpdir, retention_days=-1)

            assert os.path.exists(log_path)

    def test_removes_old_logs(self):
        """Test that old logs are removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "old.log")
            with open(log_path, 'w') as f:
                f.write("old log")

            # Set file modification time to 10 days ago
            old_time = (datetime.now() - timedelta(days=10)).timestamp()
            os.utime(log_path, (old_time, old_time))

            cleanup_old_logs(tmpdir, retention_days=7)

            # Old file should be removed
            assert not os.path.exists(log_path)

    def test_keeps_recent_logs(self):
        """Test that recent logs are kept."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "recent.log")
            with open(log_path, 'w') as f:
                f.write("recent log")

            # File was just created, so it's recent

            cleanup_old_logs(tmpdir, retention_days=7)

            # Recent file should still exist
            assert os.path.exists(log_path)

    def test_ignores_non_log_files(self):
        """Test that non-.log files are ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            txt_path = os.path.join(tmpdir, "file.txt")
            with open(txt_path, 'w') as f:
                f.write("text content")

            # Set to old time
            old_time = (datetime.now() - timedelta(days=100)).timestamp()
            os.utime(txt_path, (old_time, old_time))

            cleanup_old_logs(tmpdir, retention_days=7)

            # .txt file should still exist (not a .log file)
            assert os.path.exists(txt_path)

    @patch('utils.helpers.os.remove')
    def test_handles_file_remove_error(self, mock_remove):
        """Test that file removal errors are handled gracefully."""
        mock_remove.side_effect = PermissionError("Access denied")

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "old.log")
            with open(log_path, 'w') as f:
                f.write("old log")

            # Set file modification time to 10 days ago
            old_time = (datetime.now() - timedelta(days=10)).timestamp()
            os.utime(log_path, (old_time, old_time))

            # Should not raise - error is handled gracefully
            cleanup_old_logs(tmpdir, retention_days=7)

            # File still exists since removal failed
            assert os.path.exists(log_path)

    def test_handles_nonexistent_directory(self):
        """Test handling of nonexistent directory."""
        # Should not raise an exception
        cleanup_old_logs("/nonexistent/directory/path", retention_days=7)

    def test_handles_mixed_old_and_new(self):
        """Test cleanup with mix of old and new logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_log = os.path.join(tmpdir, "old.log")
            new_log = os.path.join(tmpdir, "new.log")

            with open(old_log, 'w') as f:
                f.write("old")
            with open(new_log, 'w') as f:
                f.write("new")

            # Set old_log to 10 days ago
            old_time = (datetime.now() - timedelta(days=10)).timestamp()
            os.utime(old_log, (old_time, old_time))

            cleanup_old_logs(tmpdir, retention_days=7)

            assert not os.path.exists(old_log)
            assert os.path.exists(new_log)

    def test_truncates_oversized_append_only_log(self):
        """An append-only log's mtime is refreshed on every write, so it
        can never age past retention_days - proves the size-based cap
        catches what the mtime check structurally cannot."""
        with tempfile.TemporaryDirectory() as tmpdir:
            huge_log = os.path.join(tmpdir, "daily-run.log")
            with open(huge_log, 'wb') as f:
                f.write(b"x" * (MAX_LOG_FILE_BYTES + 1))

            # Freshly written - mtime is "now", nowhere near the
            # retention_days cutoff.
            cleanup_old_logs(tmpdir, retention_days=7)

            assert os.path.exists(huge_log)
            assert os.path.getsize(huge_log) == 0

    def test_keeps_log_under_size_cap(self):
        """A log under the cap is left alone by the size check (mtime
        rules still apply as normal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            small_log = os.path.join(tmpdir, "daily-run.log")
            with open(small_log, 'wb') as f:
                f.write(b"x" * 100)

            cleanup_old_logs(tmpdir, retention_days=7)

            assert os.path.exists(small_log)
            assert os.path.getsize(small_log) == 100

    @patch('utils.helpers.os.path.getsize')
    def test_handles_size_check_error(self, mock_getsize):
        """Errors while checking/truncating for size are handled
        gracefully, matching the existing per-file error handling."""
        mock_getsize.side_effect = OSError("stat failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "test.log")
            with open(log_path, 'w') as f:
                f.write("log content")

            # Should not raise
            cleanup_old_logs(tmpdir, retention_days=7)


class TestComputeProfileHash:
    """Tests for compute_profile_hash() function."""

    def test_empty_profile_returns_empty_string(self):
        """Test that empty profile returns empty string."""
        assert compute_profile_hash({}) == ""
        assert compute_profile_hash(None) == ""

    def test_same_data_same_hash(self):
        """Test that identical data produces identical hash."""
        profile1 = {'genres': {'action': 10, 'comedy': 5}}
        profile2 = {'genres': {'action': 10, 'comedy': 5}}
        assert compute_profile_hash(profile1) == compute_profile_hash(profile2)

    def test_different_data_different_hash(self):
        """Test that different data produces different hash."""
        profile1 = {'genres': {'action': 10}}
        profile2 = {'genres': {'action': 11}}
        assert compute_profile_hash(profile1) != compute_profile_hash(profile2)

    def test_order_independent(self):
        """Test that key order doesn't affect hash."""
        profile1 = {'genres': {'action': 10, 'comedy': 5}, 'actors': {'a': 1}}
        profile2 = {'actors': {'a': 1}, 'genres': {'comedy': 5, 'action': 10}}
        assert compute_profile_hash(profile1) == compute_profile_hash(profile2)

    def test_returns_16_char_string(self):
        """Test that hash is 16 characters."""
        profile = {'genres': {'action': 10}}
        result = compute_profile_hash(profile)
        assert len(result) == 16
        assert isinstance(result, str)


class TestGetProjectRoot:
    """Tests for get_project_root().

    get_project_root() is @lru_cache(maxsize=1), so every test clears
    the cache before and after - otherwise a frozen/mocked result from
    one test would leak into whichever test (in this file or another)
    happens to call the real function next.
    """

    def setup_method(self):
        get_project_root.cache_clear()

    def teardown_method(self):
        get_project_root.cache_clear()

    def test_non_frozen_returns_repo_root(self):
        """Normal (non-frozen) run: unchanged behavior - parent of utils/."""
        root = get_project_root()
        assert os.path.isdir(os.path.join(root, 'utils'))
        assert os.path.isdir(os.path.join(root, 'web'))

    @patch('utils.helpers.sys.frozen', True, create=True)
    def test_frozen_windows_uses_appdata(self, tmp_path, monkeypatch):
        """A frozen binary on Windows uses %APPDATA%\\curatarr."""
        monkeypatch.setattr(os, 'name', 'nt')
        monkeypatch.setenv('APPDATA', str(tmp_path))
        root = get_project_root()
        assert root == os.path.join(str(tmp_path), 'curatarr')
        assert os.path.isdir(root)

    @patch('utils.helpers.sys.frozen', True, create=True)
    def test_frozen_windows_falls_back_to_home_without_appdata(self, tmp_path, monkeypatch):
        """No %APPDATA% set (unusual, but shouldn't crash): fall back to ~\\curatarr.

        Patches os.path.expanduser directly rather than the HOME env
        var: get_project_root()'s fallback is os.path.expanduser('~'),
        and on a real Windows interpreter that's ntpath.expanduser,
        which prefers %USERPROFILE% over %HOME% - so setting HOME alone
        doesn't actually redirect it when this test itself runs on
        real Windows, only on POSIX. Patching expanduser exercises the
        branch logic under test on every platform, which is the point.
        """
        monkeypatch.setattr(os, 'name', 'nt')
        monkeypatch.delenv('APPDATA', raising=False)
        monkeypatch.setattr(os.path, 'expanduser', lambda p: str(tmp_path))
        root = get_project_root()
        assert root == os.path.join(str(tmp_path), 'curatarr')
        assert os.path.isdir(root)

    @patch('utils.helpers.sys.frozen', True, create=True)
    def test_frozen_posix_uses_dot_curatarr_in_home(self, tmp_path, monkeypatch):
        """A frozen binary on macOS/Linux uses ~/.curatarr.

        Patches os.path.expanduser directly (see
        test_frozen_windows_falls_back_to_home_without_appdata above
        for why) rather than the HOME env var.
        """
        monkeypatch.setattr(os, 'name', 'posix')
        monkeypatch.setattr(os.path, 'expanduser', lambda p: str(tmp_path))
        root = get_project_root()
        assert root == os.path.join(str(tmp_path), '.curatarr')
        assert os.path.isdir(root)

    @patch('utils.helpers.sys.frozen', True, create=True)
    def test_frozen_reuses_existing_dir(self, tmp_path, monkeypatch):
        """Second run against an already-populated data dir doesn't error."""
        monkeypatch.setattr(os, 'name', 'posix')
        monkeypatch.setattr(os.path, 'expanduser', lambda p: str(tmp_path))
        existing = os.path.join(str(tmp_path), '.curatarr')
        os.makedirs(existing)
        with open(os.path.join(existing, 'marker.txt'), 'w') as f:
            f.write('keep me')
        root = get_project_root()
        assert root == existing
        assert os.path.isfile(os.path.join(existing, 'marker.txt'))

    def test_config_dir_env_override_wins_over_source_checkout(self, tmp_path, monkeypatch):
        """CURATARR_CONFIG_DIR (see Dockerfile/docs/DOCKER.md) takes
        priority over the normal non-frozen repo-root behavior."""
        override = os.path.join(str(tmp_path), 'data')
        monkeypatch.setenv('CURATARR_CONFIG_DIR', override)
        root = get_project_root()
        assert root == override
        assert os.path.isdir(root)

    @patch('utils.helpers.sys.frozen', True, create=True)
    def test_config_dir_env_override_wins_over_frozen_binary_dir(self, tmp_path, monkeypatch):
        """CURATARR_CONFIG_DIR also wins over the frozen-binary per-user
        data dir - it's the highest-priority override either way."""
        override = os.path.join(str(tmp_path), 'data')
        monkeypatch.setenv('CURATARR_CONFIG_DIR', override)
        monkeypatch.setattr(os, 'name', 'posix')
        monkeypatch.setenv('HOME', str(tmp_path))
        root = get_project_root()
        assert root == override
        assert root != os.path.join(str(tmp_path), '.curatarr')

    def test_config_dir_env_override_reuses_existing_dir(self, tmp_path, monkeypatch):
        override = os.path.join(str(tmp_path), 'data')
        os.makedirs(override)
        with open(os.path.join(override, 'marker.txt'), 'w') as f:
            f.write('keep me')
        monkeypatch.setenv('CURATARR_CONFIG_DIR', override)
        root = get_project_root()
        assert root == override
        assert os.path.isfile(os.path.join(override, 'marker.txt'))

    def test_config_dir_env_unset_is_unaffected(self, monkeypatch):
        """Unset (the default for every existing install) falls through
        to the normal repo-root behavior, unchanged."""
        monkeypatch.delenv('CURATARR_CONFIG_DIR', raising=False)
        root = get_project_root()
        assert os.path.isdir(os.path.join(root, 'utils'))
        assert os.path.isdir(os.path.join(root, 'web'))


class TestMigrateLegacyCacheDir:
    """Tests for migrate_legacy_cache_dir() - the best-effort, one-time
    move of cache files from the pre-2.10.3 __file__-relative cache
    directory to the get_project_root()-resolved one (see
    recommenders/base.py's cache_dir setup)."""

    def test_moves_files_to_new_location(self, tmp_path):
        legacy_dir = tmp_path / "legacy" / "cache"
        new_dir = tmp_path / "new" / "cache"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "all_movies_cache.json").write_text('{"movies": {}}')
        (legacy_dir / "watched_cache_plex_admin.json").write_text('{}')

        migrate_legacy_cache_dir(str(legacy_dir), str(new_dir))

        assert not (legacy_dir / "all_movies_cache.json").exists()
        assert not (legacy_dir / "watched_cache_plex_admin.json").exists()
        assert (new_dir / "all_movies_cache.json").read_text() == '{"movies": {}}'
        assert (new_dir / "watched_cache_plex_admin.json").exists()

    def test_does_not_overwrite_existing_file_at_new_location(self, tmp_path):
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "new"
        legacy_dir.mkdir()
        new_dir.mkdir()
        (legacy_dir / "all_movies_cache.json").write_text('"stale"')
        (new_dir / "all_movies_cache.json").write_text('"current"')

        migrate_legacy_cache_dir(str(legacy_dir), str(new_dir))

        # New location's file is untouched; stale legacy copy is left in place
        # rather than clobbering it.
        assert (new_dir / "all_movies_cache.json").read_text() == '"current"'
        assert (legacy_dir / "all_movies_cache.json").read_text() == '"stale"'

    def test_same_resolved_path_is_a_noop(self, tmp_path):
        """Legacy and new paths resolving to the same directory (the
        normal case for every install without CURATARR_CONFIG_DIR set) -
        must not touch anything."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "all_movies_cache.json").write_text('"data"')

        migrate_legacy_cache_dir(str(cache_dir), str(cache_dir))

        assert (cache_dir / "all_movies_cache.json").read_text() == '"data"'

    def test_missing_legacy_dir_is_a_noop(self, tmp_path):
        """Nothing to migrate (Docker/frozen-binary case where the old
        location never had anything persisted in it) - must not raise
        or create the new directory."""
        legacy_dir = tmp_path / "does_not_exist"
        new_dir = tmp_path / "new"

        migrate_legacy_cache_dir(str(legacy_dir), str(new_dir))

        assert not new_dir.exists()

    def test_ignores_subdirectories(self, tmp_path):
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "new"
        legacy_dir.mkdir()
        (legacy_dir / "subdir").mkdir()
        (legacy_dir / "subdir" / "nested.json").write_text('{}')

        migrate_legacy_cache_dir(str(legacy_dir), str(new_dir))

        # Only flat files are migrated - a subdirectory is left alone.
        assert (legacy_dir / "subdir" / "nested.json").exists()

    @patch('utils.helpers.log_warning')
    @patch('utils.helpers.shutil.move')
    def test_move_failure_logs_warning_and_does_not_raise(self, mock_move, mock_warn, tmp_path):
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "new"
        legacy_dir.mkdir()
        (legacy_dir / "all_movies_cache.json").write_text('"data"')
        mock_move.side_effect = OSError("simulated move failure")

        # Must not raise.
        migrate_legacy_cache_dir(str(legacy_dir), str(new_dir))

        mock_warn.assert_called_once()

    @patch('utils.helpers.log_info')
    def test_logs_migrated_filenames_at_info(self, mock_info, tmp_path):
        legacy_dir = tmp_path / "legacy"
        new_dir = tmp_path / "new"
        legacy_dir.mkdir()
        (legacy_dir / "all_movies_cache.json").write_text('"data"')

        migrate_legacy_cache_dir(str(legacy_dir), str(new_dir))

        mock_info.assert_called_once()
        assert "all_movies_cache.json" in mock_info.call_args[0][0]


class TestNoWindowKwargs:
    """no_window_kwargs() - shared subprocess.run()/Popen() kwargs that
    suppress a console window on Windows for the short-lived helper
    children (tasklist/taskkill/powershell precondition checks) spread
    across web/job_runner.py and web/update_apply.py - see that
    function's own docstring for why this is centralized rather than
    each call site repeating its own getattr(...) guard.

    subprocess.CREATE_NO_WINDOW only exists as an attribute on win32
    Python builds, so both the implementation and these tests read it
    via getattr(..., default=0) - monkeypatching os.name exercises both
    branches without needing an actual Windows interpreter."""

    def test_windows_returns_create_no_window_flag(self, monkeypatch):
        monkeypatch.setattr(os, 'name', 'nt')
        assert no_window_kwargs() == {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}

    def test_posix_returns_empty_dict(self, monkeypatch):
        monkeypatch.setattr(os, 'name', 'posix')
        assert no_window_kwargs() == {}
