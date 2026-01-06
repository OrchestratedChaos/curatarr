"""
Tests for utils/counters.py - Counter utility functions.
"""

import pytest
from collections import Counter
from unittest.mock import patch
from utils.counters import (
    create_empty_counters,
    process_counters_from_cache,
    _apply_capped_weight
)


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
        assert 'studios' not in result

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
        assert 'studios' in result
        assert 'directors' not in result

        assert isinstance(result['studios'], Counter)

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
        assert 'studios' not in result

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

        assert counters['studios']['hbo'] == 1.0

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
        """Test that studios counter is created if missing for TV."""
        counters = create_empty_counters('movie')  # Movie counters don't have studios
        media_info = {'studio': 'Netflix', 'title': 'Test'}

        process_counters_from_cache(media_info, counters, media_type='tv')

        assert 'studios' in counters
        assert counters['studios']['netflix'] == 1.0


class TestApplyCappedWeight:
    """Tests for _apply_capped_weight() helper function."""

    def test_positive_weight_adds_directly(self):
        """Test that positive weights are added directly."""
        counter = Counter()
        _apply_capped_weight(counter, 'action', 2.0)
        assert counter['action'] == 2.0

    def test_positive_weight_accumulates(self):
        """Test that positive weights accumulate."""
        counter = Counter({'action': 3.0})
        _apply_capped_weight(counter, 'action', 2.0)
        assert counter['action'] == 5.0

    def test_negative_weight_on_empty_counter(self):
        """Test negative weight on empty counter."""
        counter = Counter()
        _apply_capped_weight(counter, 'action', -1.0)
        assert counter['action'] == -1.0

    def test_negative_weight_caps_at_floor(self):
        """Test that negative weight is capped at floor value."""
        counter = Counter({'action': 10.0})
        # With cap_penalty=0.5, floor is 5.0
        # Applying -8.0 would give 2.0, but floor is 5.0
        _apply_capped_weight(counter, 'action', -8.0, cap_penalty=0.5)
        assert counter['action'] == 5.0

    def test_negative_weight_within_cap_applies_fully(self):
        """Test that negative weight within cap applies fully."""
        counter = Counter({'action': 10.0})
        # With cap_penalty=0.5, floor is 5.0
        # Applying -3.0 gives 7.0, which is above floor
        _apply_capped_weight(counter, 'action', -3.0, cap_penalty=0.5)
        assert counter['action'] == 7.0

    def test_custom_cap_penalty(self):
        """Test custom cap_penalty value."""
        counter = Counter({'action': 10.0})
        # With cap_penalty=0.3, floor is 3.0
        _apply_capped_weight(counter, 'action', -20.0, cap_penalty=0.3)
        assert counter['action'] == 3.0

    def test_negative_on_already_negative(self):
        """Test negative weight on already negative counter."""
        counter = Counter({'action': -2.0})
        _apply_capped_weight(counter, 'action', -1.0)
        assert counter['action'] == -3.0


class TestProcessCountersNegativeSignals:
    """Tests for negative signal processing in process_counters_from_cache()."""

    def test_negative_signal_config_enables_negative_weights(self):
        """Test that negative signals config enables negative weight processing."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}
        ns_config = {
            'enabled': True,
            'bad_ratings': {
                'enabled': True,
                'threshold': 3,
                'cap_penalty': 0.5
            }
        }

        # Rating of 2 should be below threshold and return negative
        result = process_counters_from_cache(
            media_info, counters,
            rating=2,
            negative_signals_config=ns_config
        )

        assert result is True  # Indicates processed as negative signal

    def test_returns_false_for_positive_rating(self):
        """Test that high ratings return False (not negative signal)."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}
        ns_config = {
            'enabled': True,
            'bad_ratings': {
                'enabled': True,
                'threshold': 3,
                'cap_penalty': 0.5
            }
        }

        result = process_counters_from_cache(
            media_info, counters,
            rating=8,
            negative_signals_config=ns_config
        )

        assert result is False

    def test_disabled_negative_signals_ignores_bad_ratings(self):
        """Test that disabled negative signals ignores bad ratings."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}
        ns_config = {
            'enabled': False,
            'bad_ratings': {'enabled': True, 'threshold': 3}
        }

        result = process_counters_from_cache(
            media_info, counters,
            rating=1,
            negative_signals_config=ns_config
        )

        assert result is False
        assert counters['genres']['action'] > 0  # Still positive

    def test_capped_negative_signal_preserves_positive_preference(self):
        """Test that capped negative signals don't destroy positive preferences."""
        counters = create_empty_counters('movie')
        counters['genres']['action'] = 10.0  # Pre-existing preference
        media_info = {'genres': ['Action'], 'title': 'Test'}
        ns_config = {
            'enabled': True,
            'bad_ratings': {
                'enabled': True,
                'threshold': 3,
                'cap_penalty': 0.5
            }
        }

        process_counters_from_cache(
            media_info, counters,
            rating=0,  # Strong dislike
            negative_signals_config=ns_config
        )

        # Should be capped at 5.0 (50% of 10.0)
        assert counters['genres']['action'] >= 5.0


class TestProcessCountersPreCalculatedWeight:
    """Tests for pre-calculated weight parameter in process_counters_from_cache()."""

    def test_weight_parameter_skips_internal_calculation(self):
        """Test that weight parameter bypasses internal weight calculation."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}

        # Pass pre-calculated weight directly
        process_counters_from_cache(media_info, counters, weight=2.5)

        assert counters['genres']['action'] == 2.5

    def test_negative_weight_parameter(self):
        """Test that negative weight parameter works correctly."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}

        result = process_counters_from_cache(media_info, counters, weight=-0.5)

        assert result is True  # Returns True for negative signals
        assert counters['genres']['action'] == -0.5

    def test_weight_parameter_with_cap_penalty(self):
        """Test that cap_penalty works with weight parameter."""
        counters = create_empty_counters('movie')
        counters['genres']['action'] = 10.0  # Pre-existing preference

        media_info = {'genres': ['Action'], 'title': 'Test'}

        # With cap_penalty=0.5, floor is 5.0
        process_counters_from_cache(media_info, counters, weight=-8.0, cap_penalty=0.5)

        assert counters['genres']['action'] == 5.0  # Capped at floor

    def test_weight_parameter_ignores_other_multiplier_params(self):
        """Test that weight parameter ignores view_count, rating, etc."""
        counters = create_empty_counters('movie')
        media_info = {'genres': ['Action'], 'title': 'Test'}

        # Even with view_count and rating, weight should be used directly
        process_counters_from_cache(
            media_info, counters,
            view_count=5,
            rating=10,
            weight=1.5
        )

        # Should be exactly 1.5, not affected by view_count or rating
        assert counters['genres']['action'] == 1.5

    def test_weight_zero_adds_nothing(self):
        """Test that weight=0 adds nothing to counters."""
        counters = create_empty_counters('movie')
        counters['genres']['action'] = 5.0
        media_info = {'genres': ['Action'], 'title': 'Test'}

        process_counters_from_cache(media_info, counters, weight=0.0)

        assert counters['genres']['action'] == 5.0  # Unchanged
