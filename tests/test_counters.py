"""
Tests for utils/counters.py - Counter utility functions.
"""

import pytest
from collections import Counter
from utils.counters import create_empty_counters


class TestCreateEmptyCounters:
    """Tests for create_empty_counters() function."""

    def test_create_movie_counters(self):
        """Test creating empty counters for movies."""
        result = create_empty_counters(media_type='movie')

        assert 'genres' in result
        assert 'actors' in result
        assert 'languages' in result
        assert 'tmdb_keywords' in result
        assert 'tmdb_ids' in result
        assert 'directors' in result
        assert 'studio' not in result

        assert isinstance(result['genres'], Counter)
        assert isinstance(result['actors'], Counter)
        assert isinstance(result['directors'], Counter)
        assert isinstance(result['tmdb_ids'], set)

    def test_create_tv_counters(self):
        """Test creating empty counters for TV shows."""
        result = create_empty_counters(media_type='tv')

        assert 'genres' in result
        assert 'actors' in result
        assert 'languages' in result
        assert 'tmdb_keywords' in result
        assert 'tmdb_ids' in result
        assert 'studio' in result
        assert 'directors' not in result

        assert isinstance(result['studio'], Counter)

    def test_counters_are_empty(self):
        """Test that all counters are initially empty."""
        result = create_empty_counters(media_type='movie')

        assert len(result['genres']) == 0
        assert len(result['actors']) == 0
        assert len(result['directors']) == 0
        assert len(result['languages']) == 0
        assert len(result['tmdb_keywords']) == 0
        assert len(result['tmdb_ids']) == 0

    def test_default_media_type_is_movie(self):
        """Test that default media_type is 'movie'."""
        result = create_empty_counters()

        assert 'directors' in result
        assert 'studio' not in result

    def test_counters_are_independent(self):
        """Test that each call creates independent counters."""
        result1 = create_empty_counters()
        result2 = create_empty_counters()

        result1['genres']['action'] = 5

        assert result2['genres']['action'] == 0
