"""Tests for the /config/connections screen: form render, save
(correct modular YAML + round-trip), secret masking/blank-keeps-
existing, validation rejection without corrupting the file, and the
/config/test/<service> endpoints (clients mocked - no real network)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import yaml

from web.app import create_app
from web.config_io import load_module, module_path


@pytest.fixture
def client(curatarr_web_root):
    app = create_app(project_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


def _read_yaml(root, name):
    path = module_path(root, name)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


VALID_FORM = {
    'plex_url': 'http://localhost:32400',
    'plex_token': '',
    'tmdb_api_key': '',
    'tautulli_url': '',
    'tautulli_api_key': '',
    'sonarr_url': 'http://localhost:8989',
    'sonarr_api_key': 'sonarr-key-123',
    'sonarr_user_mode': 'mapping',
    'sonarr_plex_users': 'alice',
    'radarr_url': 'http://localhost:7878',
    'radarr_api_key': 'radarr-key-123',
    'radarr_user_mode': 'mapping',
    'radarr_plex_users': 'alice',
    'trakt_client_id': 'client-id-123',
    'trakt_client_secret': 'client-secret-123',
    'trakt_user_mode': 'mapping',
    'trakt_plex_users': 'alice',
}


class TestGet:
    def test_renders_form(self, client):
        c, app, root = client
        resp = c.get('/config/connections')
        assert resp.status_code == 200
        assert b'Setup / Connections' in resp.data

    def test_shows_masked_secret_status_not_raw_value(self, client):
        c, app, root = client
        resp = c.get('/config/connections')
        assert b'not-a-real-token' not in resp.data
        assert b'configured' in resp.data


class TestSave:
    def test_saves_plex_and_tmdb_to_config_yml(self, client):
        c, app, root = client
        resp = c.post('/config/connections', data=VALID_FORM)
        assert resp.status_code == 303

        core = _read_yaml(root, 'config')
        assert core['plex']['url'] == 'http://localhost:32400'

    def test_saves_sonarr_radarr_trakt_to_their_own_files(self, client):
        c, app, root = client
        c.post('/config/connections', data=VALID_FORM)

        sonarr = _read_yaml(root, 'sonarr')
        assert sonarr['url'] == 'http://localhost:8989'
        assert sonarr['api_key'] == 'sonarr-key-123'
        assert sonarr['plex_users'] == ['alice']

        radarr = _read_yaml(root, 'radarr')
        assert radarr['url'] == 'http://localhost:7878'

        trakt = _read_yaml(root, 'trakt')
        assert trakt['client_id'] == 'client-id-123'
        assert trakt['export']['plex_users'] == ['alice']

    def test_never_renders_secret_after_save(self, client):
        c, app, root = client
        c.post('/config/connections', data=VALID_FORM)
        resp = c.get('/config/connections')
        assert b'sonarr-key-123' not in resp.data
        assert b'radarr-key-123' not in resp.data
        assert b'client-secret-123' not in resp.data

    def test_blank_secret_on_resave_keeps_existing_value(self, client):
        c, app, root = client
        c.post('/config/connections', data=VALID_FORM)

        second = dict(VALID_FORM)
        second['sonarr_api_key'] = ''  # blank = keep existing
        second['sonarr_url'] = 'http://localhost:9999'  # change a non-secret field too
        c.post('/config/connections', data=second)

        sonarr = _read_yaml(root, 'sonarr')
        assert sonarr['api_key'] == 'sonarr-key-123'  # unchanged
        assert sonarr['url'] == 'http://localhost:9999'  # changed

    def test_nonblank_secret_overwrites(self, client):
        c, app, root = client
        c.post('/config/connections', data=VALID_FORM)

        second = dict(VALID_FORM)
        second['sonarr_api_key'] = 'brand-new-key'
        c.post('/config/connections', data=second)

        sonarr = _read_yaml(root, 'sonarr')
        assert sonarr['api_key'] == 'brand-new-key'

    def test_round_trip_preserves_untouched_yaml_comments_and_keys(self, client):
        c, app, root = client
        sonarr_path = module_path(root, 'sonarr')
        os.makedirs(os.path.dirname(sonarr_path), exist_ok=True)
        with open(sonarr_path, 'w', encoding='utf-8') as f:
            f.write(
                "# Curatarr Sonarr Configuration\n"
                "enabled: true\n"
                "url: http://localhost:8989\n"
                "api_key: old-key\n"
                "root_folder: /Volumes/TV\n"
                "quality_profile: HD-1080p\n"
            )

        c.post('/config/connections', data=VALID_FORM)

        content = open(sonarr_path, encoding='utf-8').read()
        assert '# Curatarr Sonarr Configuration' in content
        assert 'root_folder: /Volumes/TV' in content
        assert 'quality_profile: HD-1080p' in content


class TestValidation:
    def test_invalid_plex_url_rejected_with_400(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad['plex_url'] = 'not-a-url'
        resp = c.post('/config/connections', data=bad)
        assert resp.status_code == 400
        assert b'valid http' in resp.data or b'Must be a valid' in resp.data

    def test_invalid_input_does_not_corrupt_existing_file(self, client):
        c, app, root = client
        c.post('/config/connections', data=VALID_FORM)
        before = _read_yaml(root, 'config')

        bad = dict(VALID_FORM)
        bad['plex_url'] = 'not-a-url'
        c.post('/config/connections', data=bad)

        after = _read_yaml(root, 'config')
        assert after == before

    def test_invalid_user_mode_rejected(self, client):
        c, app, root = client
        bad = dict(VALID_FORM)
        bad['sonarr_user_mode'] = 'not-a-real-mode'
        resp = c.post('/config/connections', data=bad)
        assert resp.status_code == 400


class TestConnectionsTestEndpoint:
    def test_unknown_service_404s(self, client):
        c, app, root = client
        resp = c.post('/config/test/not-a-service')
        assert resp.status_code == 404

    def test_plex_test_success(self, client, monkeypatch):
        c, app, root = client

        class _FakeServer:
            class library:
                @staticmethod
                def sections():
                    return [object()]

        import web.config_test_connection as cc
        monkeypatch.setattr(cc, 'init_plex', lambda config: _FakeServer())

        resp = c.post('/config/test/plex', data={'url': 'http://localhost:32400', 'token': 'tok'})
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True

    def test_plex_test_failure_message_is_redacted(self, client, monkeypatch):
        c, app, root = client

        def _raise(config):
            raise ConnectionError('failed for X-Plex-Token=abcdef1234567890 on request')

        import web.config_test_connection as cc
        monkeypatch.setattr(cc, 'init_plex', _raise)

        resp = c.post('/config/test/plex', data={'url': 'http://localhost:32400', 'token': 'abcdef1234567890'})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['ok'] is False
        assert 'abcdef1234567890' not in body['message']

    def test_sonarr_test_uses_saved_key_when_submission_blank(self, client, monkeypatch):
        c, app, root = client
        c.post('/config/connections', data=VALID_FORM)  # saves sonarr api_key

        captured = {}

        class _FakeClient:
            def __init__(self, url, api_key):
                captured['url'] = url
                captured['api_key'] = api_key

            def test_connection(self):
                return True

        import web.config_test_connection as cc
        monkeypatch.setattr(cc, 'SonarrClient', _FakeClient)

        resp = c.post('/config/test/sonarr', data={'url': 'http://localhost:8989', 'api_key': ''})
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True
        assert captured['api_key'] == 'sonarr-key-123'

    def test_trakt_test_reports_missing_auth(self, client):
        c, app, root = client
        resp = c.post('/config/test/trakt', data={'client_id': 'cid', 'client_secret': 'csecret'})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['ok'] is False
        assert 'trakt_auth' in body['message']

    def test_sonarr_test_with_different_url_does_not_leak_saved_key(self, client, monkeypatch):
        """HIGH #1: submitting a blank secret alongside a URL that
        differs from the saved one must never cause the stored secret to
        be sent to that (possibly attacker-controlled) URL."""
        c, app, root = client
        c.post('/config/connections', data=VALID_FORM)  # saves sonarr api_key = sonarr-key-123

        captured = {}

        class _FakeClient:
            def __init__(self, url, api_key):
                captured['url'] = url
                captured['api_key'] = api_key

            def test_connection(self):
                return True

        import web.config_test_connection as cc
        monkeypatch.setattr(cc, 'SonarrClient', _FakeClient)

        resp = c.post(
            '/config/test/sonarr',
            data={'url': 'http://attacker.example.com', 'api_key': ''},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        # test_sonarr() fails fast on a blank api_key before ever
        # constructing SonarrClient - the saved key was never merged in
        # (and never even reaches the network).
        assert body['ok'] is False
        assert captured == {}

    def test_plex_test_with_same_saved_url_still_uses_saved_token(self, client, monkeypatch):
        """The blank-secret convenience still works for the normal case:
        testing the URL that's already saved."""
        c, app, root = client
        form = dict(VALID_FORM)
        form['plex_token'] = 'plex-token-123'
        c.post('/config/connections', data=form)

        captured = {}

        class _FakeServer:
            class library:
                @staticmethod
                def sections():
                    captured['called'] = True
                    return [object()]

        import web.config_test_connection as cc

        def _fake_init_plex(config):
            captured['token'] = config['plex']['token']
            return _FakeServer()

        monkeypatch.setattr(cc, 'init_plex', _fake_init_plex)

        resp = c.post('/config/test/plex', data={'url': 'http://localhost:32400', 'token': ''})
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True
        assert captured['token'] == 'plex-token-123'

    def test_null_plex_section_in_hand_edited_yaml_does_not_500(self, client):
        """M2: a bare `plex:` line (parses to None, not {}) must not 500
        or half-save the connections form."""
        c, app, root = client
        config_path = module_path(root, 'config')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write('plex:\nusers:\n  list: "alice, bob"\n')

        resp = c.post('/config/connections', data=VALID_FORM)
        assert resp.status_code == 303

        core = _read_yaml(root, 'config')
        assert core['plex']['url'] == 'http://localhost:32400'
        sonarr = _read_yaml(root, 'sonarr')
        assert sonarr['url'] == 'http://localhost:8989'

    def test_merge_validation_failure_prevents_write_and_returns_500(self, client, monkeypatch):
        """M4: a merge that fails the pre-write dry-run must not write
        ANY of the module files for this save."""
        c, app, root = client
        import web.config_app as config_app_mod
        monkeypatch.setattr(
            config_app_mod, 'validate_merge',
            lambda project_root, modules: 'simulated merge failure',
        )

        resp = c.post('/config/connections', data=VALID_FORM)
        assert resp.status_code == 500

        # sonarr.yml didn't exist before this save - if _commit_modules
        # correctly gates every write on the dry-run passing, it's still
        # absent (empty) after a failed one.
        sonarr = _read_yaml(root, 'sonarr')
        assert sonarr == {}
