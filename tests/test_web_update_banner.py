"""Tests for the dismissible update-available banner (web/app.py's
_update_banner_context context processor + /update/dismiss route) and
its gating by general.update_mode.

The version-check itself (utils/update_check.py) is unit-tested
separately in tests/test_update_check.py - these tests mock
web.app.update_available so no test here ever touches the network.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from web.app import create_app
from web.config_io import module_path


@pytest.fixture
def client(curatarr_web_root):
    app = create_app(project_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


def _write_config(root, update_mode=None):
    config_path = module_path(root, 'config')
    general = f'general:\n  update_mode: {update_mode}\n' if update_mode else ''
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(
            'plex:\n  url: "http://localhost:32400"\n'
            'users:\n  list: "alice, bob"\n'
            f'{general}'
        )


class TestBannerGating:
    def test_hidden_when_update_mode_off(self, client):
        c, app, root = client
        _write_config(root, update_mode='off')

        with patch('web.app.update_available') as mock_update_available:
            resp = c.get('/')
            mock_update_available.assert_not_called()

        assert b'update-banner' not in resp.data

    def test_hidden_when_no_newer_version(self, client):
        c, app, root = client
        _write_config(root, update_mode='notify')

        with patch('web.app.update_available', return_value=('2.8.28', '2.8.28', False)):
            resp = c.get('/')

        assert b'update-banner' not in resp.data

    def test_shown_when_newer_version_available(self, client):
        c, app, root = client
        _write_config(root, update_mode='notify')

        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')

        assert b'update-banner' in resp.data
        assert b'v2.9.0' in resp.data
        assert b'v2.8.28' in resp.data

    def test_shown_in_force_mode_too(self, client):
        """force mode still shows the banner - it's the source install's
        run.sh/run.ps1 (not the web UI) that auto-applies in force mode."""
        c, app, root = client
        _write_config(root, update_mode='force')

        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')

        assert b'update-banner' in resp.data

    def test_banner_renders_on_config_screens_too(self, client):
        """The context processor is registered on the shared app, so it
        must cover config_app.py's routes as well, not just the
        dashboard/run/results routes defined directly in web/app.py."""
        c, app, root = client
        _write_config(root, update_mode='notify')

        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/config/settings')

        assert b'update-banner' in resp.data

    def test_broken_config_fails_open_no_banner_no_500(self, client):
        c, app, root = client
        config_path = module_path(root, 'config')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write('not: [valid, yaml: structure\n')

        with patch('web.app.update_available', side_effect=Exception('should not be reached')) as mock_update_available:
            resp = c.get('/')
            mock_update_available.assert_not_called()

        assert resp.status_code == 200
        assert b'update-banner' not in resp.data

    def test_update_available_raising_fails_open_no_500(self, client):
        """Belt-and-suspenders: even if update_available() itself somehow
        raised (it's fail-open internally and shouldn't), the context
        processor's own try/except must still turn that into "no
        banner", never a 500."""
        c, app, root = client
        _write_config(root, update_mode='notify')

        with patch('web.app.update_available', side_effect=RuntimeError('unexpected')):
            resp = c.get('/')

        assert resp.status_code == 200
        assert b'update-banner' not in resp.data


class TestBannerContent:
    def test_frozen_binary_shows_update_now_button(self, client, monkeypatch):
        """As of v2.8.29, frozen binaries get the same one-click
        "Update now" button as source installs (in-binary self-update -
        see utils/self_update.py) - see
        tests/test_web_update_apply.py::TestFrozenAndSourceBothGetTheButton
        for the /update/apply route-level assertions."""
        c, app, root = client
        _write_config(root, update_mode='notify')
        monkeypatch.setattr(sys, 'frozen', True, raising=False)

        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')

        assert b'update-now-btn' in resp.data

    def test_frozen_binary_mentions_verification(self, client, monkeypatch):
        c, app, root = client
        _write_config(root, update_mode='notify')
        monkeypatch.setattr(sys, 'frozen', True, raising=False)

        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')

        assert b'verified' in resp.data.lower()

    def test_source_install_shows_update_now_button(self, client, monkeypatch):
        c, app, root = client
        _write_config(root, update_mode='notify')
        monkeypatch.setattr(sys, 'frozen', False, raising=False)

        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')

        assert b'update-now-btn' in resp.data

    def test_docker_hides_update_now_button_and_points_at_docker_pull(self, client, monkeypatch):
        """RUNNING_IN_DOCKER=true (set by the Dockerfile) still shows the
        banner - there IS a newer version - but never a button that
        would just fail (see web/update_apply.py's UpdateManager.
        begin_update RUNNING_IN_DOCKER gate); instead it tells the user
        to `docker pull`."""
        c, app, root = client
        _write_config(root, update_mode='notify')
        monkeypatch.setenv('RUNNING_IN_DOCKER', 'true')
        monkeypatch.setattr(sys, 'frozen', False, raising=False)

        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')

        assert b'update-banner' in resp.data
        # The <button> element itself must be gone - not just checking
        # for the bare id substring, which also appears (harmlessly,
        # guarded by `if (!btn) { return; }`) inside the banner's own
        # always-rendered <script> block.
        assert b'id="update-now-btn"' not in resp.data
        assert b'docker pull' in resp.data

    def test_non_docker_unaffected_by_running_in_docker_unset(self, client, monkeypatch):
        c, app, root = client
        _write_config(root, update_mode='notify')
        monkeypatch.delenv('RUNNING_IN_DOCKER', raising=False)
        monkeypatch.setattr(sys, 'frozen', False, raising=False)

        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')

        assert b'id="update-now-btn"' in resp.data
        assert b'docker pull' not in resp.data


class TestDismiss:
    def test_dismiss_sets_cookie_and_redirects(self, client):
        c, app, root = client
        _write_config(root, update_mode='notify')

        resp = c.post('/update/dismiss', data={'version': '2.9.0', 'next': '/'})

        assert resp.status_code == 303
        set_cookie = resp.headers.get('Set-Cookie', '')
        assert 'curatarr_update_dismissed=2.9.0' in set_cookie

    def test_dismiss_redirects_to_next(self, client):
        c, app, root = client
        _write_config(root, update_mode='notify')

        resp = c.post('/update/dismiss', data={'version': '2.9.0', 'next': '/results'})

        assert resp.headers['Location'].endswith('/results')

    def test_dismiss_rejects_external_next_url(self, client):
        """'next' must never turn this into an open redirect."""
        c, app, root = client
        _write_config(root, update_mode='notify')

        resp = c.post('/update/dismiss', data={'version': '2.9.0', 'next': 'http://evil.example.com'})

        assert 'evil.example.com' not in resp.headers['Location']

    def test_dismissed_version_suppresses_banner(self, client):
        c, app, root = client
        _write_config(root, update_mode='notify')
        c.set_cookie('curatarr_update_dismissed', '2.9.0')

        with patch('web.app.update_available', return_value=('2.9.0', '2.8.28', True)):
            resp = c.get('/')

        assert b'update-banner' not in resp.data

    def test_dismissing_older_version_does_not_suppress_a_newer_one(self, client):
        c, app, root = client
        _write_config(root, update_mode='notify')
        c.set_cookie('curatarr_update_dismissed', '2.9.0')

        with patch('web.app.update_available', return_value=('2.10.0', '2.8.28', True)):
            resp = c.get('/')

        assert b'update-banner' in resp.data
        assert b'v2.10.0' in resp.data
