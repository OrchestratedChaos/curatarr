"""Tests for utils/tautulli.py - Tautulli API client and watch-history merge."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from utils.tautulli import (
    TautulliClient,
    TautulliAPIError,
    TautulliHistoryItem,
    create_tautulli_client,
    map_users,
    build_user_map,
    fetch_tautulli_movie_history,
    fetch_tautulli_show_watched_data,
    merge_movie_history,
    merge_show_watched_data,
    TAUTULLI_RATE_LIMIT_DELAY,
)


# ---------------------------------------------------------------------------
# TautulliClient
# ---------------------------------------------------------------------------

class TestTautulliClientInit:
    """Tests for TautulliClient initialization."""

    def test_init_strips_trailing_slash(self):
        client = TautulliClient(url="http://localhost:8181/", api_key="key123")
        assert client.base_url == "http://localhost:8181"
        assert client.api_key == "key123"

    def test_init_sets_rate_limit_state(self):
        client = TautulliClient(url="http://localhost:8181", api_key="key123")
        assert client._last_request_time == 0
        assert client.rate_limit_delay == TAUTULLI_RATE_LIMIT_DELAY


class TestTautulliClientCall:
    """Tests for the low-level _call() request/response handling."""

    @patch('utils.api_client.requests.request')
    def test_call_success_unwraps_data(self, mock_request):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'response': {'result': 'success', 'message': None, 'data': [{'user_id': 1}]}
        }
        mock_request.return_value = mock_response

        client = TautulliClient("http://localhost:8181", "key123")
        data = client._call('get_users')

        assert data == [{'user_id': 1}]
        called_url = mock_request.call_args.kwargs['url']
        called_params = mock_request.call_args.kwargs['params']
        assert called_url == "http://localhost:8181/api/v2"
        assert called_params['apikey'] == "key123"
        assert called_params['cmd'] == 'get_users'

    @patch('utils.api_client.requests.request')
    def test_call_error_result_raises(self, mock_request):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'response': {'result': 'error', 'message': 'Invalid apikey', 'data': {}}
        }
        mock_request.return_value = mock_response

        client = TautulliClient("http://localhost:8181", "bad-key")
        with pytest.raises(TautulliAPIError, match="Invalid apikey"):
            client._call('get_users')

    @patch('utils.api_client.requests.request')
    def test_call_unexpected_shape_raises(self, mock_request):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"unexpected": True}
        mock_request.return_value = mock_response

        client = TautulliClient("http://localhost:8181", "key123")
        with pytest.raises(TautulliAPIError, match="Unexpected response shape"):
            client._call('get_users')

    @patch('utils.api_client.requests.request')
    def test_call_http_error_raises(self, mock_request):
        mock_response = Mock()
        mock_response.status_code = 401
        mock_request.return_value = mock_response

        client = TautulliClient("http://localhost:8181", "bad-key")
        with pytest.raises(TautulliAPIError):
            client._call('get_users')

    @patch('utils.api_client.requests.request')
    def test_call_timeout_raises_tautulli_error(self, mock_request):
        import requests
        mock_request.side_effect = requests.exceptions.Timeout()

        client = TautulliClient("http://localhost:8181", "key123")
        with pytest.raises(TautulliAPIError, match="timeout"):
            client._call('get_history', params={'user_id': 1})

    @patch('utils.api_client.requests.request')
    def test_call_connection_error_raises_tautulli_error(self, mock_request):
        import requests
        mock_request.side_effect = requests.exceptions.ConnectionError()

        client = TautulliClient("http://localhost:8181", "key123")
        with pytest.raises(TautulliAPIError, match="Could not connect"):
            client._call('get_users')


class TestTautulliClientGetUsers:
    """Tests for get_users()."""

    def test_get_users_returns_data(self):
        client = TautulliClient("http://localhost:8181", "key123")
        client._call = Mock(return_value=[{'user_id': 1, 'username': 'alice'}])

        result = client.get_users()

        assert result == [{'user_id': 1, 'username': 'alice'}]
        client._call.assert_called_once_with('get_users')

    def test_get_users_returns_empty_list_when_none(self):
        client = TautulliClient("http://localhost:8181", "key123")
        client._call = Mock(return_value=None)

        assert client.get_users() == []


class TestTautulliClientGetHistory:
    """Tests for get_history()."""

    def test_get_history_unwraps_datatables_envelope(self):
        client = TautulliClient("http://localhost:8181", "key123")
        client._call = Mock(return_value={'recordsTotal': 5, 'data': [{'rating_key': 100}]})

        result = client.get_history(user_id=42)

        assert result == [{'rating_key': 100}]
        _, kwargs = client._call.call_args
        assert kwargs['params'] == {'user_id': 42, 'length': 5000}

    def test_get_history_handles_plain_list(self):
        client = TautulliClient("http://localhost:8181", "key123")
        client._call = Mock(return_value=[{'rating_key': 1}])

        assert client.get_history(user_id=42) == [{'rating_key': 1}]

    def test_get_history_returns_empty_when_no_data(self):
        client = TautulliClient("http://localhost:8181", "key123")
        client._call = Mock(return_value={'data': None})

        assert client.get_history(user_id=42) == []

    def test_get_history_custom_length(self):
        client = TautulliClient("http://localhost:8181", "key123")
        client._call = Mock(return_value=[])

        client.get_history(user_id=42, length=50)

        _, kwargs = client._call.call_args
        assert kwargs['params']['length'] == 50


# ---------------------------------------------------------------------------
# create_tautulli_client
# ---------------------------------------------------------------------------

class TestCreateTautulliClient:
    """Tests for create_tautulli_client()."""

    def test_disabled_returns_none(self):
        config = {'tautulli': {'enabled': False, 'url': 'http://x:8181', 'api_key': 'realkey'}}
        assert create_tautulli_client(config) is None

    def test_missing_section_returns_none(self):
        assert create_tautulli_client({}) is None

    def test_enabled_missing_url_returns_none(self):
        config = {'tautulli': {'enabled': True, 'api_key': 'realkey'}}
        assert create_tautulli_client(config) is None

    def test_enabled_placeholder_key_returns_none(self):
        config = {'tautulli': {'enabled': True, 'url': 'http://x:8181', 'api_key': 'YOUR_TAUTULLI_API_KEY'}}
        assert create_tautulli_client(config) is None

    def test_enabled_valid_returns_client(self):
        config = {'tautulli': {'enabled': True, 'url': 'http://x:8181', 'api_key': 'realkey'}}
        client = create_tautulli_client(config)
        assert isinstance(client, TautulliClient)
        assert client.base_url == 'http://x:8181'
        assert client.api_key == 'realkey'


# ---------------------------------------------------------------------------
# User mapping
# ---------------------------------------------------------------------------

class TestMapUsers:
    """Tests for map_users() - Plex <-> Tautulli identity matching."""

    def test_matches_by_email(self):
        plex_identities = [{'id': '1', 'username': 'jsmith', 'email': 'Jason@Example.com'}]
        tautulli_users = [{'user_id': 501, 'username': 'jason_t', 'friendly_name': 'Jason T', 'email': 'jason@example.com'}]

        result = map_users(plex_identities, tautulli_users)

        assert result == {'1': '501'}

    def test_falls_back_to_username_when_no_email_match(self):
        plex_identities = [{'id': '2', 'username': 'ericarutyunov', 'email': None}]
        tautulli_users = [{'user_id': 502, 'username': 'ericarutyunov', 'email': 'someone-else@example.com'}]

        result = map_users(plex_identities, tautulli_users)

        assert result == {'2': '502'}

    def test_falls_back_to_friendly_name(self):
        plex_identities = [{'id': '3', 'username': 'homehouse165', 'email': None}]
        tautulli_users = [{'user_id': 503, 'username': 'random_login', 'friendly_name': 'homehouse165', 'email': None}]

        result = map_users(plex_identities, tautulli_users)

        assert result == {'3': '503'}

    def test_unmapped_user_excluded_from_result(self):
        plex_identities = [{'id': '4', 'username': 'ghost', 'email': 'ghost@example.com'}]
        tautulli_users = [{'user_id': 504, 'username': 'someone_else', 'email': 'other@example.com'}]

        result = map_users(plex_identities, tautulli_users)

        assert result == {}

    def test_empty_inputs_return_empty_map(self):
        assert map_users([], []) == {}
        assert map_users([{'id': '1', 'username': 'a', 'email': None}], []) == {}

    def test_skips_tautulli_users_without_user_id(self):
        plex_identities = [{'id': '1', 'username': 'a', 'email': 'a@example.com'}]
        tautulli_users = [{'username': 'a', 'email': 'a@example.com'}]  # missing user_id

        assert map_users(plex_identities, tautulli_users) == {}

    def test_multiple_users_mixed_match_strategies(self):
        plex_identities = [
            {'id': '1', 'username': 'alice', 'email': 'alice@example.com'},
            {'id': '2', 'username': 'bob', 'email': None},
            {'id': '3', 'username': 'nomatch', 'email': 'nomatch@example.com'},
        ]
        tautulli_users = [
            {'user_id': 10, 'username': 'alice_t', 'email': 'alice@example.com'},
            {'user_id': 20, 'username': 'bob', 'email': None},
        ]

        result = map_users(plex_identities, tautulli_users)

        assert result == {'1': '10', '2': '20'}


class TestBuildUserMap:
    """Tests for build_user_map() - orchestrates client + plexapi lookups."""

    @patch('plexapi.myplex.MyPlexAccount')
    def test_builds_map_from_client_and_plex_account(self, mock_account_cls):
        mock_account = Mock()
        mock_account.id = 245355570  # global plex.tv account id (owner)
        mock_account.username = 'owner'
        mock_account.email = 'owner@example.com'

        mock_user = Mock()
        mock_user.id = 2
        mock_user.title = 'friend'
        mock_user.email = 'friend@example.com'
        mock_account.users.return_value = [mock_user]
        mock_account_cls.return_value = mock_account

        client = Mock()
        client.get_users.return_value = [
            {'user_id': 100, 'username': 'owner_t', 'email': 'owner@example.com'},
            {'user_id': 200, 'username': 'friend_t', 'email': 'friend@example.com'},
        ]

        config = {'plex': {'token': 'tok'}}
        result = build_user_map(client, config)

        # Owner must be keyed by the local '1' convention (matches
        # get_plex_account_ids()/fetch_plex_watch_history_movies()'s
        # accountID for the owner), NOT their global plex.tv account.id.
        assert result == {'1': '100', '2': '200'}

    @patch('plexapi.myplex.MyPlexAccount')
    def test_owner_keyed_by_local_id_not_global_id(self, mock_account_cls):
        """Regression test: owner's global account.id must never leak into the map key."""
        mock_account = Mock()
        mock_account.id = 245355570
        mock_account.username = 'owner'
        mock_account.email = 'owner@example.com'
        mock_account.users.return_value = []
        mock_account_cls.return_value = mock_account

        client = Mock()
        client.get_users.return_value = [
            {'user_id': 245355570, 'username': 'owner', 'email': 'owner@example.com'},
        ]

        result = build_user_map(client, {'plex': {'token': 'tok'}})

        assert '1' in result
        assert '245355570' not in result
        assert result['1'] == '245355570'

    def test_get_users_error_returns_empty_map(self):
        client = Mock()
        client.get_users.side_effect = TautulliAPIError("boom")

        result = build_user_map(client, {'plex': {'token': 'tok'}})

        assert result == {}

    def test_no_tautulli_users_returns_empty_map(self):
        client = Mock()
        client.get_users.return_value = []

        result = build_user_map(client, {'plex': {'token': 'tok'}})

        assert result == {}

    @patch('plexapi.myplex.MyPlexAccount')
    def test_plex_account_error_returns_empty_map(self, mock_account_cls):
        mock_account_cls.side_effect = Exception("Plex unreachable")

        client = Mock()
        client.get_users.return_value = [{'user_id': 100, 'username': 'x', 'email': 'x@example.com'}]

        result = build_user_map(client, {'plex': {'token': 'tok'}})

        assert result == {}


# ---------------------------------------------------------------------------
# fetch_tautulli_movie_history
# ---------------------------------------------------------------------------

class TestFetchTautulliMovieHistory:
    """Tests for fetch_tautulli_movie_history() - and Plex-only fallback safety."""

    def test_no_client_returns_empty_list(self):
        # tautulli disabled/misconfigured -> create_tautulli_client() returns None internally
        result = fetch_tautulli_movie_history({}, ['1'])
        assert result == []

    def test_empty_user_map_returns_empty_list(self):
        client = Mock()
        result = fetch_tautulli_movie_history({}, ['1'], client=client, user_map={})
        assert result == []
        client.get_history.assert_not_called()

    def test_unmapped_account_id_skipped(self):
        client = Mock()
        result = fetch_tautulli_movie_history({}, ['999'], client=client, user_map={'1': '100'})
        assert result == []
        client.get_history.assert_not_called()

    def test_filters_to_movies_and_watched(self):
        client = Mock()
        client.get_history.return_value = [
            {'rating_key': 1, 'media_type': 'movie', 'watched_status': 1, 'stopped': 1700000000},
            {'rating_key': 2, 'media_type': 'episode', 'watched_status': 1, 'stopped': 1700000000},
            {'rating_key': 3, 'media_type': 'movie', 'watched_status': 0, 'stopped': 1700000000},
            {'rating_key': None, 'media_type': 'movie', 'watched_status': 1, 'stopped': 1700000000},
        ]

        result = fetch_tautulli_movie_history({}, ['1'], client=client, user_map={'1': '100'})

        assert len(result) == 1
        assert result[0].ratingKey == '1'
        assert isinstance(result[0].viewedAt, datetime)
        assert result[0].userRating is None

    def test_uses_date_fallback_when_stopped_missing(self):
        client = Mock()
        client.get_history.return_value = [
            {'rating_key': 5, 'media_type': 'movie', 'watched_status': 1, 'date': 1700000000},
        ]

        result = fetch_tautulli_movie_history({}, ['1'], client=client, user_map={'1': '100'})

        assert result[0].viewedAt == datetime.fromtimestamp(1700000000)

    def test_history_fetch_error_for_one_user_does_not_stop_others(self):
        client = Mock()

        def side_effect(user_id, **kwargs):
            if user_id == '100':
                raise TautulliAPIError("timeout")
            return [{'rating_key': 9, 'media_type': 'movie', 'watched_status': 1, 'stopped': 1700000000}]

        client.get_history.side_effect = side_effect

        result = fetch_tautulli_movie_history(
            {}, ['1', '2'], client=client, user_map={'1': '100', '2': '200'}
        )

        assert len(result) == 1
        assert result[0].ratingKey == '9'


# ---------------------------------------------------------------------------
# fetch_tautulli_show_watched_data
# ---------------------------------------------------------------------------

class TestFetchTautulliShowWatchedData:
    """Tests for fetch_tautulli_show_watched_data()."""

    def test_no_client_returns_empty(self):
        ids, timestamps = fetch_tautulli_show_watched_data({}, ['1'])
        assert ids == set()
        assert timestamps == {}

    def test_empty_user_map_returns_empty(self):
        client = Mock()
        ids, timestamps = fetch_tautulli_show_watched_data({}, ['1'], client=client, user_map={})
        assert ids == set()
        assert timestamps == {}
        client.get_history.assert_not_called()

    def test_filters_to_episodes_and_watched_builds_show_ids(self):
        client = Mock()
        client.get_history.return_value = [
            {'grandparent_rating_key': 55, 'media_type': 'episode', 'watched_status': 1, 'stopped': 1700000000},
            {'grandparent_rating_key': 55, 'media_type': 'episode', 'watched_status': 1, 'stopped': 1700003600},
            {'grandparent_rating_key': 60, 'media_type': 'movie', 'watched_status': 1, 'stopped': 1700000000},
            {'grandparent_rating_key': 70, 'media_type': 'episode', 'watched_status': 0, 'stopped': 1700000000},
        ]

        ids, timestamps = fetch_tautulli_show_watched_data({}, ['1'], client=client, user_map={'1': '100'})

        assert ids == {55}
        assert timestamps == {55: 1700003600}  # keeps latest timestamp

    def test_missing_grandparent_rating_key_skipped(self):
        client = Mock()
        client.get_history.return_value = [
            {'grandparent_rating_key': None, 'media_type': 'episode', 'watched_status': 1, 'stopped': 1700000000},
        ]

        ids, timestamps = fetch_tautulli_show_watched_data({}, ['1'], client=client, user_map={'1': '100'})

        assert ids == set()
        assert timestamps == {}


# ---------------------------------------------------------------------------
# merge_movie_history
# ---------------------------------------------------------------------------

class TestMergeMovieHistory:
    """Tests for merge_movie_history() - dedupe + weighting-compatible output."""

    def test_no_overlap_simple_union(self):
        plex_item = TautulliHistoryItem('1', datetime(2026, 1, 1), 8.0)
        tautulli_item = TautulliHistoryItem('2', datetime(2026, 2, 1), None)

        result = merge_movie_history([plex_item], [tautulli_item])

        keys = {item.ratingKey for item in result}
        assert keys == {'1', '2'}

    def test_dedupes_by_rating_key(self):
        plex_item = TautulliHistoryItem('1', datetime(2026, 1, 1), None)
        tautulli_item = TautulliHistoryItem('1', datetime(2026, 2, 1), None)

        result = merge_movie_history([plex_item], [tautulli_item])

        assert len(result) == 1

    def test_plex_rating_wins_over_tautulli(self):
        plex_item = TautulliHistoryItem('1', datetime(2026, 1, 1), 9.0)
        tautulli_item = TautulliHistoryItem('1', datetime(2026, 2, 1), None)

        result = merge_movie_history([plex_item], [tautulli_item])

        assert result[0].userRating == 9.0

    def test_keeps_most_recent_viewed_at(self):
        plex_item = TautulliHistoryItem('1', datetime(2026, 1, 1), None)
        tautulli_item = TautulliHistoryItem('1', datetime(2026, 6, 1), None)

        result = merge_movie_history([plex_item], [tautulli_item])

        assert result[0].viewedAt == datetime(2026, 6, 1)

    def test_empty_plex_falls_back_to_tautulli_only(self):
        tautulli_item = TautulliHistoryItem('1', datetime(2026, 2, 1), None)

        result = merge_movie_history([], [tautulli_item])

        assert len(result) == 1
        assert result[0].ratingKey == '1'

    def test_empty_tautulli_returns_plex_only(self):
        plex_item = TautulliHistoryItem('1', datetime(2026, 1, 1), 7.0)

        result = merge_movie_history([plex_item], [])

        assert len(result) == 1
        assert result[0].userRating == 7.0

    def test_both_empty_returns_empty(self):
        assert merge_movie_history([], []) == []


# ---------------------------------------------------------------------------
# merge_show_watched_data
# ---------------------------------------------------------------------------

class TestMergeShowWatchedData:
    """Tests for merge_show_watched_data() - dedupe + weighting-compatible output."""

    def test_union_of_ids(self):
        ids, timestamps = merge_show_watched_data({1, 2}, {}, {2, 3}, {})
        assert ids == {1, 2, 3}

    def test_keeps_max_timestamp_per_show(self):
        ids, timestamps = merge_show_watched_data(
            {1}, {1: 1000}, {1}, {1: 2000}
        )
        assert timestamps == {1: 2000}

    def test_plex_timestamp_kept_when_newer(self):
        ids, timestamps = merge_show_watched_data(
            {1}, {1: 5000}, {1}, {1: 2000}
        )
        assert timestamps == {1: 5000}

    def test_tautulli_only_show_included(self):
        ids, timestamps = merge_show_watched_data(set(), {}, {9}, {9: 1234})
        assert ids == {9}
        assert timestamps == {9: 1234}

    def test_empty_tautulli_returns_plex_only(self):
        ids, timestamps = merge_show_watched_data({1, 2}, {1: 100}, set(), {})
        assert ids == {1, 2}
        assert timestamps == {1: 100}

    def test_both_empty_returns_empty(self):
        ids, timestamps = merge_show_watched_data(set(), {}, set(), {})
        assert ids == set()
        assert timestamps == {}


# ---------------------------------------------------------------------------
# Disabled / unreachable / unmapped -> Plex-only fallback (no regression)
# ---------------------------------------------------------------------------

class TestPlexOnlyFallback:
    """End-to-end-ish tests confirming safe fallback behavior."""

    def test_disabled_config_never_calls_tautulli(self):
        config = {'tautulli': {'enabled': False, 'url': 'http://x:8181', 'api_key': 'realkey'}}

        movie_items = fetch_tautulli_movie_history(config, ['1'])
        show_ids, show_timestamps = fetch_tautulli_show_watched_data(config, ['1'])

        assert movie_items == []
        assert show_ids == set()
        assert show_timestamps == {}

    def test_missing_tautulli_config_never_calls_tautulli(self):
        assert fetch_tautulli_movie_history({}, ['1']) == []
        ids, timestamps = fetch_tautulli_show_watched_data({}, ['1'])
        assert ids == set()
        assert timestamps == {}

    @patch('utils.tautulli.create_tautulli_client')
    def test_unreachable_tautulli_falls_back_cleanly(self, mock_create_client):
        client = Mock()
        client.get_users.side_effect = TautulliAPIError("Could not connect to Tautulli")
        mock_create_client.return_value = client

        config = {'tautulli': {'enabled': True, 'url': 'http://x:8181', 'api_key': 'realkey'}, 'plex': {'token': 'tok'}}

        result = fetch_tautulli_movie_history(config, ['1'])

        assert result == []
