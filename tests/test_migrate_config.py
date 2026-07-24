"""Tests for utils/migrate_config.py - Config migration utilities"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import os
import sys
import tempfile
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.migrate_config import (
    needs_migration,
    extract_tuning_config,
    extract_feature_config,
    build_core_config,
    migrate_config,
    migrate_to_libraries,
    migrate_update_mode,
    main,
    TUNING_SECTIONS,
    CORE_SECTIONS,
    FEATURE_MODULES,
)


class TestNeedsMigration:
    """Tests for needs_migration function"""

    def test_returns_false_for_empty_config(self):
        """Test returns False for empty config."""
        assert needs_migration({}) is False

    def test_returns_true_for_tuning_sections(self):
        """Test returns True if tuning sections present."""
        config = {'movies': {'limit_results': 50}}
        assert needs_migration(config) is True

    def test_returns_true_for_feature_modules(self):
        """Test returns True if feature modules present."""
        config = {'trakt': {'enabled': True}}
        assert needs_migration(config) is True

    def test_returns_true_for_nested_radarr(self):
        """Test returns True if radarr nested in movies."""
        config = {'movies': {'radarr': {'enabled': True}}}
        assert needs_migration(config) is True

    def test_returns_true_for_nested_sonarr(self):
        """Test returns True if sonarr nested in tv."""
        config = {'tv': {'sonarr': {'enabled': True}}}
        assert needs_migration(config) is True

    def test_returns_false_for_core_only(self):
        """Test returns False if only core sections present."""
        config = {
            'plex': {'url': 'http://localhost:32400'},
            'tmdb': {'api_key': 'abc'},
            'users': {'list': 'alice'},
        }
        assert needs_migration(config) is False

    def test_returns_true_for_legacy_movie_library_without_libraries(self):
        """Test returns True if plex.movie_library present but no libraries list (#157)."""
        config = {'plex': {'movie_library': 'Movies'}}
        assert needs_migration(config) is True

    def test_returns_true_for_legacy_tv_library_without_libraries(self):
        """Test returns True if plex.tv_library present but no libraries list (#157)."""
        config = {'plex': {'tv_library': 'TV Shows'}}
        assert needs_migration(config) is True

    def test_returns_false_when_libraries_already_present(self):
        """Test returns False if libraries already migrated, even with legacy keys (idempotent)."""
        config = {
            'plex': {'movie_library': 'Movies', 'tv_library': 'TV Shows'},
            'libraries': [{'id': 'movies'}, {'id': 'tv-shows'}],
        }
        assert needs_migration(config) is False

    def test_returns_false_when_no_plex_library_keys(self):
        """Test returns False if plex config has no movie_library/tv_library at all."""
        config = {'plex': {'url': 'http://localhost:32400'}}
        assert needs_migration(config) is False


class TestCoreSectionsIncludesLibraries:
    """Test CORE_SECTIONS includes 'libraries' (#157 Phase 1)"""

    def test_libraries_in_core_sections(self):
        assert 'libraries' in CORE_SECTIONS


class TestMigrateToLibraries:
    """Tests for migrate_to_libraries (#157 Phase 1)"""

    def test_returns_none_if_libraries_already_present(self, tmp_path):
        config = {'plex': {'movie_library': 'Movies'}, 'libraries': [{'id': 'movies'}]}
        result = migrate_to_libraries(config, str(tmp_path))
        assert result is None

    def test_returns_none_if_no_legacy_library_keys(self, tmp_path):
        config = {'plex': {'url': 'http://localhost:32400'}}
        result = migrate_to_libraries(config, str(tmp_path))
        assert result is None

    def test_builds_two_entries_from_scalar_config(self, tmp_path):
        config = {'plex': {'movie_library': 'Movies', 'tv_library': 'TV Shows'}}
        result = migrate_to_libraries(config, str(tmp_path))
        assert len(result) == 2
        assert result[0]['id'] == 'movies'
        assert result[0]['media_type'] == 'movie'
        assert result[0]['name'] == 'Movies'
        assert result[1]['id'] == 'tv-shows'
        assert result[1]['media_type'] == 'tv'
        assert result[1]['name'] == 'TV Shows'

    def test_defaults_missing_tv_library_name(self, tmp_path):
        config = {'plex': {'movie_library': 'Movies'}}
        result = migrate_to_libraries(config, str(tmp_path))
        assert len(result) == 2
        assert result[1]['name'] == 'TV Shows'

    def test_folds_radarr_routing_from_config_dict(self, tmp_path):
        config = {
            'plex': {'movie_library': 'Movies'},
            'radarr': {
                'root_folder': '/data/movies',
                'quality_profile': '4K',
                'tag': 'Curatarr',
                'monitor': True,
                'search_for_movie': True,
                'minimum_availability': 'announced',
            },
        }
        result = migrate_to_libraries(config, str(tmp_path))
        movie_arr = result[0]['arr']
        assert movie_arr['root_folder'] == '/data/movies'
        assert movie_arr['quality_profile'] == '4K'
        assert movie_arr['tag'] == 'Curatarr'
        assert movie_arr['monitor'] is True
        assert movie_arr['search'] is True
        assert movie_arr['minimum_availability'] == 'announced'

    def test_folds_sonarr_routing_from_config_dict(self, tmp_path):
        config = {
            'plex': {'tv_library': 'TV Shows'},
            'sonarr': {
                'root_folder': '/data/tv',
                'series_type': 'anime',
                'search_for_series': True,
            },
        }
        result = migrate_to_libraries(config, str(tmp_path))
        tv_arr = result[1]['arr']
        assert tv_arr['root_folder'] == '/data/tv'
        assert tv_arr['series_type'] == 'anime'
        assert tv_arr['search'] is True

    def test_folds_radarr_routing_from_standalone_module_file(self, tmp_path):
        """When config.yml is already modular, radarr.yml lives as a separate file."""
        radarr_path = tmp_path / 'radarr.yml'
        with open(radarr_path, 'w') as f:
            yaml.dump({'root_folder': '/data/movies', 'quality_profile': 'HD-1080p'}, f)

        config = {'plex': {'movie_library': 'Movies'}}
        result = migrate_to_libraries(config, str(tmp_path))
        assert result[0]['arr']['root_folder'] == '/data/movies'
        assert result[0]['arr']['quality_profile'] == 'HD-1080p'

    def test_only_copies_fields_present_in_legacy_config(self, tmp_path):
        config = {'plex': {'movie_library': 'Movies'}, 'radarr': {'root_folder': '/data/movies'}}
        result = migrate_to_libraries(config, str(tmp_path))
        assert result[0]['arr'] == {'root_folder': '/data/movies'}

    def test_does_not_mutate_plex_library_keys(self, tmp_path):
        """Additive: migrate_to_libraries doesn't touch plex.movie_library/tv_library."""
        config = {'plex': {'movie_library': 'Movies', 'tv_library': 'TV Shows'}}
        migrate_to_libraries(config, str(tmp_path))
        assert config['plex']['movie_library'] == 'Movies'
        assert config['plex']['tv_library'] == 'TV Shows'


class TestExtractTuningConfig:
    """Tests for extract_tuning_config function"""

    def test_extracts_tuning_sections(self):
        """Test extracts all tuning sections."""
        config = {
            'movies': {'limit_results': 50},
            'tv': {'limit_results': 20},
            'plex': {'url': 'test'},  # Should not be extracted
        }

        result = extract_tuning_config(config)

        assert 'movies' in result
        assert 'tv' in result
        assert 'plex' not in result

    def test_returns_empty_if_no_tuning(self):
        """Test returns empty dict if no tuning sections."""
        config = {'plex': {'url': 'test'}}

        result = extract_tuning_config(config)

        assert result == {}


class TestExtractFeatureConfig:
    """Tests for extract_feature_config function"""

    def test_extracts_root_level_feature(self):
        """Test extracts feature from root level."""
        config = {'trakt': {'enabled': True, 'client_id': 'abc'}}

        result = extract_feature_config(config, 'trakt')

        assert result == {'enabled': True, 'client_id': 'abc'}

    def test_extracts_nested_radarr(self):
        """Test extracts radarr from movies section."""
        config = {'movies': {'radarr': {'enabled': True, 'url': 'http://localhost'}}}

        result = extract_feature_config(config, 'radarr')

        assert result == {'enabled': True, 'url': 'http://localhost'}

    def test_extracts_nested_sonarr(self):
        """Test extracts sonarr from tv section."""
        config = {'tv': {'sonarr': {'enabled': True, 'url': 'http://localhost'}}}

        result = extract_feature_config(config, 'sonarr')

        assert result == {'enabled': True, 'url': 'http://localhost'}

    def test_returns_none_if_not_found(self):
        """Test returns None if feature not found."""
        config = {'plex': {'url': 'test'}}

        result = extract_feature_config(config, 'trakt')

        assert result is None

    def test_returns_none_for_disabled_empty_feature(self):
        """Test returns None for disabled/empty feature config."""
        config = {'trakt': {'enabled': False}}

        result = extract_feature_config(config, 'trakt')

        # Only enabled: False, so treated as not having real content
        assert result is None


class TestBuildCoreConfig:
    """Tests for build_core_config function"""

    def test_extracts_core_sections_only(self):
        """Test extracts only core sections."""
        config = {
            'plex': {'url': 'http://localhost'},
            'tmdb': {'api_key': 'abc'},
            'movies': {'limit_results': 50},  # Should not be included
            'trakt': {'enabled': True},  # Should not be included
        }

        result = build_core_config(config)

        assert 'plex' in result
        assert 'tmdb' in result
        assert 'movies' not in result
        assert 'trakt' not in result

    def test_returns_empty_if_no_core(self):
        """Test returns empty dict if no core sections."""
        config = {'movies': {'limit_results': 50}}

        result = build_core_config(config)

        assert result == {}


class TestMigrateConfig:
    """Tests for migrate_config function"""

    def test_returns_not_migrated_if_file_not_found(self, tmp_path):
        """Test returns not migrated if config file doesn't exist."""
        fake_path = str(tmp_path / 'nonexistent.yml')

        result = migrate_config(fake_path)

        assert result['migrated'] is False
        assert result['files_created'] == []

    def test_returns_not_migrated_if_file_empty(self, tmp_path):
        """Test returns not migrated if config file is empty."""
        config_path = str(tmp_path / 'config.yml')
        with open(config_path, 'w') as f:
            f.write('')

        result = migrate_config(config_path)

        assert result['migrated'] is False

    def test_returns_not_migrated_if_already_modular(self, tmp_path):
        """Test returns not migrated if already in modular format."""
        config_path = str(tmp_path / 'config.yml')
        config = {'plex': {'url': 'test'}, 'tmdb': {'api_key': 'abc'}}
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        result = migrate_config(config_path)

        assert result['migrated'] is False

    def test_dry_run_does_not_write_files(self, tmp_path):
        """Test dry run mode doesn't write files."""
        config_path = str(tmp_path / 'config.yml')
        config = {
            'plex': {'url': 'test'},
            'movies': {'limit_results': 50},
        }
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        result = migrate_config(config_path, dry_run=True)

        assert result['migrated'] is True
        # No tuning.yml should be created in dry run
        assert not os.path.exists(str(tmp_path / 'tuning.yml'))

    def test_creates_backup_and_module_files(self, tmp_path):
        """Test creates backup and module files."""
        config_path = str(tmp_path / 'config.yml')
        config = {
            'plex': {'url': 'http://localhost'},
            'tmdb': {'api_key': 'abc'},
            'movies': {'limit_results': 50},
            'trakt': {'enabled': True, 'client_id': 'xyz'},
        }
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        result = migrate_config(config_path)

        assert result['migrated'] is True
        assert result['backup_path'] is not None
        assert os.path.exists(result['backup_path'])
        assert 'tuning.yml' in result['files_created']
        assert 'trakt.yml' in result['files_created']
        assert os.path.exists(str(tmp_path / 'tuning.yml'))
        assert os.path.exists(str(tmp_path / 'trakt.yml'))


class TestMigrateConfigLibraries:
    """Tests for the 'libraries' hook inside migrate_config (#157 Phase 1)"""

    def test_scalar_and_arr_config_folds_into_libraries(self, tmp_path):
        """End-to-end: legacy movie_library/tv_library + radarr/sonarr routing
        become a 'libraries' list in the migrated config.yml."""
        config_path = str(tmp_path / 'config.yml')
        config = {
            'plex': {'url': 'http://localhost', 'movie_library': 'Movies', 'tv_library': 'TV Shows'},
            'tmdb': {'api_key': 'abc'},
            'radarr': {'enabled': True, 'root_folder': '/data/movies', 'quality_profile': '4K'},
            'sonarr': {'enabled': True, 'root_folder': '/data/tv', 'series_type': 'anime'},
        }
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        result = migrate_config(config_path)

        assert result['migrated'] is True
        with open(config_path, 'r') as f:
            migrated = yaml.safe_load(f)

        assert 'libraries' in migrated
        assert len(migrated['libraries']) == 2
        movie_lib = next(l for l in migrated['libraries'] if l['media_type'] == 'movie')
        tv_lib = next(l for l in migrated['libraries'] if l['media_type'] == 'tv')
        assert movie_lib['arr']['root_folder'] == '/data/movies'
        assert movie_lib['arr']['quality_profile'] == '4K'
        assert tv_lib['arr']['root_folder'] == '/data/tv'
        assert tv_lib['arr']['series_type'] == 'anime'

    def test_additive_does_not_delete_legacy_plex_keys(self, tmp_path):
        """Migration must not remove plex.movie_library/tv_library - additive only."""
        config_path = str(tmp_path / 'config.yml')
        config = {
            'plex': {'url': 'http://localhost', 'movie_library': 'Movies', 'tv_library': 'TV Shows'},
            'tmdb': {'api_key': 'abc'},
        }
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        migrate_config(config_path)

        with open(config_path, 'r') as f:
            migrated = yaml.safe_load(f)

        assert migrated['plex']['movie_library'] == 'Movies'
        assert migrated['plex']['tv_library'] == 'TV Shows'
        assert 'libraries' in migrated

    def test_idempotent_second_run_does_not_remigrate(self, tmp_path):
        """Running migrate_config again after libraries exist is a no-op."""
        config_path = str(tmp_path / 'config.yml')
        config = {
            'plex': {'url': 'http://localhost', 'movie_library': 'Movies', 'tv_library': 'TV Shows'},
            'tmdb': {'api_key': 'abc'},
        }
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        first = migrate_config(config_path)
        assert first['migrated'] is True

        second = migrate_config(config_path)
        assert second['migrated'] is False

    def test_no_migration_needed_when_no_legacy_library_keys_and_already_modular(self, tmp_path):
        """A config with only core sections and no movie_library/tv_library needs no migration."""
        config_path = str(tmp_path / 'config.yml')
        config = {'plex': {'url': 'http://localhost'}, 'tmdb': {'api_key': 'abc'}}
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        result = migrate_config(config_path)

        assert result['migrated'] is False


class TestMain:
    """Tests for main CLI entry point"""

    @patch('utils.migrate_config.migrate_config')
    def test_calls_migrate_with_args(self, mock_migrate):
        """Test main calls migrate_config with parsed args."""
        mock_migrate.return_value = {'migrated': True, 'files_created': ['tuning.yml']}

        with patch.object(sys, 'argv', ['migrate_config', 'test.yml']):
            main()

        mock_migrate.assert_called_once_with('test.yml', dry_run=False)

    @patch('utils.migrate_config.migrate_config')
    def test_handles_dry_run_flag(self, mock_migrate):
        """Test main handles --dry-run flag."""
        mock_migrate.return_value = {'migrated': True, 'files_created': []}

        with patch.object(sys, 'argv', ['migrate_config', 'test.yml', '--dry-run']):
            main()

        mock_migrate.assert_called_once_with('test.yml', dry_run=True)

    @patch('utils.migrate_config.migrate_config')
    def test_handles_no_migration(self, mock_migrate):
        """Test main handles case where no migration performed."""
        mock_migrate.return_value = {'migrated': False, 'files_created': []}

        with patch.object(sys, 'argv', ['migrate_config', 'test.yml']):
            main()

    @patch('utils.migrate_config.migrate_config')
    def test_uses_default_config_path(self, mock_migrate):
        """Test uses default config path if not specified."""
        mock_migrate.return_value = {'migrated': False, 'files_created': []}

        with patch.object(sys, 'argv', ['migrate_config']):
            main()

        mock_migrate.assert_called_once_with('config/config.yml', dry_run=False)


class TestMigrateUpdateMode:
    """Tests for migrate_update_mode - additive general.update_mode
    derivation from legacy general.auto_update, same pattern as
    migrate_to_libraries."""

    def test_returns_none_if_update_mode_already_present(self):
        config = {'general': {'update_mode': 'off', 'auto_update': True}}
        assert migrate_update_mode(config) is None

    def test_returns_none_if_no_general_section(self):
        assert migrate_update_mode({}) is None

    def test_returns_none_if_no_legacy_auto_update(self):
        config = {'general': {'plex_only': True}}
        assert migrate_update_mode(config) is None

    def test_derives_force_from_auto_update_true(self):
        config = {'general': {'auto_update': True}}
        assert migrate_update_mode(config) == 'force'

    def test_derives_off_from_auto_update_false(self):
        config = {'general': {'auto_update': False}}
        assert migrate_update_mode(config) == 'off'


class TestMigrateConfigUpdateMode:
    """Tests for the 'update_mode' hook inside migrate_config, wired in
    alongside the 'libraries' hook (#157-style additive migration)."""

    def test_persists_derived_update_mode_alongside_libraries(self, tmp_path):
        """End-to-end: a legacy auto_update flag gets an explicit
        update_mode persisted into the migrated config.yml, without
        removing auto_update."""
        config_path = str(tmp_path / 'config.yml')
        config = {
            'plex': {'url': 'http://localhost', 'movie_library': 'Movies', 'tv_library': 'TV Shows'},
            'tmdb': {'api_key': 'abc'},
            'general': {'auto_update': True},
        }
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        result = migrate_config(config_path)

        assert result['migrated'] is True
        with open(config_path, 'r') as f:
            migrated = yaml.safe_load(f)

        assert migrated['general']['update_mode'] == 'force'
        assert migrated['general']['auto_update'] is True

    def test_no_update_mode_key_written_when_no_legacy_auto_update(self, tmp_path):
        """Idempotent: nothing to derive from means no update_mode key is
        added at all - a fresh install with no auto_update just gets the
        'notify' default at runtime (get_update_mode), not a persisted key."""
        config_path = str(tmp_path / 'config.yml')
        config = {
            'plex': {'url': 'http://localhost', 'movie_library': 'Movies'},
            'tmdb': {'api_key': 'abc'},
            'general': {'plex_only': True},
        }
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        migrate_config(config_path)

        with open(config_path, 'r') as f:
            migrated = yaml.safe_load(f)

        assert 'update_mode' not in migrated.get('general', {})
