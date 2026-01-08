"""Tests for utils/cli.py - CLI utilities"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.cli import (
    get_users_from_config,
    resolve_admin_username,
    update_config_for_user,
    setup_log_file,
    teardown_log_file,
    print_runtime,
    run_recommender_main,
)


class TestGetUsersFromConfig:
    """Tests for get_users_from_config function"""

    def test_gets_users_from_users_list_string(self):
        """Test extracts users from users.list as comma-separated string."""
        config = {
            'users': {
                'list': 'alice, bob, charlie'
            }
        }

        result = get_users_from_config(config)

        assert result == ['alice', 'bob', 'charlie']

    def test_gets_users_from_users_list_array(self):
        """Test extracts users from users.list as array."""
        config = {
            'users': {
                'list': ['alice', 'bob']
            }
        }

        result = get_users_from_config(config)

        assert result == ['alice', 'bob']

    def test_falls_back_to_plex_users_string(self):
        """Test falls back to plex_users.users string format."""
        config = {
            'users': {},
            'plex_users': {
                'users': 'user1, user2'
            }
        }

        result = get_users_from_config(config)

        assert result == ['user1', 'user2']

    def test_falls_back_to_plex_users_array(self):
        """Test falls back to plex_users.users array format."""
        config = {
            'users': {},
            'plex_users': {
                'users': ['user1', 'user2']
            }
        }

        result = get_users_from_config(config)

        assert result == ['user1', 'user2']

    def test_falls_back_to_managed_users(self):
        """Test falls back to plex.managed_users (oldest format)."""
        config = {
            'users': {},
            'plex_users': {},
            'plex': {
                'managed_users': 'legacy_user'
            }
        }

        result = get_users_from_config(config)

        assert result == ['legacy_user']

    def test_returns_empty_list_if_no_users(self):
        """Test returns empty list when no users configured."""
        config = {}

        result = get_users_from_config(config)

        assert result == []

    def test_strips_whitespace_from_user_names(self):
        """Test strips whitespace from user names."""
        config = {
            'users': {
                'list': '  alice  ,  bob  '
            }
        }

        result = get_users_from_config(config)

        assert result == ['alice', 'bob']

    def test_skips_empty_strings(self):
        """Test skips empty strings in user list."""
        config = {
            'users': {
                'list': 'alice,,bob,'
            }
        }

        result = get_users_from_config(config)

        assert result == ['alice', 'bob']

    def test_ignores_plex_users_none_string(self):
        """Test ignores plex_users.users when set to 'None' string."""
        config = {
            'users': {},
            'plex_users': {
                'users': 'None'
            },
            'plex': {
                'managed_users': 'fallback_user'
            }
        }

        result = get_users_from_config(config)

        assert result == ['fallback_user']


class TestResolveAdminUsername:
    """Tests for resolve_admin_username function"""

    def test_returns_username_if_not_admin(self):
        """Test returns original username if not admin."""
        result = resolve_admin_username('regular_user', 'token123')

        assert result == 'regular_user'

    @patch('utils.cli.MyPlexAccount')
    def test_resolves_admin_to_account_username(self, mock_account):
        """Test resolves 'Admin' to actual account username."""
        mock_account.return_value.username = 'actual_admin_name'

        result = resolve_admin_username('Admin', 'token123')

        assert result == 'actual_admin_name'
        mock_account.assert_called_once_with(token='token123')

    @patch('utils.cli.MyPlexAccount')
    def test_resolves_administrator_to_account_username(self, mock_account):
        """Test resolves 'Administrator' to actual account username."""
        mock_account.return_value.username = 'actual_admin_name'

        result = resolve_admin_username('Administrator', 'token123')

        assert result == 'actual_admin_name'

    @patch('utils.cli.MyPlexAccount')
    def test_returns_original_on_exception(self, mock_account):
        """Test returns original username if resolution fails."""
        mock_account.side_effect = Exception("Network error")

        result = resolve_admin_username('Admin', 'token123')

        assert result == 'Admin'

    def test_case_insensitive_admin_check(self):
        """Test admin check is case insensitive."""
        with patch('utils.cli.MyPlexAccount') as mock_account:
            mock_account.return_value.username = 'resolved'

            result1 = resolve_admin_username('ADMIN', 'token')
            result2 = resolve_admin_username('admin', 'token')

            assert result1 == 'resolved'
            assert result2 == 'resolved'


class TestUpdateConfigForUser:
    """Tests for update_config_for_user function"""

    def test_updates_managed_users(self):
        """Test updates plex.managed_users."""
        config = {
            'plex': {
                'token': 'abc',
                'managed_users': 'old_user'
            }
        }

        result = update_config_for_user(config, 'new_user')

        assert result['plex']['managed_users'] == 'new_user'
        # Original unchanged
        assert config['plex']['managed_users'] == 'old_user'

    def test_updates_plex_users_users(self):
        """Test updates plex_users.users when managed_users not present."""
        config = {
            'plex': {'token': 'abc'},
            'plex_users': {'users': ['old_user']}
        }

        result = update_config_for_user(config, 'new_user')

        assert result['plex_users']['users'] == ['new_user']

    def test_creates_deep_copy(self):
        """Test creates deep copy, original unchanged."""
        config = {
            'plex': {'token': 'abc', 'managed_users': 'old'},
            'nested': {'deep': {'value': 1}}
        }

        result = update_config_for_user(config, 'new')

        result['nested']['deep']['value'] = 999
        assert config['nested']['deep']['value'] == 1

    def test_handles_empty_config(self):
        """Test handles empty config gracefully."""
        config = {}

        result = update_config_for_user(config, 'user')

        assert result == {}


class TestSetupLogFile:
    """Tests for setup_log_file function"""

    def test_returns_false_if_retention_zero(self):
        """Test returns False if log_retention_days is 0."""
        result = setup_log_file('/tmp/logs', 0)

        assert result is False

    def test_returns_false_if_retention_negative(self):
        """Test returns False if log_retention_days is negative."""
        result = setup_log_file('/tmp/logs', -1)

        assert result is False

    def test_creates_log_directory(self, tmp_path):
        """Test creates log directory if it doesn't exist."""
        log_dir = str(tmp_path / 'new_logs')
        original_stdout = sys.stdout

        try:
            result = setup_log_file(log_dir, 7)

            assert result is True
            assert os.path.exists(log_dir)
        finally:
            # Cleanup
            if sys.stdout is not original_stdout:
                sys.stdout.logfile.close()
                sys.stdout = original_stdout

    def test_creates_log_file_with_timestamp(self, tmp_path):
        """Test creates log file with timestamp in name."""
        log_dir = str(tmp_path)
        original_stdout = sys.stdout

        try:
            result = setup_log_file(log_dir, 7, media_type='movie')

            assert result is True
            log_files = [f for f in os.listdir(log_dir) if f.startswith('movie_')]
            assert len(log_files) == 1
        finally:
            if sys.stdout is not original_stdout:
                sys.stdout.logfile.close()
                sys.stdout = original_stdout

    def test_includes_user_suffix(self, tmp_path):
        """Test includes user suffix in log file name."""
        log_dir = str(tmp_path)
        original_stdout = sys.stdout

        try:
            result = setup_log_file(log_dir, 7, single_user='testuser', media_type='tv')

            assert result is True
            log_files = [f for f in os.listdir(log_dir) if 'testuser' in f]
            assert len(log_files) == 1
        finally:
            if sys.stdout is not original_stdout:
                sys.stdout.logfile.close()
                sys.stdout = original_stdout

    @patch('utils.cli.os.makedirs')
    def test_returns_false_on_exception(self, mock_makedirs):
        """Test returns False if setup fails."""
        mock_makedirs.side_effect = PermissionError("No permission")

        result = setup_log_file('/fake/path', 7)

        assert result is False


class TestTeardownLogFile:
    """Tests for teardown_log_file function"""

    def test_does_nothing_if_retention_zero(self):
        """Test does nothing if log_retention_days is 0."""
        original_stdout = sys.stdout

        # Should not raise
        teardown_log_file(original_stdout, 0)

    def test_does_nothing_if_stdout_not_changed(self):
        """Test does nothing if stdout hasn't been redirected."""
        original_stdout = sys.stdout

        # Should not raise
        teardown_log_file(original_stdout, 7)

    def test_closes_log_and_restores_stdout(self, tmp_path):
        """Test closes log file and restores stdout."""
        log_file_path = str(tmp_path / 'test.log')
        original_stdout = sys.stdout

        # Simulate what setup_log_file does
        from utils.display import TeeLogger
        lf = open(log_file_path, 'w', encoding='utf-8')
        sys.stdout = TeeLogger(lf)

        # Now teardown
        teardown_log_file(original_stdout, 7)

        assert sys.stdout is original_stdout
        assert lf.closed


class TestPrintRuntime:
    """Tests for print_runtime function"""

    def test_prints_formatted_runtime(self, capsys):
        """Test prints formatted runtime."""
        start_time = datetime.now() - timedelta(hours=1, minutes=30, seconds=45)

        print_runtime(start_time)

        captured = capsys.readouterr()
        assert 'All processing completed!' in captured.out
        assert '01:30:45' in captured.out

    def test_handles_short_runtime(self, capsys):
        """Test handles short runtime with zero padding."""
        start_time = datetime.now() - timedelta(seconds=5)

        print_runtime(start_time)

        captured = capsys.readouterr()
        assert '00:00:0' in captured.out  # Could be 05 or similar


class TestGetUsersFromConfigEdgeCases:
    """Additional edge case tests for get_users_from_config"""

    def test_empty_users_list_string(self):
        """Test handles empty users.list string."""
        config = {'users': {'list': ''}}

        result = get_users_from_config(config)

        assert result == []

    def test_users_list_with_only_whitespace(self):
        """Test handles users.list with only whitespace."""
        config = {'users': {'list': '   ,  ,   '}}

        result = get_users_from_config(config)

        assert result == []

    def test_empty_plex_users_list(self):
        """Test handles empty plex_users.users list."""
        config = {
            'users': {},
            'plex_users': {'users': []}
        }

        result = get_users_from_config(config)

        assert result == []

    def test_plex_users_none_lowercase(self):
        """Test ignores plex_users.users with 'none' (lowercase)."""
        config = {
            'users': {},
            'plex_users': {'users': 'none'},
            'plex': {'managed_users': 'fallback'}
        }

        result = get_users_from_config(config)

        assert result == ['fallback']


class TestTeardownLogFileException:
    """Tests for teardown_log_file exception handling"""

    def test_handles_close_exception(self, tmp_path):
        """Test handles exception when closing log file."""
        original_stdout = sys.stdout

        # Create a mock that raises on close
        mock_logfile = Mock()
        mock_logfile.close.side_effect = Exception("Close failed")

        mock_tee = Mock()
        mock_tee.logfile = mock_logfile
        sys.stdout = mock_tee

        # Should not raise, just warn
        teardown_log_file(original_stdout, 7)

        # Restore stdout for other tests
        sys.stdout = original_stdout


class TestRunRecommenderMain:
    """Tests for run_recommender_main function"""

    @patch('utils.cli.yaml.safe_load')
    @patch('builtins.open', create=True)
    @patch('utils.cli.get_project_root')
    @patch('utils.cli.argparse.ArgumentParser.parse_args')
    @patch('utils.cli.setup_logging')
    def test_exits_on_config_load_error(
        self, mock_setup_log, mock_parse_args, mock_root, mock_open, mock_yaml
    ):
        """Test exits with code 1 if config cannot be loaded."""
        mock_parse_args.return_value = Mock(username=None, debug=False)
        mock_root.return_value = '/fake/root'
        mock_open.side_effect = FileNotFoundError("No config")

        mock_adapt = Mock()
        mock_process = Mock()

        with pytest.raises(SystemExit) as exc_info:
            run_recommender_main('Movie', 'Test', mock_adapt, mock_process)

        assert exc_info.value.code == 1

    @patch('utils.cli.print_runtime')
    @patch('utils.cli.resolve_admin_username')
    @patch('utils.cli.setup_logging')
    @patch('utils.cli.yaml.safe_load')
    @patch('builtins.open', create=True)
    @patch('utils.cli.get_project_root')
    @patch('utils.cli.argparse.ArgumentParser.parse_args')
    def test_exits_if_no_users_configured(
        self, mock_parse_args, mock_root, mock_open, mock_yaml,
        mock_setup_log, mock_resolve, mock_print
    ):
        """Test exits with code 1 if no users configured."""
        mock_parse_args.return_value = Mock(username=None, debug=False)
        mock_root.return_value = '/fake/root'
        mock_yaml.return_value = {'plex': {'token': 'abc'}}  # No users

        mock_adapt = Mock(return_value={'plex': {'token': 'abc'}, 'general': {}})
        mock_process = Mock()
        mock_setup_log.return_value = Mock()

        with pytest.raises(SystemExit) as exc_info:
            run_recommender_main('Movie', 'Test', mock_adapt, mock_process)

        assert exc_info.value.code == 1

    @patch('utils.cli.print_runtime')
    @patch('utils.cli.resolve_admin_username')
    @patch('utils.cli.setup_logging')
    @patch('utils.cli.yaml.safe_load')
    @patch('builtins.open', create=True)
    @patch('utils.cli.get_project_root')
    @patch('utils.cli.argparse.ArgumentParser.parse_args')
    def test_processes_each_user(
        self, mock_parse_args, mock_root, mock_open, mock_yaml,
        mock_setup_log, mock_resolve, mock_print
    ):
        """Test calls process_func for each configured user."""
        mock_parse_args.return_value = Mock(username=None, debug=False)
        mock_root.return_value = '/fake/root'
        mock_yaml.return_value = {
            'plex': {'token': 'abc'},
            'users': {'list': 'alice, bob'}
        }

        mock_adapt = Mock(return_value={
            'plex': {'token': 'abc'},
            'users': {'list': 'alice, bob'},
            'general': {}
        })
        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u  # Return unchanged

        run_recommender_main('Movie', 'Test', mock_adapt, mock_process)

        assert mock_process.call_count == 2

    @patch('utils.cli.print_runtime')
    @patch('utils.cli.resolve_admin_username')
    @patch('utils.cli.setup_logging')
    @patch('utils.cli.yaml.safe_load')
    @patch('builtins.open', create=True)
    @patch('utils.cli.get_project_root')
    @patch('utils.cli.argparse.ArgumentParser.parse_args')
    def test_single_user_mode(
        self, mock_parse_args, mock_root, mock_open, mock_yaml,
        mock_setup_log, mock_resolve, mock_print
    ):
        """Test processes only specified user in single user mode."""
        mock_parse_args.return_value = Mock(username='bob', debug=False)
        mock_root.return_value = '/fake/root'
        mock_yaml.return_value = {
            'plex': {'token': 'abc'},
            'users': {'list': 'alice, bob, charlie'}
        }

        mock_adapt = Mock(return_value={
            'plex': {'token': 'abc'},
            'users': {'list': 'alice, bob, charlie'},
            'general': {}
        })
        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main('Movie', 'Test', mock_adapt, mock_process)

        assert mock_process.call_count == 1

    @patch('utils.cli.print_runtime')
    @patch('utils.cli.resolve_admin_username')
    @patch('utils.cli.setup_logging')
    @patch('utils.cli.yaml.safe_load')
    @patch('builtins.open', create=True)
    @patch('utils.cli.get_project_root')
    @patch('utils.cli.argparse.ArgumentParser.parse_args')
    def test_enables_debug_logging(
        self, mock_parse_args, mock_root, mock_open, mock_yaml,
        mock_setup_log, mock_resolve, mock_print
    ):
        """Test enables debug logging when --debug flag is set."""
        mock_parse_args.return_value = Mock(username=None, debug=True)
        mock_root.return_value = '/fake/root'
        mock_yaml.return_value = {
            'plex': {'token': 'abc'},
            'users': {'list': 'alice'}
        }

        mock_adapt = Mock(return_value={
            'plex': {'token': 'abc'},
            'users': {'list': 'alice'},
            'general': {}
        })
        mock_process = Mock()
        mock_logger = Mock()
        mock_setup_log.return_value = mock_logger
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main('Movie', 'Test', mock_adapt, mock_process)

        mock_setup_log.assert_called_once()
        call_kwargs = mock_setup_log.call_args
        assert call_kwargs[1]['debug'] is True
