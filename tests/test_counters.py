"""
Tests for utils/counters.py - Counter utility functions.
"""

import pytest
from collections import Counter
from unittest.mock import patch
from utils.counters import create_empty_counters, process_counters_from_cache


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


class TestProcessCountersFromCache:
    """Tests for process_counters_from_cache() function."""

    def test_updates_genre_counters(self):
        """Test that genres are added to counters."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action', 'Comedy'], 'title': 'Test'}

        process_counters_from_cache(media_info, counters)

        assert counters['genres']['action'] == 1.0
        assert counters['genres']['comedy'] == 1.0

    def test_updates_actor_counters(self):
        """Test that actors are added to counters."""
        counters = create_empty_counters('movie')
        media_info = {'actors': ['Actor A', 'Actor B'], 'title': 'Test'}

        process_counters_from_cache(media_info, counters)

        assert counters['actors']['Actor A'] == 1.0
        assert counters['actors']['Actor B'] == 1.0

    def test_updates_director_counters_for_movies(self):
        """Test that directors are added for movies."""
        counters = create_empty_counters('movie')
        media_info = {'directors': ['Director X'], 'title': 'Test'}

        process_counters_from_cache(media_info, counters, media_type='movie')

        assert counters['directors']['Director X'] == 1.0

    def test_updates_studio_counters_for_tv(self):
        """Test that studio is added for TV shows."""
        counters = create_empty_counters('tv')
        media_info = {'studio': 'HBO', 'title': 'Test'}

        process_counters_from_cache(media_info, counters, media_type='tv')

        assert counters['studio']['hbo'] == 1.0

    def test_updates_language_counters(self):
        """Test that language is added to counters."""
        counters = create_empty_counters('movie')
        media_info = {'language': 'English', 'title': 'Test'}

        process_counters_from_cache(media_info, counters)

        assert counters['languages']['english'] == 1.0

    def test_skips_na_language(self):
        """Test that N/A language is skipped."""
        counters = create_empty_counters('movie')
        media_info = {'language': 'N/A', 'title': 'Test'}

        process_counters_from_cache(media_info, counters)

        assert len(counters['languages']) == 0

    def test_updates_keyword_counters(self):
        """Test that keywords are added to counters."""
        counters = create_empty_counters('movie')
        media_info = {'tmdb_keywords': ['superhero', 'action'], 'title': 'Test'}

        process_counters_from_cache(media_info, counters)

        assert counters['tmdb_keywords']['superhero'] == 1.0
        assert counters['tmdb_keywords']['action'] == 1.0

    def test_tracks_tmdb_id(self):
        """Test that TMDB ID is tracked."""
        counters = create_empty_counters('movie')
        media_info = {'tmdb_id': 12345, 'title': 'Test'}

        process_counters_from_cache(media_info, counters)

        assert 12345 in counters['tmdb_ids']

    def test_applies_rewatch_multiplier(self):
        """Test that rewatch multiplier is applied."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}

        # With view_count=2, log2(2)+1 = 2.0 multiplier
        process_counters_from_cache(media_info, counters, view_count=2)

        assert counters['genres']['action'] == 2.0

    def test_applies_rating_multiplier(self):
        """Test that rating multiplier is applied."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}
        rating_mults = {10: 2.0, 8: 1.5, 5: 1.0}

        process_counters_from_cache(
            media_info, counters,
            rating=10,
            rating_multipliers=rating_mults
        )

        assert counters['genres']['action'] == 2.0

    def test_applies_recency_multiplier(self):
        """Test that recency multiplier is applied."""
        from datetime import datetime, timezone, timedelta

        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}

        # Viewed 60 days ago - should get 0.75 multiplier
        viewed_at = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
        recency_config = {'enabled': True, 'days_31_90': 0.5}

        process_counters_from_cache(
            media_info, counters,
            viewed_at=viewed_at,
            recency_config=recency_config
        )

        assert counters['genres']['action'] == 0.5

    def test_combined_multipliers(self):
        """Test that all multipliers are combined."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}
        rating_mults = {10: 2.0}

        # view_count=2 gives 2.0 multiplier, rating=10 gives 2.0
        # total = 2.0 * 2.0 = 4.0
        process_counters_from_cache(
            media_info, counters,
            view_count=2,
            rating=10,
            rating_multipliers=rating_mults
        )

        assert counters['genres']['action'] == 4.0

    def test_handles_string_director(self):
        """Test handling of director as string instead of list."""
        counters = create_empty_counters('movie')
        media_info = {'directors': 'Single Director', 'title': 'Test'}

        process_counters_from_cache(media_info, counters, media_type='movie')

        assert counters['directors']['Single Director'] == 1.0

    def test_handles_cast_key_for_actors(self):
        """Test that 'cast' key is used as fallback for actors."""
        counters = create_empty_counters('movie')
        media_info = {'cast': ['Actor from Cast'], 'title': 'Test'}

        process_counters_from_cache(media_info, counters)

        assert counters['actors']['Actor from Cast'] == 1.0

    def test_skips_empty_values(self):
        """Test that empty/null values are skipped."""
        counters = create_empty_counters('movie')
        media_info = {
            'genres': ['', None, 'Action'],
            'actors': ['', None],
            'tmdb_keywords': ['', None],
            'title': 'Test'
        }

        process_counters_from_cache(media_info, counters)

        assert counters['genres']['action'] == 1.0
        assert '' not in counters['genres']
        assert len(counters['actors']) == 0

    def test_empty_media_info(self):
        """Test handling empty media info."""
        counters = create_empty_counters('movie')
        media_info = {'title': 'Test'}  # Minimal info

        # Should not raise
        process_counters_from_cache(media_info, counters)

        # No updates should have been made
        assert len(counters['genres']) == 0

    def test_creates_studio_counter_if_missing(self):
        """Test that studio counter is created if missing for TV."""
        counters = create_empty_counters('movie')  # Movie counters don't have studio
        media_info = {'studio': 'Netflix', 'title': 'Test'}

        process_counters_from_cache(media_info, counters, media_type='tv')

        assert 'studio' in counters
        assert counters['studio']['netflix'] == 1.0
