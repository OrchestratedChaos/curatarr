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
    save_media_cache
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
