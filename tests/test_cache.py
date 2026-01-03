"""
Tests for utils/cache.py - Cache I/O functions.
"""

import pytest
import os
import json
import tempfile
from utils.cache import (
    save_json_cache,
    load_json_cache,
    load_media_cache,
    save_media_cache,
    save_watched_cache
)
from utils.config import CACHE_VERSION


class TestSaveJsonCache:
    """Tests for save_json_cache() function."""

    def test_save_basic_dict(self):
        """Test saving a basic dictionary."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_path = f.name

        try:
            data = {"key": "value", "number": 42}
            result = save_json_cache(cache_path, data)

            assert result is True
            assert os.path.exists(cache_path)

            with open(cache_path, 'r') as f:
                loaded = json.load(f)
            assert loaded["key"] == "value"
            assert loaded["number"] == 42
        finally:
            os.unlink(cache_path)

    def test_save_with_cache_version(self):
        """Test saving with cache version."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_path = f.name

        try:
            data = {"key": "value"}
            save_json_cache(cache_path, data, cache_version=5)

            with open(cache_path, 'r') as f:
                loaded = json.load(f)
            assert loaded["cache_version"] == 5
        finally:
            os.unlink(cache_path)

    def test_save_unicode_content(self):
        """Test saving unicode content."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_path = f.name

        try:
            data = {"title": "日本語タイトル", "emoji": "🎬"}
            result = save_json_cache(cache_path, data)

            assert result is True

            with open(cache_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            assert loaded["title"] == "日本語タイトル"
            assert loaded["emoji"] == "🎬"
        finally:
            os.unlink(cache_path)

    def test_save_invalid_path(self):
        """Test saving to invalid path returns False."""
        result = save_json_cache("/nonexistent/path/cache.json", {"key": "value"})
        assert result is False


class TestLoadJsonCache:
    """Tests for load_json_cache() function."""

    def test_load_existing_file(self):
        """Test loading an existing cache file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"key": "value", "number": 42}, f)
            cache_path = f.name

        try:
            result = load_json_cache(cache_path)

            assert result is not None
            assert result["key"] == "value"
            assert result["number"] == 42
        finally:
            os.unlink(cache_path)

    def test_load_nonexistent_file(self):
        """Test loading a nonexistent file returns None."""
        result = load_json_cache("/nonexistent/path/cache.json")
        assert result is None

    def test_load_invalid_json(self):
        """Test loading invalid JSON returns None."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json {{{")
            cache_path = f.name

        try:
            result = load_json_cache(cache_path)
            assert result is None
        finally:
            os.unlink(cache_path)


class TestLoadMediaCache:
    """Tests for load_media_cache() function."""

    def test_load_valid_cache(self):
        """Test loading a valid media cache."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_data = {
                "movies": {"123": {"title": "Test Movie"}},
                "last_updated": "2024-01-01",
                "library_count": 100,
                "cache_version": CACHE_VERSION
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            result = load_media_cache(cache_path, media_key='movies')

            assert "movies" in result
            assert result["movies"]["123"]["title"] == "Test Movie"
        finally:
            os.unlink(cache_path)

    def test_load_missing_file_returns_empty(self):
        """Test that missing file returns empty cache structure."""
        result = load_media_cache("/nonexistent/cache.json", media_key='movies')

        assert result["movies"] == {}
        assert result["last_updated"] is None
        assert result["library_count"] == 0
        assert result["cache_version"] == CACHE_VERSION

    def test_load_tv_cache(self):
        """Test loading TV show cache."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_data = {
                "shows": {"456": {"title": "Test Show"}},
                "last_updated": "2024-01-01",
                "library_count": 50,
                "cache_version": CACHE_VERSION
            }
            json.dump(cache_data, f)
            cache_path = f.name

        try:
            result = load_media_cache(cache_path, media_key='shows')

            assert "shows" in result
            assert result["shows"]["456"]["title"] == "Test Show"
        finally:
            os.unlink(cache_path)


class TestSaveMediaCache:
    """Tests for save_media_cache() function."""

    def test_save_movie_cache(self):
        """Test saving movie cache."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_path = f.name

        try:
            cache_data = {
                "movies": {"123": {"title": "Test Movie"}},
                "last_updated": "2024-01-01",
                "library_count": 100,
                "cache_version": CACHE_VERSION
            }

            result = save_media_cache(cache_path, cache_data, media_key='movies')

            assert result is True
            assert os.path.exists(cache_path)

            with open(cache_path, 'r') as f:
                loaded = json.load(f)
            assert loaded["movies"]["123"]["title"] == "Test Movie"
        finally:
            os.unlink(cache_path)

    def test_save_preserves_structure(self):
        """Test that save preserves the entire structure."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_path = f.name

        try:
            cache_data = {
                "movies": {"1": {"title": "Movie 1"}, "2": {"title": "Movie 2"}},
                "last_updated": "2024-06-15T10:30:00",
                "library_count": 250,
                "cache_version": CACHE_VERSION,
                "extra_field": "preserved"
            }

            save_media_cache(cache_path, cache_data)

            with open(cache_path, 'r') as f:
                loaded = json.load(f)

            assert loaded["extra_field"] == "preserved"
            assert loaded["library_count"] == 250
        finally:
            os.unlink(cache_path)

    def test_save_invalid_path_returns_false(self):
        """Test that saving to invalid path returns False."""
        cache_data = {"movies": {}}
        result = save_media_cache("/nonexistent/path/cache.json", cache_data)
        assert result is False


class TestLoadMediaCacheErrorHandling:
    """Tests for error handling in load_media_cache."""

    def test_load_corrupted_json_returns_empty(self):
        """Test that corrupted JSON returns empty cache structure."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            # Write valid version header but corrupted content
            f.write('{"cache_version": ' + str(CACHE_VERSION) + ', "movies": {invalid}')
            cache_path = f.name

        try:
            result = load_media_cache(cache_path, media_key='movies')
            # Should return empty structure on error
            assert result["movies"] == {}
            assert result["cache_version"] == CACHE_VERSION
        finally:
            if os.path.exists(cache_path):
                os.unlink(cache_path)


class TestSaveWatchedCache:
    """Tests for save_watched_cache() function."""

    def test_save_movie_watched_cache(self):
        """Test saving movie watched cache."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_path = f.name

        try:
            watched_data = {
                'genres': {'action': 5, 'comedy': 3},
                'actors': {'Actor A': 2},
                'directors': {'Dir X': 1}
            }
            plex_tmdb = {123: 456, 789: 101}
            keywords = {'456': ['action', 'hero']}
            watched_ids = {123, 789}
            label_dates = {'123_Recommended': '2024-01-01'}

            result = save_watched_cache(
                cache_path,
                watched_data,
                plex_tmdb,
                keywords,
                watched_ids,
                label_dates,
                watched_count=2,
                media_type='movie'
            )

            assert result is True
            assert os.path.exists(cache_path)

            with open(cache_path, 'r') as f:
                loaded = json.load(f)

            assert loaded['cache_version'] == CACHE_VERSION
            assert loaded['watched_count'] == 2
            assert loaded['watched_data_counters']['genres']['action'] == 5
            assert 'watched_movie_ids' in loaded
            assert len(loaded['watched_movie_ids']) == 2
            assert loaded['label_dates'] == label_dates
        finally:
            os.unlink(cache_path)

    def test_save_tv_watched_cache(self):
        """Test saving TV watched cache uses show IDs key."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_path = f.name

        try:
            result = save_watched_cache(
                cache_path,
                {'genres': {'drama': 3}},
                {},
                {},
                {111, 222},
                {},
                watched_count=2,
                media_type='tv'
            )

            assert result is True

            with open(cache_path, 'r') as f:
                loaded = json.load(f)

            assert 'watched_show_ids' in loaded
            assert 'watched_movie_ids' not in loaded
        finally:
            os.unlink(cache_path)

    def test_save_converts_tmdb_ids_set_to_list(self):
        """Test that tmdb_ids set is converted to list."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_path = f.name

        try:
            watched_data = {
                'genres': {},
                'tmdb_ids': {100, 200, 300}  # Set that needs conversion
            }

            result = save_watched_cache(
                cache_path,
                watched_data,
                {},
                {},
                set(),
                {},
                watched_count=0,
                media_type='movie'
            )

            assert result is True

            with open(cache_path, 'r') as f:
                loaded = json.load(f)

            # Should be a list, not a set (sets aren't JSON serializable)
            assert isinstance(loaded['watched_data_counters']['tmdb_ids'], list)
        finally:
            os.unlink(cache_path)

    def test_save_invalid_path_returns_false(self):
        """Test that invalid path returns False."""
        result = save_watched_cache(
            "/nonexistent/path/cache.json",
            {},
            {},
            {},
            set(),
            {},
            watched_count=0,
            media_type='movie'
        )
        assert result is False

    def test_save_converts_plex_tmdb_keys_to_string(self):
        """Test that plex_tmdb_cache keys are converted to strings."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            cache_path = f.name

        try:
            plex_tmdb = {123: 456, 789: 101}  # Integer keys

            result = save_watched_cache(
                cache_path,
                {},
                plex_tmdb,
                {},
                set(),
                {},
                watched_count=0,
                media_type='movie'
            )

            assert result is True

            with open(cache_path, 'r') as f:
                loaded = json.load(f)

            # Keys should be strings in JSON
            assert '123' in loaded['plex_tmdb_cache']
            assert '789' in loaded['plex_tmdb_cache']
        finally:
            os.unlink(cache_path)
