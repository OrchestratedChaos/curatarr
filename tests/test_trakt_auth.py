"""Tests for utils/trakt_auth.py"""

import pytest
from unittest.mock import Mock, patch, mock_open
import yaml


class TestTraktAuthLoadConfig:
    """Tests for trakt_auth load_config function."""

    @patch('builtins.open', mock_open(read_data='trakt:\n  enabled: true'))
    def test_loads_config_file(self):
        """Test loads config from yaml file."""
        from utils.trakt_auth import load_config
        result = load_config()
        assert result['trakt']['enabled'] is True


class TestTraktAuthSaveTokens:
    """Tests for trakt_auth save_tokens function."""

    def test_saves_tokens_to_config(self, tmp_path, monkeypatch):
        """Test saves tokens to config file."""
        # Create initial config
        config_path = tmp_path / 'config.yml'
        initial_config = {
            'trakt': {
                'enabled': True,
                'client_id': 'test_id',
                'client_secret': 'test_secret'
            }
        }
        with open(config_path, 'w') as f:
            yaml.dump(initial_config, f)

        # Patch the path resolution
        import utils.trakt_auth as trakt_auth_module
        original_func = trakt_auth_module.save_tokens

        def patched_save_tokens(access_token, refresh_token):
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            config['trakt']['access_token'] = access_token
            config['trakt']['refresh_token'] = refresh_token
            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        patched_save_tokens('new_access', 'new_refresh')

        # Verify tokens saved
        with open(config_path, 'r') as f:
            result = yaml.safe_load(f)

        assert result['trakt']['access_token'] == 'new_access'
        assert result['trakt']['refresh_token'] == 'new_refresh'


class TestTraktAuthMain:
    """Tests for trakt_auth main function."""

    @patch('utils.trakt_auth.load_config')
    def test_exits_when_config_not_found(self, mock_load):
        """Test exits with error when config not found."""
        from utils.trakt_auth import main
        mock_load.side_effect = FileNotFoundError()

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch('utils.trakt_auth.load_config')
    def test_exits_when_trakt_disabled(self, mock_load):
        """Test exits when Trakt is disabled."""
        from utils.trakt_auth import main
        mock_load.return_value = {'trakt': {'enabled': False}}

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch('utils.trakt_auth.load_config')
    def test_exits_when_missing_credentials(self, mock_load):
        """Test exits when client_id or secret missing."""
        from utils.trakt_auth import main
        mock_load.return_value = {
            'trakt': {
                'enabled': True,
                'client_id': None,
                'client_secret': 'secret'
            }
        }

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch('utils.trakt_auth.load_config')
    def test_exits_when_already_authenticated(self, mock_load):
        """Test exits cleanly when already authenticated."""
        from utils.trakt_auth import main
        mock_load.return_value = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': 'existing_token'
            }
        }

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    @patch('utils.trakt_auth.TraktClient')
    @patch('utils.trakt_auth.load_config')
    def test_starts_device_auth_flow(self, mock_load, mock_client_class):
        """Test starts device auth flow when not authenticated."""
        from utils.trakt_auth import main

        mock_load.return_value = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': None
            }
        }

        mock_client = Mock()
        mock_client.get_device_code.return_value = {
            'device_code': 'abc123',
            'user_code': 'XYZ789',
            'verification_url': 'https://trakt.tv/activate',
            'interval': 5,
            'expires_in': 600
        }
        mock_client.poll_for_token.return_value = True
        mock_client.get_username.return_value = 'testuser'
        mock_client_class.return_value = mock_client

        # Should complete without exit
        main()

        mock_client.get_device_code.assert_called_once()
        mock_client.poll_for_token.assert_called_once()

    @patch('utils.trakt_auth.TraktClient')
    @patch('utils.trakt_auth.load_config')
    def test_handles_auth_failure(self, mock_load, mock_client_class):
        """Test handles authentication failure."""
        from utils.trakt_auth import main

        mock_load.return_value = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': None
            }
        }

        mock_client = Mock()
        mock_client.get_device_code.return_value = {
            'device_code': 'abc',
            'user_code': 'XYZ',
            'verification_url': 'https://trakt.tv/activate'
        }
        mock_client.poll_for_token.return_value = False
        mock_client_class.return_value = mock_client

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch('utils.trakt_auth.TraktClient')
    @patch('utils.trakt_auth.load_config')
    def test_handles_trakt_auth_error(self, mock_load, mock_client_class):
        """Test handles TraktAuthError exception."""
        from utils.trakt_auth import main, TraktAuthError

        mock_load.return_value = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': None
            }
        }

        mock_client = Mock()
        mock_client.get_device_code.side_effect = TraktAuthError("Auth failed")
        mock_client_class.return_value = mock_client

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch('utils.trakt_auth.TraktClient')
    @patch('utils.trakt_auth.load_config')
    def test_handles_keyboard_interrupt(self, mock_load, mock_client_class):
        """Test handles KeyboardInterrupt gracefully."""
        from utils.trakt_auth import main

        mock_load.return_value = {
            'trakt': {
                'enabled': True,
                'client_id': 'id',
                'client_secret': 'secret',
                'access_token': None
            }
        }

        mock_client = Mock()
        mock_client.get_device_code.side_effect = KeyboardInterrupt()
        mock_client_class.return_value = mock_client

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
