"""
Tests for utils/helpers.py - Miscellaneous helper functions.
"""

import pytest
from utils.helpers import normalize_title, map_path, TITLE_SUFFIXES_TO_STRIP


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
