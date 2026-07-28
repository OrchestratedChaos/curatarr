"""Tests for utils/cli.py - CLI utilities"""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.cli import (
    get_users_from_config,
    print_runtime,
    print_update_notice,
    resolve_admin_username,
    run_recommender_main,
    setup_log_file,
    teardown_log_file,
    update_config_for_user,
)


@pytest.fixture(autouse=True)
def _no_real_update_check(monkeypatch):
    """Prevent every run_recommender_main() test in this file from making
    a real network call via the new print_update_notice() call inside it
    - default to "no update available" so existing tests (which don't
    care about the update notice) aren't slowed down or made
    network-dependent. TestPrintUpdateNotice below overrides this
    per-test where it actually needs to control the result."""
    monkeypatch.setattr("utils.cli.update_available", lambda **kwargs: (None, "0.0.0", False))


class TestGetUsersFromConfig:
    """Tests for get_users_from_config function"""

    def test_gets_users_from_users_list_string(self):
        """Test extracts users from users.list as comma-separated string."""
        config = {"users": {"list": "alice, bob, charlie"}}

        result = get_users_from_config(config)

        assert result == ["alice", "bob", "charlie"]

    def test_gets_users_from_users_list_array(self):
        """Test extracts users from users.list as array."""
        config = {"users": {"list": ["alice", "bob"]}}

        result = get_users_from_config(config)

        assert result == ["alice", "bob"]

    def test_falls_back_to_plex_users_string(self):
        """Test falls back to plex_users.users string format."""
        config = {"users": {}, "plex_users": {"users": "user1, user2"}}

        result = get_users_from_config(config)

        assert result == ["user1", "user2"]

    def test_falls_back_to_plex_users_array(self):
        """Test falls back to plex_users.users array format."""
        config = {"users": {}, "plex_users": {"users": ["user1", "user2"]}}

        result = get_users_from_config(config)

        assert result == ["user1", "user2"]

    def test_falls_back_to_managed_users(self):
        """Test falls back to plex.managed_users (oldest format)."""
        config = {"users": {}, "plex_users": {}, "plex": {"managed_users": "legacy_user"}}

        result = get_users_from_config(config)

        assert result == ["legacy_user"]

    def test_returns_empty_list_if_no_users(self):
        """Test returns empty list when no users configured."""
        config = {}

        result = get_users_from_config(config)

        assert result == []

    def test_strips_whitespace_from_user_names(self):
        """Test strips whitespace from user names."""
        config = {"users": {"list": "  alice  ,  bob  "}}

        result = get_users_from_config(config)

        assert result == ["alice", "bob"]

    def test_skips_empty_strings(self):
        """Test skips empty strings in user list."""
        config = {"users": {"list": "alice,,bob,"}}

        result = get_users_from_config(config)

        assert result == ["alice", "bob"]

    def test_ignores_plex_users_none_string(self):
        """Test ignores plex_users.users when set to 'None' string."""
        config = {"users": {}, "plex_users": {"users": "None"}, "plex": {"managed_users": "fallback_user"}}

        result = get_users_from_config(config)

        assert result == ["fallback_user"]


class TestResolveAdminUsername:
    """Tests for resolve_admin_username function"""

    def test_returns_username_if_not_admin(self):
        """Test returns original username if not admin."""
        result = resolve_admin_username("regular_user", "token123")

        assert result == "regular_user"

    @patch("utils.cli.MyPlexAccount")
    def test_resolves_admin_to_account_username(self, mock_account):
        """Test resolves 'Admin' to actual account username."""
        mock_account.return_value.username = "actual_admin_name"

        result = resolve_admin_username("Admin", "token123")

        assert result == "actual_admin_name"
        mock_account.assert_called_once_with(token="token123")

    @patch("utils.cli.MyPlexAccount")
    def test_resolves_administrator_to_account_username(self, mock_account):
        """Test resolves 'Administrator' to actual account username."""
        mock_account.return_value.username = "actual_admin_name"

        result = resolve_admin_username("Administrator", "token123")

        assert result == "actual_admin_name"

    @patch("utils.cli.MyPlexAccount")
    def test_returns_original_on_exception(self, mock_account):
        """Test returns original username if resolution fails."""
        mock_account.side_effect = Exception("Network error")

        result = resolve_admin_username("Admin", "token123")

        assert result == "Admin"

    def test_case_insensitive_admin_check(self):
        """Test admin check is case insensitive."""
        with patch("utils.cli.MyPlexAccount") as mock_account:
            mock_account.return_value.username = "resolved"

            result1 = resolve_admin_username("ADMIN", "token")
            result2 = resolve_admin_username("admin", "token")

            assert result1 == "resolved"
            assert result2 == "resolved"


class TestUpdateConfigForUser:
    """Tests for update_config_for_user function"""

    def test_updates_managed_users(self):
        """Test updates plex.managed_users."""
        config = {"plex": {"token": "abc", "managed_users": "old_user"}}

        result = update_config_for_user(config, "new_user")

        assert result["plex"]["managed_users"] == "new_user"
        # Original unchanged
        assert config["plex"]["managed_users"] == "old_user"

    def test_updates_plex_users_users(self):
        """Test updates plex_users.users when managed_users not present."""
        config = {"plex": {"token": "abc"}, "plex_users": {"users": ["old_user"]}}

        result = update_config_for_user(config, "new_user")

        assert result["plex_users"]["users"] == ["new_user"]

    def test_creates_deep_copy(self):
        """Test creates deep copy, original unchanged."""
        config = {"plex": {"token": "abc", "managed_users": "old"}, "nested": {"deep": {"value": 1}}}

        result = update_config_for_user(config, "new")

        result["nested"]["deep"]["value"] = 999
        assert config["nested"]["deep"]["value"] == 1

    def test_handles_empty_config(self):
        """Test handles empty config gracefully."""
        config = {}

        result = update_config_for_user(config, "user")

        assert result == {}


class TestSetupLogFile:
    """Tests for setup_log_file function"""

    def test_returns_false_if_retention_zero(self):
        """Test returns False if log_retention_days is 0."""
        result = setup_log_file("/tmp/logs", 0)

        assert result is False

    def test_returns_false_if_retention_negative(self):
        """Test returns False if log_retention_days is negative."""
        result = setup_log_file("/tmp/logs", -1)

        assert result is False

    def test_creates_log_directory(self, tmp_path):
        """Test creates log directory if it doesn't exist."""
        log_dir = str(tmp_path / "new_logs")
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
            result = setup_log_file(log_dir, 7, media_type="movie")

            assert result is True
            log_files = [f for f in os.listdir(log_dir) if f.startswith("movie_")]
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
            result = setup_log_file(log_dir, 7, single_user="testuser", media_type="tv")

            assert result is True
            log_files = [f for f in os.listdir(log_dir) if "testuser" in f]
            assert len(log_files) == 1
        finally:
            if sys.stdout is not original_stdout:
                sys.stdout.logfile.close()
                sys.stdout = original_stdout

    @patch("utils.cli.os.makedirs")
    def test_returns_false_on_exception(self, mock_makedirs):
        """Test returns False if setup fails."""
        mock_makedirs.side_effect = PermissionError("No permission")

        result = setup_log_file("/fake/path", 7)

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
        log_file_path = str(tmp_path / "test.log")
        original_stdout = sys.stdout

        # Simulate what setup_log_file does
        from utils.display import TeeLogger

        lf = open(log_file_path, "w", encoding="utf-8")
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
        assert "All processing completed!" in captured.out
        assert "01:30:45" in captured.out

    def test_handles_short_runtime(self, capsys):
        """Test handles short runtime with zero padding."""
        start_time = datetime.now() - timedelta(seconds=5)

        print_runtime(start_time)

        captured = capsys.readouterr()
        assert "00:00:0" in captured.out  # Could be 05 or similar


class TestGetUsersFromConfigEdgeCases:
    """Additional edge case tests for get_users_from_config"""

    def test_empty_users_list_string(self):
        """Test handles empty users.list string."""
        config = {"users": {"list": ""}}

        result = get_users_from_config(config)

        assert result == []

    def test_users_list_with_only_whitespace(self):
        """Test handles users.list with only whitespace."""
        config = {"users": {"list": "   ,  ,   "}}

        result = get_users_from_config(config)

        assert result == []

    def test_empty_plex_users_list(self):
        """Test handles empty plex_users.users list."""
        config = {"users": {}, "plex_users": {"users": []}}

        result = get_users_from_config(config)

        assert result == []

    def test_plex_users_none_lowercase(self):
        """Test ignores plex_users.users with 'none' (lowercase)."""
        config = {"users": {}, "plex_users": {"users": "none"}, "plex": {"managed_users": "fallback"}}

        result = get_users_from_config(config)

        assert result == ["fallback"]


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

    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    @patch("utils.cli.setup_logging")
    def test_exits_on_config_load_error(self, mock_setup_log, mock_parse_args, mock_root, mock_open, mock_yaml):
        """Test exits with code 1 if config cannot be loaded."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_open.side_effect = FileNotFoundError("No config")

        mock_process = Mock()

        with pytest.raises(SystemExit) as exc_info:
            run_recommender_main("Movie", "Test", mock_process)

        assert exc_info.value.code == 1

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_exits_if_no_users_configured(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """Test exits with code 1 if no users configured."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}}  # No users
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()

        with pytest.raises(SystemExit) as exc_info:
            run_recommender_main("Movie", "Test", mock_process)

        assert exc_info.value.code == 1

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_processes_each_user(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """Test calls process_func for each configured user."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}, "users": {"list": "alice, bob"}}
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u  # Return unchanged

        run_recommender_main("Movie", "Test", mock_process)

        assert mock_process.call_count == 2

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_single_user_mode(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """Test processes only specified user in single user mode."""
        mock_parse_args.return_value = Mock(username="bob", debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}, "users": {"list": "alice, bob, charlie"}}
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process)

        assert mock_process.call_count == 1

    @patch("utils.cli.log_error")
    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_single_user_mode_rejects_unconfigured_username(
        self,
        mock_parse_args,
        mock_root,
        mock_open,
        mock_yaml,
        mock_setup_log,
        mock_resolve,
        mock_print,
        mock_migrate,
        mock_error,
    ):
        """A username not in the configured user list must be rejected
        before it can reach collection/label creation on live Plex - see
        utils/cli.py's own comment on this check for the incident this
        covers (`python3 recommenders/movie.py alice` for a nonexistent
        user creating real collections/labels)."""
        mock_parse_args.return_value = Mock(username="alice", debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}, "users": {"list": "bob, charlie"}}
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()

        with pytest.raises(SystemExit) as exc_info:
            run_recommender_main("Movie", "Test", mock_process)

        assert exc_info.value.code == 1
        mock_process.assert_not_called()
        error_message = mock_error.call_args[0][0]
        assert "alice" in error_message
        assert "bob" in error_message
        assert "charlie" in error_message

    @patch("utils.cli.log_error")
    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_single_user_mode_accepts_configured_username_case_insensitively(
        self,
        mock_parse_args,
        mock_root,
        mock_open,
        mock_yaml,
        mock_setup_log,
        mock_resolve,
        mock_print,
        mock_migrate,
        mock_error,
    ):
        """A configured username still works unchanged, including when
        the casing typed on the command line doesn't match config.yml's
        casing exactly."""
        mock_parse_args.return_value = Mock(username="BOB", debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}, "users": {"list": "bob, charlie"}}
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process)

        assert mock_process.call_count == 1
        mock_error.assert_not_called()

    @patch("utils.cli.log_error")
    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_single_user_mode_accepts_admin_keyword_even_if_unconfigured(
        self,
        mock_parse_args,
        mock_root,
        mock_open,
        mock_yaml,
        mock_setup_log,
        mock_resolve,
        mock_print,
        mock_migrate,
        mock_error,
    ):
        """'Admin'/'Administrator' are always accepted regardless of the
        configured user list - resolve_admin_username() resolves either
        to the real Plex account username downstream."""
        mock_parse_args.return_value = Mock(username="Admin", debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}, "users": {"list": "bob, charlie"}}
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process)

        assert mock_process.call_count == 1
        mock_error.assert_not_called()

    @patch("utils.cli.log_error")
    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_single_user_mode_unconfigured_username_creates_nothing(
        self,
        mock_parse_args,
        mock_root,
        mock_open,
        mock_yaml,
        mock_setup_log,
        mock_resolve,
        mock_print,
        mock_migrate,
        mock_error,
    ):
        """No users configured at all (not even the requested one) still
        rejects a --username value cleanly instead of falling through to
        a single-element all_users list built straight from the raw arg."""
        mock_parse_args.return_value = Mock(username="alice", debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}}  # No users configured at all
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()

        with pytest.raises(SystemExit) as exc_info:
            run_recommender_main("Movie", "Test", mock_process)

        assert exc_info.value.code == 1
        mock_process.assert_not_called()

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_enables_debug_logging(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """Test enables debug logging when --debug flag is set."""
        mock_parse_args.return_value = Mock(username=None, debug=True, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}, "users": {"list": "alice"}}
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_logger = Mock()
        mock_setup_log.return_value = mock_logger
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process)

        mock_setup_log.assert_called_once()
        call_kwargs = mock_setup_log.call_args
        assert call_kwargs[1]["debug"] is True

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_calls_user_migration_before_adapting_config(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """Test run_recommender_main invokes rename migration and re-reads
        config.yml when a rename was migrated, so downstream processing
        sees the updated usernames."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.side_effect = [
            {"plex": {"token": "abc"}, "users": {"list": "oldname"}},
            {"plex": {"token": "abc"}, "users": {"list": "newname"}},
        ]
        mock_migrate.return_value = {"oldname": "newname"}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process)

        mock_migrate.assert_called_once()
        # Config was re-read after migration, so the adapted config (and
        # therefore the processed user) reflects the new username.
        assert mock_yaml.call_count == 2
        mock_process.assert_called_once()
        assert mock_process.call_args[0][3] == "newname"

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_skips_config_reload_when_no_renames(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """Test config.yml is only read once when no rename was detected."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}, "users": {"list": "alice"}}
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process)

        assert mock_yaml.call_count == 1


class TestRunRecommenderMainLibraryMatrixLoop:
    """Tests for the #157 Phase 3 per-library (library x user) matrix loop."""

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_single_library_install_matches_legacy_call_count(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """Regression: no 'libraries:' config (synthesized single library)
        produces exactly one process_func call per user, with the
        synthesized library passed as the 5th positional arg - same call
        count as before Phase 3."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc", "movie_library": "Movies"}, "users": {"list": "alice, bob"}}
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process, media_type_key="movie")

        assert mock_process.call_count == 2
        for call in mock_process.call_args_list:
            library = call[0][4]
            assert library is not None
            assert library["id"] == "movies"
            assert library["section"] == "Movies"

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_two_libraries_two_users_yields_four_calls(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """2 libraries x 2 users = 4 process_func invocations, each with the
        correct library object."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        root_cfg = {
            "plex": {"token": "abc"},
            "users": {"list": "alice, bob"},
            "libraries": [
                {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
                {"id": "movies-4k", "name": "Movies 4K", "section": "Movies 4K", "media_type": "movie"},
                {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"},
            ],
        }
        mock_yaml.return_value = root_cfg
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process, media_type_key="movie")

        assert mock_process.call_count == 4
        seen = set()
        for call in mock_process.call_args_list:
            resolved_user = call[0][3]
            library = call[0][4]
            seen.add((library["id"], resolved_user))
        assert seen == {
            ("movies", "alice"),
            ("movies", "bob"),
            ("movies-4k", "alice"),
            ("movies-4k", "bob"),
        }

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_media_type_key_selects_tv_libraries(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """media_type_key='tv' only loops over tv libraries, not movie ones."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = "/fake/root"
        root_cfg = {
            "plex": {"token": "abc"},
            "users": {"list": "alice"},
            "libraries": [
                {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
                {"id": "tv-shows", "name": "TV Shows", "section": "TV Shows", "media_type": "tv"},
                {"id": "anime", "name": "Anime", "section": "Anime", "media_type": "tv"},
            ],
        }
        mock_yaml.return_value = root_cfg
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("TV Show", "Test", mock_process, media_type_key="tv")

        assert mock_process.call_count == 2
        lib_ids = {call[0][4]["id"] for call in mock_process.call_args_list}
        assert lib_ids == {"tv-shows", "anime"}

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_library_flag_filters_to_single_library(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """--library <id> restricts processing to a single library."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id="movies-4k")
        mock_root.return_value = "/fake/root"
        root_cfg = {
            "plex": {"token": "abc"},
            "users": {"list": "alice"},
            "libraries": [
                {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
                {"id": "movies-4k", "name": "Movies 4K", "section": "Movies 4K", "media_type": "movie"},
            ],
        }
        mock_yaml.return_value = root_cfg
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process, media_type_key="movie")

        assert mock_process.call_count == 1
        assert mock_process.call_args[0][4]["id"] == "movies-4k"

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_library_flag_unknown_id_exits(
        self, mock_parse_args, mock_root, mock_open, mock_yaml, mock_setup_log, mock_resolve, mock_print, mock_migrate
    ):
        """--library <unknown id> exits with an error instead of silently
        processing every library."""
        mock_parse_args.return_value = Mock(username=None, debug=False, library_id="nope")
        mock_root.return_value = "/fake/root"
        mock_yaml.return_value = {"plex": {"token": "abc"}, "users": {"list": "alice"}}
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        with pytest.raises(SystemExit) as exc_info:
            run_recommender_main("Movie", "Test", mock_process, media_type_key="movie")

        assert exc_info.value.code == 1
        mock_process.assert_not_called()


class TestPrintUpdateNotice:
    """Tests for print_update_notice - the CLI surface of the update
    notification feature (the only update signal that reaches binary
    users - see docstring)."""

    @patch("utils.cli.update_available")
    def test_off_mode_no_newer_version_prints_nothing(self, mock_update_available, capsys):
        mock_update_available.return_value = ("2.8.28", "2.8.28", False)
        print_update_notice("off")
        mock_update_available.assert_called_once_with(update_mode="off")
        assert capsys.readouterr().out == ""

    @patch("utils.cli.update_available")
    def test_off_mode_still_prints_when_a_newer_version_exists(self, mock_update_available, capsys, monkeypatch):
        """As of v2.8.31, 'off' only means "don't auto-apply" - it must
        NOT suppress the notice itself (that was the bug this fixed)."""
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        mock_update_available.return_value = ("2.9.0", "2.8.28", True)
        print_update_notice("off")
        out = capsys.readouterr().out
        assert "Update available: v2.9.0" in out

    @patch("utils.cli.update_available")
    def test_no_newer_version_prints_nothing(self, mock_update_available, capsys):
        mock_update_available.return_value = ("2.8.28", "2.8.28", False)
        print_update_notice("notify")
        assert capsys.readouterr().out == ""

    @patch("utils.cli.is_dismissed")
    @patch("utils.cli.update_available")
    def test_dismissed_version_prints_nothing(self, mock_update_available, mock_is_dismissed, capsys):
        """Respects the same 7-day snooze the web UI's dismiss button
        writes (utils.update_dismissal) - see tests/test_update_dismissal.py
        for the snooze-window/version-override unit tests themselves."""
        mock_update_available.return_value = ("2.9.0", "2.8.28", True)
        mock_is_dismissed.return_value = True
        print_update_notice("notify")
        mock_is_dismissed.assert_called_once_with("2.9.0")
        assert capsys.readouterr().out == ""

    @patch("utils.cli.is_dismissed")
    @patch("utils.cli.update_available")
    def test_non_dismissed_version_still_prints(self, mock_update_available, mock_is_dismissed, capsys, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        mock_update_available.return_value = ("2.9.0", "2.8.28", True)
        mock_is_dismissed.return_value = False
        print_update_notice("notify")
        assert "Update available: v2.9.0" in capsys.readouterr().out

    @patch("utils.cli.update_available")
    def test_source_notify_mode_points_at_run_sh(self, mock_update_available, capsys, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        mock_update_available.return_value = ("2.9.0", "2.8.28", True)
        print_update_notice("notify")
        out = capsys.readouterr().out
        assert "Update available: v2.9.0" in out
        assert "v2.8.28" in out
        assert "run.sh" in out
        assert "update_mode: force" in out

    @patch("utils.cli.update_available")
    def test_source_force_mode_omits_the_force_hint(self, mock_update_available, capsys, monkeypatch):
        """force mode is already auto-applying updates - no need to tell
        the user to set update_mode: force."""
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        mock_update_available.return_value = ("2.9.0", "2.8.28", True)
        print_update_notice("force")
        out = capsys.readouterr().out
        assert "Update available: v2.9.0" in out
        assert "update_mode: force" not in out

    @patch("utils.cli.update_available")
    def test_frozen_binary_points_at_self_update_flag_and_download_url(
        self, mock_update_available, capsys, monkeypatch
    ):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        mock_update_available.return_value = ("2.9.0", "2.8.28", True)
        print_update_notice("notify")
        out = capsys.readouterr().out
        assert "Update available: v2.9.0" in out
        assert "--self-update" in out
        assert "download:" in out
        assert "github.com/OrchestratedChaos/curatarr/releases" in out
        assert "run.sh" not in out

    @patch("utils.cli.update_available")
    def test_docker_points_at_pull_not_run_sh_or_self_update(self, mock_update_available, capsys, monkeypatch):
        """RUNNING_IN_DOCKER wins over both the frozen and source
        branches - a container is neither: there's no on-disk .git to
        check out against and no frozen binary to swap (see run.sh's
        RUNNING_IN_DOCKER-gated --check-verified-update/
        --apply-verified-update and web/update_apply.py's
        UpdateManager.begin_update)."""
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        mock_update_available.return_value = ("2.9.0", "2.8.28", True)
        print_update_notice("notify")
        out = capsys.readouterr().out
        assert "Update available: v2.9.0" in out
        assert "docker pull" in out
        assert "run.sh" not in out
        assert "--self-update" not in out


class TestRunRecommenderMainCachePruneScope:
    """Tests for the resolved_usernames -> prune_orphaned_cache_files
    wiring at the end of run_recommender_main (#233 audit remediation
    batch D / PR1(b)), and the single-user-mode false-orphan bug found
    in pre-release review: single_user narrows all_users to just the one
    user being processed, so resolved_usernames would only ever contain
    that one user - pruning against it would misclassify every OTHER
    configured user's still-live cache as orphaned. Every cache_dir here
    is tmp_path/cache - never the real cache/ directory - and every test
    sets dry_run: false so a regression would actually delete files, not
    just log a false candidate.
    """

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_single_user_run_does_not_orphan_other_configured_users(
        self,
        mock_parse_args,
        mock_root,
        mock_open,
        mock_yaml,
        mock_setup_log,
        mock_resolve,
        mock_print,
        mock_migrate,
        tmp_path,
    ):
        """A single-user run for 'bob' must never treat 'alice' (still
        configured, just not processed this run) as orphaned - even with
        cache_prune.dry_run: false, which would delete it if this run
        pruned using only the current run's resolved_usernames."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        alice_cache = cache_dir / "watched_cache_plex_alice.json"
        alice_cache.write_text("{}", encoding="utf-8")

        mock_parse_args.return_value = Mock(username="bob", debug=False, library_id=None)
        mock_root.return_value = str(tmp_path)
        config = {
            "plex": {"token": "abc"},
            "users": {"list": "alice, bob"},
            "general": {"cache_prune": {"enabled": True, "dry_run": False}},
        }
        mock_yaml.return_value = config
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process)

        assert alice_cache.exists()

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_full_run_still_prunes_a_user_removed_from_config(
        self,
        mock_parse_args,
        mock_root,
        mock_open,
        mock_yaml,
        mock_setup_log,
        mock_resolve,
        mock_print,
        mock_migrate,
        tmp_path,
    ):
        """A full run (no username arg) must still prune 'charlie', who
        is no longer in users.list - this is the legitimate case the
        feature exists to handle, and scoping pruning to full-run-only
        must not break it."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        alice_cache = cache_dir / "watched_cache_plex_alice.json"
        alice_cache.write_text("{}", encoding="utf-8")
        removed_cache = cache_dir / "watched_cache_plex_charlie.json"
        removed_cache.write_text("{}", encoding="utf-8")

        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = str(tmp_path)
        config = {
            "plex": {"token": "abc"},
            "users": {"list": "alice"},
            "general": {"cache_prune": {"enabled": True, "dry_run": False}},
        }
        mock_yaml.return_value = config
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: u

        run_recommender_main("Movie", "Test", mock_process)

        assert alice_cache.exists()
        assert not removed_cache.exists()

    @patch("utils.cli.migrate_renamed_plex_users")
    @patch("utils.cli.print_runtime")
    @patch("utils.cli.resolve_admin_username")
    @patch("utils.cli.setup_logging")
    @patch("utils.cli.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("utils.cli.get_project_root")
    @patch("utils.cli.argparse.ArgumentParser.parse_args")
    def test_full_run_prunes_by_resolved_not_raw_admin_username(
        self,
        mock_parse_args,
        mock_root,
        mock_open,
        mock_yaml,
        mock_setup_log,
        mock_resolve,
        mock_print,
        mock_migrate,
        tmp_path,
    ):
        """The real per-user cache filename is written under the
        RESOLVED account username (e.g. 'realadmin'), never the raw
        config string 'admin' - see utils.cache_prune.find_orphaned_
        cache_files's own docstring on this contract. Pruning must keep
        classifying by that same resolved form: if a future change fed
        the raw config string into prune_orphaned_cache_files instead,
        a config that still says 'admin' would misclassify its own live
        cache as orphaned and delete it - the exact inverse of this
        bug."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        real_admin_cache = cache_dir / "watched_cache_plex_realadmin.json"
        real_admin_cache.write_text("{}", encoding="utf-8")

        mock_parse_args.return_value = Mock(username=None, debug=False, library_id=None)
        mock_root.return_value = str(tmp_path)
        config = {
            "plex": {"token": "abc"},
            "users": {"list": "admin"},
            "general": {"cache_prune": {"enabled": True, "dry_run": False}},
        }
        mock_yaml.return_value = config
        mock_migrate.return_value = {}

        mock_process = Mock()
        mock_setup_log.return_value = Mock()
        mock_resolve.side_effect = lambda u, t: "realadmin" if u.lower() == "admin" else u

        run_recommender_main("Movie", "Test", mock_process)

        assert real_admin_cache.exists()
