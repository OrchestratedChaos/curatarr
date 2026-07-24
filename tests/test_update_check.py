"""Tests for utils/update_check.py - the advisory-only GitHub Releases
version check (never applies/verifies anything - see module docstring
for the security contract)."""

import json
import os
import sys
import time
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils.update_check import (
    GITHUB_RELEASES_API,
    UPDATE_CHECK_INTERVAL_HOURS,
    _fetch_latest_version as _REAL_FETCH_LATEST_VERSION,
    get_latest_version,
    parse_version,
    update_available,
)


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path, monkeypatch, _no_real_update_check_network):
    """Every test gets its own throwaway cache dir instead of touching
    the real one - prevents cross-test cache pollution and matches the
    per-user data dir get_project_root() would otherwise resolve to.

    Depends on (and so runs after) tests/conftest.py's suite-wide
    _no_real_update_check_network autouse fixture, which replaces
    _fetch_latest_version with a dummy for every OTHER test file. This
    file is the one place that needs the REAL _fetch_latest_version
    (with only requests.get mocked, per test) actually running, so it's
    restored here - explicitly depending on the broader fixture (rather
    than relying on autouse-ordering) guarantees this restoration always
    wins, regardless of fixture collection order.
    """
    monkeypatch.setattr('utils.update_check.get_project_root', lambda: str(tmp_path))
    monkeypatch.setattr('utils.update_check._fetch_latest_version', _REAL_FETCH_LATEST_VERSION)
    return tmp_path


def _cache_file_path(tmp_path):
    """Matches utils.update_check._cache_path()'s project_root/cache/
    convention (same dir every other cache file in this codebase uses -
    see recommenders/external.py)."""
    return os.path.join(str(tmp_path), 'cache', 'update_check_cache.json')


def _seed_cache(tmp_path, data):
    """Pre-write a cache file (as JSON) before calling get_latest_version,
    creating the cache/ dir first since plain open(..., 'w') doesn't."""
    path = _cache_file_path(tmp_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    return path


def _seed_cache_raw(tmp_path, text):
    """Same as _seed_cache but writes raw text - used for the
    corrupt-cache-file test."""
    path = _cache_file_path(tmp_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


class TestParseVersion:
    """Tests for parse_version - must do a real semver tuple compare,
    never a string compare (which would put "2.10.0" before "2.9.0")."""

    def test_parses_plain_version(self):
        assert parse_version('2.8.28') == (2, 8, 28)

    def test_parses_v_prefixed_version(self):
        assert parse_version('v2.8.28') == (2, 8, 28)

    def test_parses_version_with_trailing_suffix(self):
        # e.g. a pre-release/build suffix on the tag - only the leading
        # X.Y.Z is meaningful for comparison purposes.
        assert parse_version('2.8.28-rc1') == (2, 8, 28)

    def test_returns_none_for_empty_string(self):
        assert parse_version('') is None

    def test_returns_none_for_none(self):
        assert parse_version(None) is None

    def test_returns_none_for_garbage(self):
        assert parse_version('not-a-version') is None

    def test_tuple_compare_is_correct_across_digit_widths(self):
        """The whole reason this isn't a string compare: "2.10.0" must
        sort AFTER "2.9.0", which '2.10.0' > '2.9.0' as strings gets
        wrong (string compare puts '1' before '9')."""
        assert parse_version('2.10.0') > parse_version('2.9.0')


class TestGetLatestVersionFailOpen:
    """Fail-open contract: ANY error fetching the latest version returns
    None instead of raising, so a broken/offline check never blocks or
    crashes the app."""

    @patch('utils.update_check.requests.get')
    def test_network_error_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError('no network')
        result = get_latest_version(update_mode='notify', force_refresh=True)
        assert result is None

    @patch('utils.update_check.requests.get')
    def test_timeout_returns_none(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.Timeout('timed out')
        result = get_latest_version(update_mode='notify', force_refresh=True)
        assert result is None

    @patch('utils.update_check.requests.get')
    def test_http_error_returns_none(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception('rate limited')
        mock_get.return_value = mock_response
        result = get_latest_version(update_mode='notify', force_refresh=True)
        assert result is None

    @patch('utils.update_check.requests.get')
    def test_malformed_json_returns_none(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError('not json')
        mock_get.return_value = mock_response
        result = get_latest_version(update_mode='notify', force_refresh=True)
        assert result is None

    @patch('utils.update_check.requests.get')
    def test_missing_tag_name_returns_none(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'name': 'Some Release'}
        mock_get.return_value = mock_response
        result = get_latest_version(update_mode='notify', force_refresh=True)
        assert result is None

    @patch('utils.update_check.requests.get')
    def test_unparsable_tag_name_returns_none(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'tag_name': 'not-a-version'}
        mock_get.return_value = mock_response
        result = get_latest_version(update_mode='notify', force_refresh=True)
        assert result is None

    @patch('utils.update_check.requests.get')
    def test_fail_open_never_raises(self, mock_get):
        """The app must be completely unaffected by an update-check
        failure - calling get_latest_version() must never propagate an
        exception under any failure mode."""
        mock_get.side_effect = Exception('anything at all')
        try:
            result = get_latest_version(update_mode='notify', force_refresh=True)
        except Exception as e:
            pytest.fail(f'get_latest_version() raised instead of failing open: {e}')
        assert result is None


class TestGetLatestVersionOffMode:
    """update_mode='off' must skip the network entirely."""

    @patch('utils.update_check.requests.get')
    def test_off_mode_never_calls_network(self, mock_get):
        result = get_latest_version(update_mode='off')
        assert result is None
        mock_get.assert_not_called()

    @patch('utils.update_check.requests.get')
    def test_off_mode_ignores_existing_cache(self, mock_get, tmp_path):
        _seed_cache(tmp_path, {'latest': '9.9.9', 'checked_at': time.time()})
        result = get_latest_version(update_mode='off')
        assert result is None
        mock_get.assert_not_called()


class TestGetLatestVersionSuccess:
    """Successful fetch + on-disk caching behavior."""

    @patch('utils.update_check.requests.get')
    def test_no_cache_file_yet_fetches_fresh(self, mock_get, tmp_path):
        """First-ever check on a fresh install: no cache file exists at
        all (not just stale) - must fetch rather than error."""
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'tag_name': 'v2.9.0'}
        mock_get.return_value = mock_response

        result = get_latest_version(update_mode='notify')

        assert result == '2.9.0'
        mock_get.assert_called_once()

    @patch('utils.update_check.requests.get')
    def test_cache_write_failure_is_not_fatal(self, mock_get, monkeypatch):
        """A disk error writing the cache (permissions, full disk, a
        cache dir that doesn't exist, etc.) must not surface as an
        exception - worst case is just a re-check next call instead of
        respecting the cache interval. Points the cache dir at a
        nonexistent path (never created) so the real open() call fails
        naturally, instead of risky global builtins.open patching."""
        monkeypatch.setattr(
            'utils.update_check.get_project_root',
            lambda: '/nonexistent/path/that/is/never/created',
        )
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'tag_name': 'v2.9.0'}
        mock_get.return_value = mock_response

        result = get_latest_version(update_mode='notify', force_refresh=True)

        assert result == '2.9.0'

    @patch('utils.update_check.requests.get')
    def test_successful_fetch_strips_v_prefix(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'tag_name': 'v2.9.0'}
        mock_get.return_value = mock_response

        result = get_latest_version(update_mode='notify', force_refresh=True)

        assert result == '2.9.0'

    @patch('utils.update_check.requests.get')
    def test_calls_the_github_releases_api(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'tag_name': 'v2.9.0'}
        mock_get.return_value = mock_response

        get_latest_version(update_mode='notify', force_refresh=True)

        called_url = mock_get.call_args[0][0]
        assert called_url == GITHUB_RELEASES_API

    @patch('utils.update_check.requests.get')
    def test_writes_cache_after_fetch(self, mock_get, tmp_path):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'tag_name': 'v2.9.0'}
        mock_get.return_value = mock_response

        get_latest_version(update_mode='notify', force_refresh=True)

        cache_path = _cache_file_path(tmp_path)
        assert os.path.isfile(cache_path)
        with open(cache_path, encoding='utf-8') as f:
            cached = json.load(f)
        assert cached['latest'] == '2.9.0'
        assert isinstance(cached['checked_at'], (int, float))

    @patch('utils.update_check.requests.get')
    def test_fresh_cache_is_used_without_a_network_call(self, mock_get, tmp_path):
        _seed_cache(tmp_path, {'latest': '2.9.0', 'checked_at': time.time()})

        result = get_latest_version(update_mode='notify')

        assert result == '2.9.0'
        mock_get.assert_not_called()

    @patch('utils.update_check.requests.get')
    def test_stale_cache_triggers_a_fresh_fetch(self, mock_get, tmp_path):
        stale_time = time.time() - (UPDATE_CHECK_INTERVAL_HOURS + 1) * 3600
        _seed_cache(tmp_path, {'latest': '2.9.0', 'checked_at': stale_time})

        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'tag_name': 'v2.10.0'}
        mock_get.return_value = mock_response

        result = get_latest_version(update_mode='notify')

        assert result == '2.10.0'
        mock_get.assert_called_once()

    @patch('utils.update_check.requests.get')
    def test_force_refresh_bypasses_fresh_cache(self, mock_get, tmp_path):
        _seed_cache(tmp_path, {'latest': '2.9.0', 'checked_at': time.time()})

        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'tag_name': 'v2.10.0'}
        mock_get.return_value = mock_response

        result = get_latest_version(update_mode='notify', force_refresh=True)

        assert result == '2.10.0'
        mock_get.assert_called_once()

    @patch('utils.update_check.requests.get')
    def test_corrupt_cache_file_is_ignored_not_fatal(self, mock_get, tmp_path):
        _seed_cache_raw(tmp_path, '{not valid json')

        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {'tag_name': 'v2.9.0'}
        mock_get.return_value = mock_response

        result = get_latest_version(update_mode='notify')

        assert result == '2.9.0'


class TestUpdateAvailable:
    """Tests for update_available - the (latest, current, is_newer)
    resolver used by every surface (CLI/run.sh/run.ps1/web)."""

    @patch('utils.update_check.__version__', '2.8.28')
    @patch('utils.update_check.get_latest_version')
    def test_newer_release_reports_is_newer_true(self, mock_get_latest):
        mock_get_latest.return_value = '2.9.0'
        latest, current, is_newer = update_available(update_mode='notify')
        assert latest == '2.9.0'
        assert current == '2.8.28'
        assert is_newer is True

    @patch('utils.update_check.__version__', '2.8.28')
    @patch('utils.update_check.get_latest_version')
    def test_same_version_reports_is_newer_false(self, mock_get_latest):
        mock_get_latest.return_value = '2.8.28'
        _, _, is_newer = update_available(update_mode='notify')
        assert is_newer is False

    @patch('utils.update_check.__version__', '2.8.28')
    @patch('utils.update_check.get_latest_version')
    def test_older_release_reports_is_newer_false(self, mock_get_latest):
        mock_get_latest.return_value = '2.7.0'
        _, _, is_newer = update_available(update_mode='notify')
        assert is_newer is False

    @patch('utils.update_check.get_latest_version')
    def test_unknown_latest_reports_is_newer_false(self, mock_get_latest):
        """"We don't know" (network failure etc.) must never be treated
        as "yes there's an update"."""
        mock_get_latest.return_value = None
        latest, _, is_newer = update_available(update_mode='notify')
        assert latest is None
        assert is_newer is False

    @patch('utils.update_check.__version__', '2.8.28')
    @patch('utils.update_check.get_latest_version')
    def test_double_digit_minor_version_compares_correctly(self, mock_get_latest):
        """Regression guard for the string-vs-tuple compare bug: v2.10.0
        must register as newer than v2.8.28."""
        mock_get_latest.return_value = '2.10.0'
        _, _, is_newer = update_available(update_mode='notify')
        assert is_newer is True
