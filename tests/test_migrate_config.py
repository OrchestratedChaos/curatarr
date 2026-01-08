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
