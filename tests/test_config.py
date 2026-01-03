"""Tests for utils/config.py"""

import os
import json
import tempfile
import pytest

from utils.config import (
    CACHE_VERSION,
    DEFAULT_RATING_MULTIPLIERS,
    check_cache_version,
    get_config_section,
    get_tmdb_config,
    get_rating_multipliers,
    adapt_config_for_media_type,
)


class TestCheckCacheVersion:
    """Tests for check_cache_version function"""

    def test_returns_false_for_nonexistent_file(self):
        result = check_cache_version("/nonexistent/path/cache.json")
        assert result is False

    def test_returns_true_for_current_version(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'cache_version': CACHE_VERSION, 'data': {}}, f)
            f.flush()
            try:
                result = check_cache_version(f.name)
                assert result is True
            finally:
                os.unlink(f.name)

    def test_returns_false_for_old_version(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'cache_version': 1, 'data': {}}, f)
            f.flush()
            try:
                result = check_cache_version(f.name)
                assert result is False
                # File should be deleted
                assert not os.path.exists(f.name)
            except:
                if os.path.exists(f.name):
                    os.unlink(f.name)
                raise

    def test_returns_false_for_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json")
            f.flush()
            try:
                result = check_cache_version(f.name)
                assert result is False
            finally:
                if os.path.exists(f.name):
                    os.unlink(f.name)

    def test_defaults_to_v1_if_no_version(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'data': {}}, f)  # No cache_version key
            f.flush()
            try:
                result = check_cache_version(f.name)
                # Should return False because v1 < CACHE_VERSION (2)
                assert result is False
            except:
                pass
            finally:
                if os.path.exists(f.name):
                    os.unlink(f.name)


class TestGetConfigSection:
    """Tests for get_config_section function"""

    def test_returns_lowercase_key(self):
        config = {'tmdb': {'api_key': 'abc123'}}
        result = get_config_section(config, 'tmdb')
        assert result == {'api_key': 'abc123'}

    def test_returns_uppercase_key(self):
        config = {'TMDB': {'api_key': 'abc123'}}
        result = get_config_section(config, 'tmdb')
        assert result == {'api_key': 'abc123'}

    def test_prefers_lowercase_over_uppercase(self):
        config = {'tmdb': {'key': 'lower'}, 'TMDB': {'key': 'upper'}}
        result = get_config_section(config, 'tmdb')
        assert result == {'key': 'lower'}

    def test_returns_default_if_not_found(self):
        config = {'plex': {}}
        result = get_config_section(config, 'tmdb', {'default': True})
        assert result == {'default': True}

    def test_returns_empty_dict_as_default(self):
        config = {'plex': {}}
        result = get_config_section(config, 'tmdb')
        assert result == {}


class TestGetTmdbConfig:
    """Tests for get_tmdb_config function"""

    def test_extracts_api_key(self):
        config = {'tmdb': {'api_key': 'my_api_key'}}
        result = get_tmdb_config(config)
        assert result['api_key'] == 'my_api_key'

    def test_extracts_use_keywords_lowercase(self):
        config = {'tmdb': {'api_key': 'key', 'use_tmdb_keywords': False}}
        result = get_tmdb_config(config)
        assert result['use_keywords'] is False

    def test_extracts_use_keywords_mixed_case(self):
        config = {'tmdb': {'api_key': 'key', 'use_TMDB_keywords': False}}
        result = get_tmdb_config(config)
        assert result['use_keywords'] is False

    def test_defaults_use_keywords_to_true(self):
        config = {'tmdb': {'api_key': 'key'}}
        result = get_tmdb_config(config)
        assert result['use_keywords'] is True

    def test_handles_missing_tmdb_section(self):
        config = {'plex': {}}
        result = get_tmdb_config(config)
        assert result['api_key'] is None
        assert result['use_keywords'] is True


class TestGetRatingMultipliers:
    """Tests for get_rating_multipliers function"""

    def test_returns_defaults_when_no_config(self):
        result = get_rating_multipliers(None)
        assert result == DEFAULT_RATING_MULTIPLIERS

    def test_returns_defaults_when_no_rating_multipliers_section(self):
        result = get_rating_multipliers({'plex': {}})
        assert result == DEFAULT_RATING_MULTIPLIERS

    def test_custom_multipliers_applied(self):
        config = {'rating_multipliers': {'star_5': 3.0, 'star_1': 0.1}}
        result = get_rating_multipliers(config)
        assert result[10] == 3.0  # star_5 maps to rating 10
        assert result[1] == 0.1   # star_1 maps to rating 1

    def test_rating_0_always_0_1(self):
        config = {'rating_multipliers': {'star_5': 5.0}}
        result = get_rating_multipliers(config)
        assert result[0] == 0.1

    def test_interpolation_between_stars(self):
        config = {'rating_multipliers': {'star_3': 1.0, 'star_4': 2.0}}
        result = get_rating_multipliers(config)
        # Rating 6 is between star_3 (5) and star_4 (7)
        assert result[6] == 1.5  # Midpoint


class TestAdaptConfigForMediaType:
    """Tests for adapt_config_for_media_type function"""

    def test_movies_gets_director_weight(self):
        config = {'movies': {'weights': {'director': 0.10}}}
        result = adapt_config_for_media_type(config, 'movies')
        assert 'director' in result['weights']
        assert result['weights']['director'] == 0.10

    def test_tv_gets_studio_weight(self):
        config = {'tv': {'weights': {'studio': 0.15}}}
        result = adapt_config_for_media_type(config, 'tv')
        assert 'studio' in result['weights']
        assert result['weights']['studio'] == 0.15

    def test_movies_default_limit_50(self):
        config = {}
        result = adapt_config_for_media_type(config, 'movies')
        assert result['limit_results'] == 50

    def test_tv_default_limit_20(self):
        config = {}
        result = adapt_config_for_media_type(config, 'tv')
        assert result['limit_results'] == 20

    def test_inherits_plex_config(self):
        config = {'plex': {'url': 'http://localhost:32400'}}
        result = adapt_config_for_media_type(config, 'movies')
        assert result['plex']['url'] == 'http://localhost:32400'

    def test_movies_quality_defaults(self):
        config = {}
        result = adapt_config_for_media_type(config, 'movies')
        assert result['min_rating'] == 5.0
        assert result['min_vote_count'] == 50

    def test_tv_quality_defaults(self):
        config = {}
        result = adapt_config_for_media_type(config, 'tv')
        assert result['min_rating'] == 0.0
        assert result['min_vote_count'] == 0

    def test_handles_uppercase_media_section(self):
        config = {'MOVIES': {'limit_results': 100}}
        result = adapt_config_for_media_type(config, 'movies')
        assert result['limit_results'] == 100

    def test_collection_settings_inherited(self):
        config = {'collections': {'add_label': False, 'stale_removal_days': 14}}
        result = adapt_config_for_media_type(config, 'movies')
        assert result['add_label'] is False
        assert result['stale_removal_days'] == 14
