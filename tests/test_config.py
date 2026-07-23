"""Tests for utils/config.py"""

import os
import json
import tempfile
import pytest

from utils.config import (
    CACHE_VERSION,
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_TV,
    DEFAULT_RATING_MULTIPLIERS,
    DEFAULT_NEGATIVE_MULTIPLIERS,
    DEFAULT_NEGATIVE_THRESHOLD,
    check_cache_version,
    get_config_section,
    get_tmdb_config,
    get_rating_multipliers,
    get_negative_signals_config,
    get_negative_multiplier,
    adapt_config_for_media_type,
    load_config,
    get_libraries,
    get_libraries_for_media_type,
    get_effective_arr_config,
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
            except Exception:
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
            except Exception:
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
        config = {'collections': {'add_label': False}}
        result = adapt_config_for_media_type(config, 'movies')
        assert result['add_label'] is False


class TestNegativeSignalsConstants:
    """Tests for negative signals constants"""

    def test_default_negative_multipliers_defined(self):
        assert DEFAULT_NEGATIVE_MULTIPLIERS is not None
        assert isinstance(DEFAULT_NEGATIVE_MULTIPLIERS, dict)

    def test_default_negative_multipliers_are_negative(self):
        for rating, mult in DEFAULT_NEGATIVE_MULTIPLIERS.items():
            assert mult < 0, f"Rating {rating} should have negative multiplier"

    def test_default_negative_threshold(self):
        assert DEFAULT_NEGATIVE_THRESHOLD == 3

    def test_multipliers_increase_severity_with_lower_ratings(self):
        # Lower rating = more negative multiplier
        assert DEFAULT_NEGATIVE_MULTIPLIERS[0] < DEFAULT_NEGATIVE_MULTIPLIERS[1]
        assert DEFAULT_NEGATIVE_MULTIPLIERS[1] < DEFAULT_NEGATIVE_MULTIPLIERS[2]
        assert DEFAULT_NEGATIVE_MULTIPLIERS[2] < DEFAULT_NEGATIVE_MULTIPLIERS[3]


class TestGetNegativeSignalsConfig:
    """Tests for get_negative_signals_config function"""

    def test_returns_defaults_when_no_config(self):
        result = get_negative_signals_config(None)
        assert result['enabled'] is True
        assert result['bad_ratings']['enabled'] is True
        assert result['bad_ratings']['threshold'] == 3
        assert result['bad_ratings']['cap_penalty'] == 0.5

    def test_returns_defaults_when_empty_config(self):
        result = get_negative_signals_config({})
        assert result['enabled'] is True

    def test_respects_disabled_flag(self):
        config = {'negative_signals': {'enabled': False}}
        result = get_negative_signals_config(config)
        assert result['enabled'] is False

    def test_custom_threshold(self):
        config = {'negative_signals': {'bad_ratings': {'threshold': 5}}}
        result = get_negative_signals_config(config)
        assert result['bad_ratings']['threshold'] == 5

    def test_dropped_shows_defaults(self):
        result = get_negative_signals_config(None)
        assert result['dropped_shows']['enabled'] is True
        assert result['dropped_shows']['min_episodes_watched'] == 2
        assert result['dropped_shows']['max_completion_percent'] == 25
        assert result['dropped_shows']['penalty_multiplier'] == -0.4


class TestGetNegativeMultiplier:
    """Tests for get_negative_multiplier function"""

    def test_returns_negative_for_low_ratings(self):
        assert get_negative_multiplier(0) < 0
        assert get_negative_multiplier(1) < 0
        assert get_negative_multiplier(2) < 0
        assert get_negative_multiplier(3) < 0

    def test_rating_0_most_negative(self):
        assert get_negative_multiplier(0) == -1.0

    def test_rating_3_least_negative(self):
        assert get_negative_multiplier(3) == -0.3

    def test_unknown_rating_returns_mild_negative(self):
        assert get_negative_multiplier(99) == -0.3


class TestLoadConfig:
    """Tests for load_config function with environment variable support"""

    def test_loads_yaml_config(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n  token: abc123\n")
            f.flush()
            try:
                result = load_config(f.name)
                assert result['plex']['url'] == 'http://localhost:32400'
                assert result['plex']['token'] == 'abc123'
            finally:
                os.unlink(f.name)

    def test_env_var_overrides_plex_token(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n  token: file_token\n")
            f.flush()
            try:
                os.environ['PLEX_TOKEN'] = 'env_token'
                result = load_config(f.name)
                assert result['plex']['token'] == 'env_token'
            finally:
                del os.environ['PLEX_TOKEN']
                os.unlink(f.name)

    def test_env_var_overrides_plex_url(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n")
            f.flush()
            try:
                os.environ['PLEX_URL'] = 'http://remote:32400'
                result = load_config(f.name)
                assert result['plex']['url'] == 'http://remote:32400'
            finally:
                del os.environ['PLEX_URL']
                os.unlink(f.name)

    def test_env_var_overrides_tmdb_api_key(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write("tmdb:\n  api_key: file_key\n")
            f.flush()
            try:
                os.environ['TMDB_API_KEY'] = 'env_key'
                result = load_config(f.name)
                assert result['tmdb']['api_key'] == 'env_key'
            finally:
                del os.environ['TMDB_API_KEY']
                os.unlink(f.name)

    def test_env_var_creates_section_if_missing(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n")
            f.flush()
            try:
                os.environ['TMDB_API_KEY'] = 'env_key'
                result = load_config(f.name)
                assert result['tmdb']['api_key'] == 'env_key'
            finally:
                del os.environ['TMDB_API_KEY']
                os.unlink(f.name)

    def test_no_env_var_uses_file_value(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write("plex:\n  token: file_token\n")
            f.flush()
            try:
                # Ensure env var is not set
                if 'PLEX_TOKEN' in os.environ:
                    del os.environ['PLEX_TOKEN']
                result = load_config(f.name)
                assert result['plex']['token'] == 'file_token'
            finally:
                os.unlink(f.name)


class TestModularConfigLoading:
    """Tests for modular config file loading"""

    def test_loads_tuning_yml_when_present(self):
        import shutil
        config_dir = tempfile.mkdtemp()
        try:
            # Write main config
            config_path = os.path.join(config_dir, 'config.yml')
            with open(config_path, 'w') as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            # Write tuning.yml
            tuning_path = os.path.join(config_dir, 'tuning.yml')
            with open(tuning_path, 'w') as f:
                f.write("movies:\n  limit_results: 100\n")

            result = load_config(config_path)
            assert result['movies']['limit_results'] == 100
        finally:
            shutil.rmtree(config_dir)

    def test_loads_trakt_yml_when_present(self):
        import shutil
        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, 'config.yml')
            with open(config_path, 'w') as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            trakt_path = os.path.join(config_dir, 'trakt.yml')
            with open(trakt_path, 'w') as f:
                f.write("enabled: true\nclient_id: abc123\n")

            result = load_config(config_path)
            assert result['trakt']['enabled'] is True
            assert result['trakt']['client_id'] == 'abc123'
        finally:
            shutil.rmtree(config_dir)

    def test_works_without_module_files(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write("plex:\n  url: http://localhost:32400\n")
            f.flush()
            try:
                result = load_config(f.name)
                assert result['plex']['url'] == 'http://localhost:32400'
            finally:
                os.unlink(f.name)

    def test_tuning_merges_into_config(self):
        import shutil
        config_dir = tempfile.mkdtemp()
        try:
            # Main config with only core sections (no migration triggered)
            config_path = os.path.join(config_dir, 'config.yml')
            with open(config_path, 'w') as f:
                f.write("plex:\n  url: http://localhost:32400\n")

            # tuning.yml adds movies settings
            tuning_path = os.path.join(config_dir, 'tuning.yml')
            with open(tuning_path, 'w') as f:
                f.write("movies:\n  limit_results: 200\n")

            result = load_config(config_path)
            # tuning.yml should be merged in
            assert result['movies']['limit_results'] == 200
        finally:
            shutil.rmtree(config_dir)


class TestConfigMigration:
    """Tests for config migration functionality"""

    def test_needs_migration_detects_tuning_sections(self):
        from utils.migrate_config import needs_migration

        # Config with tuning sections needs migration
        config = {'plex': {}, 'movies': {'limit_results': 50}}
        assert needs_migration(config) is True

        # Config with only core sections doesn't need migration
        config = {'plex': {}, 'tmdb': {}, 'users': {}}
        assert needs_migration(config) is False

    def test_needs_migration_detects_feature_modules(self):
        from utils.migrate_config import needs_migration

        config = {'plex': {}, 'trakt': {'enabled': True}}
        assert needs_migration(config) is True

    def test_extract_tuning_config(self):
        from utils.migrate_config import extract_tuning_config

        config = {
            'plex': {'url': 'http://localhost'},
            'movies': {'limit_results': 50},
            'recency_decay': {'enabled': True},
        }
        tuning = extract_tuning_config(config)
        assert 'movies' in tuning
        assert 'recency_decay' in tuning
        assert 'plex' not in tuning

    def test_build_core_config(self):
        from utils.migrate_config import build_core_config

        config = {
            'plex': {'url': 'http://localhost'},
            'tmdb': {'api_key': 'abc'},
            'movies': {'limit_results': 50},
            'trakt': {'enabled': True},
        }
        core = build_core_config(config)
        assert 'plex' in core
        assert 'tmdb' in core
        assert 'movies' not in core
        assert 'trakt' not in core

    def test_migrate_config_creates_files(self):
        import shutil
        from utils.migrate_config import migrate_config

        config_dir = tempfile.mkdtemp()
        try:
            config_path = os.path.join(config_dir, 'config.yml')
            with open(config_path, 'w') as f:
                f.write("""
plex:
  url: http://localhost:32400
tmdb:
  api_key: abc123
movies:
  limit_results: 50
trakt:
  enabled: true
  client_id: xyz
""")

            result = migrate_config(config_path)

            assert result['migrated'] is True
            assert 'tuning.yml' in result['files_created']
            assert 'trakt.yml' in result['files_created']
            assert os.path.exists(os.path.join(config_dir, 'tuning.yml'))
            assert os.path.exists(os.path.join(config_dir, 'trakt.yml'))
        finally:
            shutil.rmtree(config_dir)


class TestAdaptConfigRadarrSonarr:
    """Tests for radarr/sonarr config handling in adapt_config_for_media_type"""

    def test_radarr_from_root_level(self):
        # New modular format - radarr at root level
        config = {
            'radarr': {'enabled': True, 'url': 'http://radarr:7878'},
            'movies': {},
        }
        result = adapt_config_for_media_type(config, 'movies')
        assert result['radarr']['enabled'] is True
        assert result['radarr']['url'] == 'http://radarr:7878'

    def test_sonarr_from_root_level(self):
        # New modular format - sonarr at root level
        config = {
            'sonarr': {'enabled': True, 'url': 'http://sonarr:8989'},
            'tv': {},
        }
        result = adapt_config_for_media_type(config, 'tv')
        assert result['sonarr']['enabled'] is True
        assert result['sonarr']['url'] == 'http://sonarr:8989'


class TestGetLibraries:
    """Tests for get_libraries fallback synthesis and normalization (#157 Phase 1)"""

    def test_no_libraries_key_synthesizes_two_entries(self):
        config = {'plex': {}}
        result = get_libraries(config)
        assert len(result) == 2
        assert result[0]['media_type'] == MEDIA_TYPE_MOVIE
        assert result[1]['media_type'] == MEDIA_TYPE_TV

    def test_synthesis_defaults_names_when_plex_library_keys_absent(self):
        config = {'plex': {}}
        result = get_libraries(config)
        assert result[0]['name'] == 'Movies'
        assert result[1]['name'] == 'TV Shows'

    def test_synthesis_uses_configured_library_names(self):
        config = {'plex': {'movie_library': 'Films', 'tv_library': 'Shows'}}
        result = get_libraries(config)
        assert result[0]['name'] == 'Films'
        assert result[0]['section'] == 'Films'
        assert result[1]['name'] == 'Shows'
        assert result[1]['section'] == 'Shows'

    def test_synthesis_derives_slug_ids(self):
        config = {'plex': {'movie_library': 'My Movies', 'tv_library': 'TV Shows'}}
        result = get_libraries(config)
        assert result[0]['id'] == 'my-movies'
        assert result[1]['id'] == 'tv-shows'

    def test_empty_libraries_list_also_synthesizes(self):
        config = {'plex': {}, 'libraries': []}
        result = get_libraries(config)
        assert len(result) == 2

    def test_synthesized_arr_merges_from_global_radarr_sonarr(self):
        config = {
            'plex': {'movie_library': 'Movies', 'tv_library': 'TV Shows'},
            'radarr': {'enabled': True, 'root_folder': '/data/movies', 'quality_profile': '4K'},
            'sonarr': {'enabled': True, 'root_folder': '/data/tv', 'series_type': 'anime'},
        }
        libraries = get_libraries(config)
        movie_lib = next(l for l in libraries if l['media_type'] == MEDIA_TYPE_MOVIE)
        tv_lib = next(l for l in libraries if l['media_type'] == MEDIA_TYPE_TV)

        movie_arr = get_effective_arr_config(config, movie_lib)
        assert movie_arr['enabled'] is True
        assert movie_arr['root_folder'] == '/data/movies'
        assert movie_arr['quality_profile'] == '4K'

        tv_arr = get_effective_arr_config(config, tv_lib)
        assert tv_arr['enabled'] is True
        assert tv_arr['root_folder'] == '/data/tv'
        assert tv_arr['series_type'] == 'anime'

    def test_normalizes_missing_id_from_name_slug(self):
        config = {'libraries': [{'name': 'Kids Movies', 'media_type': 'movie'}]}
        result = get_libraries(config)
        assert result[0]['id'] == 'kids-movies'

    def test_normalizes_missing_media_type_defaults_movie(self):
        config = {'libraries': [{'name': 'Movies'}]}
        result = get_libraries(config)
        assert result[0]['media_type'] == MEDIA_TYPE_MOVIE

    def test_normalizes_missing_section_defaults_to_name(self):
        config = {'libraries': [{'name': 'Anime', 'media_type': 'tv'}]}
        result = get_libraries(config)
        assert result[0]['section'] == 'Anime'

    def test_preserves_explicit_fields(self):
        config = {
            'libraries': [
                {'id': 'custom-id', 'name': 'Movies', 'section': 'Custom Section', 'media_type': 'movie'},
            ]
        }
        result = get_libraries(config)
        assert result[0]['id'] == 'custom-id'
        assert result[0]['section'] == 'Custom Section'

    def test_multiple_libraries_of_same_media_type(self):
        config = {
            'libraries': [
                {'name': 'Movies', 'media_type': 'movie'},
                {'name': 'Kids Movies', 'media_type': 'movie'},
            ]
        }
        result = get_libraries(config)
        assert len(result) == 2
        assert result[0]['id'] == 'movies'
        assert result[1]['id'] == 'kids-movies'


class TestGetLibrariesForMediaType:
    """Tests for get_libraries_for_media_type"""

    def test_filters_to_movie_libraries(self):
        config = {
            'libraries': [
                {'name': 'Movies', 'media_type': 'movie'},
                {'name': 'TV Shows', 'media_type': 'tv'},
            ]
        }
        result = get_libraries_for_media_type(config, MEDIA_TYPE_MOVIE)
        assert len(result) == 1
        assert result[0]['name'] == 'Movies'

    def test_filters_to_tv_libraries(self):
        config = {
            'libraries': [
                {'name': 'Movies', 'media_type': 'movie'},
                {'name': 'TV Shows', 'media_type': 'tv'},
                {'name': 'Anime', 'media_type': 'tv'},
            ]
        }
        result = get_libraries_for_media_type(config, MEDIA_TYPE_TV)
        assert len(result) == 2

    def test_returns_empty_list_when_no_match(self):
        config = {'libraries': [{'name': 'Movies', 'media_type': 'movie'}]}
        result = get_libraries_for_media_type(config, MEDIA_TYPE_TV)
        assert result == []

    def test_falls_back_to_synthesized_libraries(self):
        config = {'plex': {}}
        result = get_libraries_for_media_type(config, MEDIA_TYPE_MOVIE)
        assert len(result) == 1
        assert result[0]['name'] == 'Movies'


class TestGetEffectiveArrConfig:
    """Tests for get_effective_arr_config merge precedence (#157 Phase 1)"""

    def test_uses_global_when_library_arr_empty(self):
        config = {'radarr': {'enabled': True, 'url': 'http://radarr:7878', 'api_key': 'globalkey',
                              'root_folder': '/movies', 'quality_profile': 'HD-1080p'}}
        library = {'media_type': 'movie', 'arr': {}}
        result = get_effective_arr_config(config, library)
        assert result['enabled'] is True
        assert result['url'] == 'http://radarr:7878'
        assert result['api_key'] == 'globalkey'
        assert result['root_folder'] == '/movies'
        assert result['quality_profile'] == 'HD-1080p'

    def test_library_arr_overrides_global_routing_field(self):
        config = {'radarr': {'enabled': True, 'root_folder': '/movies', 'quality_profile': 'HD-1080p'}}
        library = {'media_type': 'movie', 'arr': {'root_folder': '/kids-movies'}}
        result = get_effective_arr_config(config, library)
        # Overridden field
        assert result['root_folder'] == '/kids-movies'
        # Fallback field untouched
        assert result['quality_profile'] == 'HD-1080p'

    def test_instance_overrides_url_and_api_key(self):
        config = {'radarr': {'enabled': True, 'url': 'http://default:7878', 'api_key': 'default_key'}}
        library = {
            'media_type': 'movie',
            'arr': {'instance': {'url': 'http://custom:7878', 'api_key': 'custom_key'}},
        }
        result = get_effective_arr_config(config, library)
        assert result['url'] == 'http://custom:7878'
        assert result['api_key'] == 'custom_key'

    def test_instance_partial_override_falls_back_for_omitted_field(self):
        config = {'radarr': {'enabled': True, 'url': 'http://default:7878', 'api_key': 'default_key'}}
        library = {
            'media_type': 'movie',
            'arr': {'instance': {'url': 'http://custom:7878'}},
        }
        result = get_effective_arr_config(config, library)
        assert result['url'] == 'http://custom:7878'
        assert result['api_key'] == 'default_key'

    def test_movie_gets_minimum_availability_not_series_type(self):
        config = {'radarr': {'minimum_availability': 'announced'}}
        library = {'media_type': 'movie', 'arr': {}}
        result = get_effective_arr_config(config, library)
        assert result['minimum_availability'] == 'announced'
        assert 'series_type' not in result

    def test_tv_gets_series_type_not_minimum_availability(self):
        config = {'sonarr': {'series_type': 'anime'}}
        library = {'media_type': 'tv', 'arr': {}}
        result = get_effective_arr_config(config, library)
        assert result['series_type'] == 'anime'
        assert 'minimum_availability' not in result

    def test_search_field_falls_back_to_legacy_radarr_search_for_movie(self):
        config = {'radarr': {'search_for_movie': True}}
        library = {'media_type': 'movie', 'arr': {}}
        result = get_effective_arr_config(config, library)
        assert result['search'] is True

    def test_search_field_falls_back_to_legacy_sonarr_search_for_series(self):
        config = {'sonarr': {'search_for_series': True}}
        library = {'media_type': 'tv', 'arr': {}}
        result = get_effective_arr_config(config, library)
        assert result['search'] is True

    def test_library_arr_search_overrides_legacy_global_search(self):
        config = {'radarr': {'search_for_movie': False}}
        library = {'media_type': 'movie', 'arr': {'search': True}}
        result = get_effective_arr_config(config, library)
        assert result['search'] is True

    def test_defaults_when_no_global_arr_config_at_all(self):
        config = {}
        library = {'media_type': 'movie', 'arr': {}}
        result = get_effective_arr_config(config, library)
        assert result['enabled'] is False
        assert result['monitor'] is False
        assert result['search'] is False
        assert result['url'] is None
        assert result['api_key'] is None

    def test_missing_media_type_defaults_to_movie(self):
        config = {'radarr': {'root_folder': '/movies'}}
        library = {'arr': {}}
        result = get_effective_arr_config(config, library)
        assert result['root_folder'] == '/movies'
        assert 'minimum_availability' in result

    def test_missing_arr_key_falls_back_entirely_to_global(self):
        config = {'sonarr': {'enabled': True, 'root_folder': '/tv'}}
        library = {'media_type': 'tv'}
        result = get_effective_arr_config(config, library)
        assert result['enabled'] is True
        assert result['root_folder'] == '/tv'
