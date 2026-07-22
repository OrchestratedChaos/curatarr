"""Tests for web/config_io.py - round-trip YAML load/save, atomic
writes, and the secret-masking helpers the config screens rely on."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.config_io import (
    format_csv_list,
    is_secret_field,
    load_module,
    merge_secret,
    module_path,
    parse_csv_list,
    save_module,
    secret_status,
)


class TestModulePath:
    def test_builds_path_under_config_dir(self, tmp_path):
        path = module_path(str(tmp_path), 'sonarr')
        assert path == os.path.join(str(tmp_path), 'config', 'sonarr.yml')

    def test_rejects_unknown_module(self, tmp_path):
        import pytest
        with pytest.raises(ValueError):
            module_path(str(tmp_path), 'not-a-real-module')


class TestLoadSaveRoundTrip:
    def test_load_missing_file_returns_empty_map(self, tmp_path):
        data = load_module(str(tmp_path / 'config' / 'sonarr.yml'))
        assert dict(data) == {}

    def test_save_then_load_round_trips_values(self, tmp_path):
        path = str(tmp_path / 'config' / 'sonarr.yml')
        data = load_module(path)
        data['enabled'] = True
        data['url'] = 'http://localhost:8989'
        data['plex_users'] = ['alice']
        save_module(path, data)

        reloaded = load_module(path)
        assert reloaded['enabled'] is True
        assert reloaded['url'] == 'http://localhost:8989'
        assert reloaded['plex_users'] == ['alice']

    def test_save_preserves_comments(self, tmp_path):
        config_dir = tmp_path / 'config'
        config_dir.mkdir()
        path = config_dir / 'sonarr.yml'
        path.write_text(
            "# Curatarr Sonarr Configuration\n"
            "\n"
            "enabled: true\n"
            "url: http://localhost:8989\n"
            "api_key: secret123\n",
            encoding='utf-8',
        )

        data = load_module(str(path))
        data['url'] = 'http://newhost:8989'
        save_module(str(path), data)

        content = path.read_text(encoding='utf-8')
        assert '# Curatarr Sonarr Configuration' in content
        assert 'http://newhost:8989' in content
        # untouched key survives
        assert 'api_key: secret123' in content

    def test_save_preserves_untouched_sibling_keys(self, tmp_path):
        config_dir = tmp_path / 'config'
        config_dir.mkdir()
        path = config_dir / 'radarr.yml'
        path.write_text(
            "enabled: false\n"
            "url: http://localhost:7878\n"
            "root_folder: /movies\n"
            "quality_profile: HD-1080p\n",
            encoding='utf-8',
        )

        data = load_module(str(path))
        data['enabled'] = True
        save_module(str(path), data)

        reloaded = load_module(str(path))
        assert reloaded['enabled'] is True
        assert reloaded['root_folder'] == '/movies'
        assert reloaded['quality_profile'] == 'HD-1080p'

    def test_save_is_atomic_no_temp_file_left_behind(self, tmp_path):
        config_dir = tmp_path / 'config'
        config_dir.mkdir()
        path = config_dir / 'tuning.yml'
        data = load_module(str(path))
        data['movies'] = {'limit_results': 50}
        save_module(str(path), data)

        leftovers = [f for f in os.listdir(config_dir) if f.startswith('.tmp-')]
        assert leftovers == []
        assert path.exists()

    def test_load_empty_file_returns_empty_map(self, tmp_path):
        config_dir = tmp_path / 'config'
        config_dir.mkdir()
        path = config_dir / 'trakt.yml'
        path.write_text('', encoding='utf-8')
        assert dict(load_module(str(path))) == {}


class TestSecretHelpers:
    def test_is_secret_field(self):
        assert is_secret_field('token') is True
        assert is_secret_field('api_key') is True
        assert is_secret_field('client_secret') is True
        assert is_secret_field('url') is False
        assert is_secret_field('display_name') is False

    def test_merge_secret_blank_keeps_existing(self):
        assert merge_secret('old-token', '') == 'old-token'
        assert merge_secret('old-token', '   ') == 'old-token'

    def test_merge_secret_nonblank_overwrites(self):
        assert merge_secret('old-token', 'new-token') == 'new-token'

    def test_merge_secret_no_existing_no_submission(self):
        assert merge_secret(None, '') == ''
        assert merge_secret('', '') == ''

    def test_secret_status(self):
        assert secret_status('a-real-token') == 'configured'
        assert secret_status('') == 'not set'
        assert secret_status(None) == 'not set'
        assert secret_status('   ') == 'not set'


class TestCsvHelpers:
    def test_parse_csv_list(self):
        assert parse_csv_list('a, b, c') == ['a', 'b', 'c']
        assert parse_csv_list('a,,b') == ['a', 'b']
        assert parse_csv_list('') == []
        assert parse_csv_list(None) == []

    def test_format_csv_list(self):
        assert format_csv_list(['a', 'b']) == 'a, b'
        assert format_csv_list([]) == ''
        assert format_csv_list(None) == ''
        assert format_csv_list('already-a-string') == 'already-a-string'
