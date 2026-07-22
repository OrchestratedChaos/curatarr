"""Tests for web/status.py - log parsing for the dashboard and results page."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from web.status import get_last_run_status, list_log_files, read_log_tail


def _write_log(logs_dir, name, content):
    path = os.path.join(str(logs_dir), name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


class TestGetLastRunStatus:
    """Tests for get_last_run_status()"""

    def test_never_run_when_no_logs(self, tmp_path):
        result = get_last_run_status(str(tmp_path), 'alice')
        assert result == {'status': 'never_run', 'timestamp': None, 'log_file': None}

    def test_success_when_no_failure_markers(self, tmp_path):
        _write_log(tmp_path, 'recommendations_alice_20260101_030000.log', 'Processing alice\nDone\n')
        result = get_last_run_status(str(tmp_path), 'alice')
        assert result['status'] == 'success'
        assert result['log_file'] == 'recommendations_alice_20260101_030000.log'
        assert result['timestamp'] == datetime(2026, 1, 1, 3, 0, 0)

    def test_failed_when_traceback_present(self, tmp_path):
        _write_log(
            tmp_path, 'recommendations_alice_20260101_030000.log',
            'Processing alice\nTraceback (most recent call last):\nValueError\n',
        )
        result = get_last_run_status(str(tmp_path), 'alice')
        assert result['status'] == 'failed'

    def test_failed_when_fatal_error_present(self, tmp_path):
        _write_log(tmp_path, 'recommendations_alice_20260101_030000.log', 'Fatal error detected\n')
        result = get_last_run_status(str(tmp_path), 'alice')
        assert result['status'] == 'failed'

    def test_unknown_when_log_empty(self, tmp_path):
        _write_log(tmp_path, 'recommendations_alice_20260101_030000.log', '')
        result = get_last_run_status(str(tmp_path), 'alice')
        assert result['status'] == 'unknown'

    def test_picks_newest_log_by_mtime(self, tmp_path):
        older = _write_log(tmp_path, 'recommendations_alice_20260101_030000.log', 'ok\n')
        newer = _write_log(
            tmp_path, 'recommendations_alice_20260102_030000.log',
            'Traceback (most recent call last):\n',
        )
        os.utime(older, (1, 1))
        os.utime(newer, (100, 100))
        result = get_last_run_status(str(tmp_path), 'alice')
        assert result['log_file'] == 'recommendations_alice_20260102_030000.log'
        assert result['status'] == 'failed'

    def test_only_matches_this_users_logs(self, tmp_path):
        _write_log(tmp_path, 'recommendations_bob_20260101_030000.log', 'ok\n')
        result = get_last_run_status(str(tmp_path), 'alice')
        assert result['status'] == 'never_run'

    def test_falls_back_to_mtime_for_unparseable_timestamp(self, tmp_path):
        # Month 13 doesn't parse as a real date - status.py should fall
        # back to the file's mtime instead of raising.
        path = _write_log(tmp_path, 'recommendations_alice_20261301_030000.log', 'ok\n')
        result = get_last_run_status(str(tmp_path), 'alice')
        assert isinstance(result['timestamp'], datetime)
        assert result['timestamp'] == datetime.fromtimestamp(os.path.getmtime(path))


class TestListLogFiles:
    """Tests for list_log_files()"""

    def test_empty_when_dir_missing(self, tmp_path):
        assert list_log_files(str(tmp_path / 'missing')) == []

    def test_lists_only_log_files_newest_first(self, tmp_path):
        a = _write_log(tmp_path, 'a.log', 'a')
        b = _write_log(tmp_path, 'b.log', 'b')
        (tmp_path / 'notes.txt').write_text('not a log')
        os.utime(a, (1, 1))
        os.utime(b, (100, 100))
        result = list_log_files(str(tmp_path))
        assert [e['name'] for e in result] == ['b.log', 'a.log']


class TestReadLogTail:
    """Tests for read_log_tail()"""

    def test_reads_content(self, tmp_path):
        _write_log(tmp_path, 'a.log', 'line1\nline2\n')
        assert read_log_tail(str(tmp_path), 'a.log') == 'line1\nline2'

    def test_raises_for_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_log_tail(str(tmp_path), 'missing.log')

    def test_raises_for_path_traversal(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_log_tail(str(tmp_path), '../secret.log')

    def test_redacts_secrets(self, tmp_path):
        _write_log(tmp_path, 'a.log', 'token=abcdef123456\n')
        result = read_log_tail(str(tmp_path), 'a.log')
        assert 'abcdef123456' not in result

    def test_truncates_to_max_lines(self, tmp_path):
        content = '\n'.join(f'line{i}' for i in range(10))
        _write_log(tmp_path, 'a.log', content)
        result = read_log_tail(str(tmp_path), 'a.log', max_lines=3)
        assert result.splitlines() == ['line7', 'line8', 'line9']
