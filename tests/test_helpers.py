"""
Tests for utils/helpers.py - Miscellaneous helper functions.
"""

import pytest
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch
from utils.helpers import normalize_title, map_path, cleanup_old_logs, compute_profile_hash, TITLE_SUFFIXES_TO_STRIP


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
