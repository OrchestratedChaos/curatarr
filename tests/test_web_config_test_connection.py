"""Tests for web/config_test_connection.py - the Test Connection checks
behind the Connections screen's buttons. Every external client is
mocked here; nothing in this file makes a real network call."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web.config_test_connection as cc


class _FakeSections(list):
    pass


class _FakeServer:
    class library:
        @staticmethod
        def sections():
            return _FakeSections([object(), object()])


class TestPlex:
    def test_missing_fields_fail_fast(self):
        result = cc.test_plex('', '')
        assert result['ok'] is False

    def test_success(self, monkeypatch):
        monkeypatch.setattr(cc, 'init_plex', lambda config: _FakeServer())
        result = cc.test_plex('http://localhost:32400', 'tok123')
        assert result['ok'] is True
        assert '2' in result['message']

    def test_failure_raises_are_caught(self, monkeypatch):
        def _raise(config):
            raise ConnectionError('boom')
        monkeypatch.setattr(cc, 'init_plex', _raise)
        result = cc.test_plex('http://localhost:32400', 'tok123')
        assert result['ok'] is False
        assert 'boom' in result['message']


class TestTmdb:
    def test_missing_key_fails_fast(self):
        result = cc.test_tmdb('')
        assert result['ok'] is False

    def test_success(self, monkeypatch):
        monkeypatch.setattr(cc, 'fetch_tmdb_with_retry', lambda *a, **k: {'images': {}})
        result = cc.test_tmdb('a-real-key')
        assert result['ok'] is True

    def test_failure(self, monkeypatch):
        monkeypatch.setattr(cc, 'fetch_tmdb_with_retry', lambda *a, **k: None)
        result = cc.test_tmdb('bad-key')
        assert result['ok'] is False


class TestTautulli:
    def test_missing_fields_fail_fast(self):
        result = cc.test_tautulli('', '')
        assert result['ok'] is False

    def test_success(self, monkeypatch):
        class _FakeClient:
            def __init__(self, url, api_key):
                pass

            def get_users(self):
                return [{'user': 'a'}, {'user': 'b'}]

        monkeypatch.setattr(cc, 'TautulliClient', _FakeClient)
        result = cc.test_tautulli('http://localhost:8181', 'key')
        assert result['ok'] is True
        assert '2' in result['message']

    def test_api_error_caught(self, monkeypatch):
        class _FakeClient:
            def __init__(self, url, api_key):
                pass

            def get_users(self):
                raise cc.TautulliAPIError('unreachable')

        monkeypatch.setattr(cc, 'TautulliClient', _FakeClient)
        result = cc.test_tautulli('http://localhost:8181', 'key')
        assert result['ok'] is False
        assert 'unreachable' in result['message']


class TestSonarr:
    def test_missing_fields_fail_fast(self):
        result = cc.test_sonarr('', '')
        assert result['ok'] is False

    def test_success(self, monkeypatch):
        class _FakeClient:
            def __init__(self, url, api_key):
                pass

            def test_connection(self):
                return True

        monkeypatch.setattr(cc, 'SonarrClient', _FakeClient)
        result = cc.test_sonarr('http://localhost:8989', 'key')
        assert result['ok'] is True

    def test_api_error_caught(self, monkeypatch):
        class _FakeClient:
            def __init__(self, url, api_key):
                pass

            def test_connection(self):
                raise cc.SonarrAPIError('unauthorized')

        monkeypatch.setattr(cc, 'SonarrClient', _FakeClient)
        result = cc.test_sonarr('http://localhost:8989', 'bad-key')
        assert result['ok'] is False
        assert 'unauthorized' in result['message']


class TestRadarr:
    def test_missing_fields_fail_fast(self):
        result = cc.test_radarr('', '')
        assert result['ok'] is False

    def test_success(self, monkeypatch):
        class _FakeClient:
            def __init__(self, url, api_key):
                pass

            def test_connection(self):
                return True

        monkeypatch.setattr(cc, 'RadarrClient', _FakeClient)
        result = cc.test_radarr('http://localhost:7878', 'key')
        assert result['ok'] is True

    def test_api_error_caught(self, monkeypatch):
        class _FakeClient:
            def __init__(self, url, api_key):
                pass

            def test_connection(self):
                raise cc.RadarrAPIError('unauthorized')

        monkeypatch.setattr(cc, 'RadarrClient', _FakeClient)
        result = cc.test_radarr('http://localhost:7878', 'bad-key')
        assert result['ok'] is False


class TestTrakt:
    def test_missing_creds_fail_fast(self):
        result = cc.test_trakt('', '', '', '')
        assert result['ok'] is False

    def test_missing_access_token_prompts_auth_flow(self):
        result = cc.test_trakt('client-id', 'client-secret', '', '')
        assert result['ok'] is False
        assert 'trakt_auth' in result['message']

    def test_success(self, monkeypatch):
        class _FakeClient:
            def __init__(self, client_id, client_secret, access_token, refresh_token):
                pass

            def get_username(self):
                return 'jasonsmith523'

        monkeypatch.setattr(cc, 'TraktClient', _FakeClient)
        result = cc.test_trakt('cid', 'csecret', 'atoken', 'rtoken')
        assert result['ok'] is True
        assert 'jasonsmith523' in result['message']

    def test_expired_token_fails(self, monkeypatch):
        class _FakeClient:
            def __init__(self, client_id, client_secret, access_token, refresh_token):
                pass

            def get_username(self):
                return None

        monkeypatch.setattr(cc, 'TraktClient', _FakeClient)
        result = cc.test_trakt('cid', 'csecret', 'atoken', 'rtoken')
        assert result['ok'] is False

    def test_raw_client_exception_is_caught_not_a_500(self, monkeypatch):
        # Matches every other tester in this module: a raw requests/API
        # error must produce a normal {ok, message} failure, not an
        # unhandled exception with an unredacted traceback.
        class _FakeClient:
            def __init__(self, client_id, client_secret, access_token, refresh_token):
                pass

            def get_username(self):
                raise ConnectionError('boom: token=abcdef123456 leaked')

        monkeypatch.setattr(cc, 'TraktClient', _FakeClient)
        result = cc.test_trakt('cid', 'csecret', 'atoken', 'rtoken')
        assert result['ok'] is False
        assert 'boom' in result['message']
        assert 'abcdef123456' not in result['message']


class TestTestersRegistry:
    def test_all_services_registered(self):
        assert set(cc.TESTERS.keys()) == {'plex', 'tmdb', 'tautulli', 'sonarr', 'radarr', 'trakt'}

    def test_tester_dispatch_reads_expected_form_keys(self, monkeypatch):
        monkeypatch.setattr(cc, 'init_plex', lambda config: _FakeServer())
        result = cc.TESTERS['plex']({'url': 'http://localhost:32400', 'token': 'tok'})
        assert result['ok'] is True
