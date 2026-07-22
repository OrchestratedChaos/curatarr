"""Tests for web/app.py - Flask routes for the dashboard, run, and
results screens, plus the localhost-only binding guardrail.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from web.app import create_app, _wait_for_listening


@pytest.fixture
def client(curatarr_web_root):
    app = create_app(project_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


def _wait_until_idle(app, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not app.job_manager.is_running():
            return
        time.sleep(0.05)
    raise AssertionError('job did not finish in time')


class TestDashboard:
    """Tests for GET /"""

    def test_renders_users_from_config(self, client):
        c, app, root = client
        resp = c.get('/')
        assert resp.status_code == 200
        assert b'alice' in resp.data
        assert b'bob' in resp.data

    def test_shows_never_run_when_no_logs(self, client):
        c, app, root = client
        resp = c.get('/')
        assert b'never_run' in resp.data

    def test_shows_success_status_from_log(self, client):
        c, app, root = client
        log_path = os.path.join(root, 'logs', 'recommendations_alice_20260101_030000.log')
        with open(log_path, 'w') as f:
            f.write('Processing alice\nDone\n')
        resp = c.get('/')
        assert b'success' in resp.data

    def test_handles_missing_config_gracefully(self, tmp_path):
        (tmp_path / 'logs').mkdir()
        (tmp_path / 'recommendations' / 'external').mkdir(parents=True)
        app = create_app(project_root=str(tmp_path))
        app.testing = True
        resp = app.test_client().get('/')
        assert resp.status_code == 200
        assert b'No users configured' in resp.data


class TestRunPage:
    """Tests for GET/POST /run and /run/stream, /run/status"""

    def test_get_run_form(self, client):
        c, app, root = client
        resp = c.get('/run')
        assert resp.status_code == 200
        assert b'alice' in resp.data
        assert b'bob' in resp.data

    def test_post_run_triggers_job_and_redirects(self, client):
        c, app, root = client
        resp = c.post('/run', data={'engine': 'external', 'user': 'all'})
        assert resp.status_code == 303
        assert app.job_manager.current_job() is not None
        _wait_until_idle(app)

    def test_post_run_rejects_concurrent_run(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setenv('CURATARR_TEST_SLOW', '1')
        resp1 = c.post('/run', data={'engine': 'movie', 'user': 'alice'})
        assert resp1.status_code == 303
        resp2 = c.post('/run', data={'engine': 'movie', 'user': 'bob'})
        assert resp2.status_code == 303
        assert 'error=busy' in resp2.headers['Location']
        _wait_until_idle(app)

    def test_post_run_rejects_unknown_user(self, client):
        c, app, root = client
        resp = c.post('/run', data={'engine': 'movie', 'user': 'mallory'})
        assert resp.status_code == 303
        assert 'error=' in resp.headers['Location']

    def test_run_stream_before_any_job_404s(self, client):
        c, app, root = client
        resp = c.get('/run/stream')
        assert resp.status_code == 404

    def test_run_stream_after_job_streams_events(self, client):
        c, app, root = client
        c.post('/run', data={'engine': 'external', 'user': 'all'})
        _wait_until_idle(app)
        resp = c.get('/run/stream')
        assert resp.status_code == 200
        assert resp.mimetype == 'text/event-stream'
        body = resp.get_data(as_text=True)
        assert 'event: done' in body

    def test_run_status_json_idle(self, client):
        c, app, root = client
        resp = c.get('/run/status')
        assert resp.status_code == 200
        assert resp.get_json() == {'state': 'idle'}

    def test_run_status_json_after_run(self, client):
        c, app, root = client
        c.post('/run', data={'engine': 'external', 'user': 'all'})
        _wait_until_idle(app)
        resp = c.get('/run/status')
        assert resp.get_json()['state'] == 'succeeded'


class TestResults:
    """Tests for GET /results, /results/watchlist/<file>, /results/log/<file>"""

    def test_lists_watchlists_and_logs(self, client):
        c, app, root = client
        ext_dir = os.path.join(root, 'recommendations', 'external')
        with open(os.path.join(ext_dir, 'watchlist.html'), 'w') as f:
            f.write('<html>hi</html>')
        with open(os.path.join(root, 'logs', 'daily-run.log'), 'w') as f:
            f.write('cron output\n')
        resp = c.get('/results')
        assert resp.status_code == 200
        assert b'watchlist.html' in resp.data
        assert b'daily-run.log' in resp.data

    def test_no_watchlists_or_logs_yet(self, client):
        c, app, root = client
        resp = c.get('/results')
        assert resp.status_code == 200
        assert b'No watchlists generated yet' in resp.data
        assert b'No logs yet' in resp.data

    def test_serves_watchlist_file(self, client):
        c, app, root = client
        ext_dir = os.path.join(root, 'recommendations', 'external')
        with open(os.path.join(ext_dir, 'watchlist.html'), 'w') as f:
            f.write('<html>hello world</html>')
        resp = c.get('/results/watchlist/watchlist.html')
        assert resp.status_code == 200
        assert b'hello world' in resp.data

    def test_watchlist_rejects_non_html_md_extension(self, client):
        c, app, root = client
        resp = c.get('/results/watchlist/evil.txt')
        assert resp.status_code == 404

    def test_watchlist_rejects_traversal(self, client):
        c, app, root = client
        # A secret one directory above recommendations/external/, that a
        # traversal attempt with an allow-listed extension might target.
        with open(os.path.join(root, 'secret.html'), 'w') as f:
            f.write('TOP SECRET MARKER')
        resp = c.get('/results/watchlist/..%2Fsecret.html')
        assert resp.status_code == 404
        assert b'TOP SECRET MARKER' not in resp.data

    def test_views_log_tail(self, client):
        c, app, root = client
        with open(os.path.join(root, 'logs', 'a.log'), 'w') as f:
            f.write('line one\ntoken=abcdef123456\n')
        resp = c.get('/results/log/a.log')
        assert resp.status_code == 200
        assert b'line one' in resp.data
        assert b'abcdef123456' not in resp.data

    def test_log_view_missing_file_404s(self, client):
        c, app, root = client
        resp = c.get('/results/log/missing.log')
        assert resp.status_code == 404

    def test_log_view_rejects_traversal(self, client):
        c, app, root = client
        resp = c.get('/results/log/..%2Fconfig%2Fconfig.yml')
        assert resp.status_code == 404


class TestWaitForListening:
    """Tests for the launcher's "server is actually listening" poll."""

    def test_returns_true_when_port_open(self):
        import socket

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            assert _wait_for_listening(port, timeout=2) is True
        finally:
            server.close()

    def test_returns_false_when_nothing_listening(self):
        import socket

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()

        assert _wait_for_listening(port, timeout=0.3) is False


class TestBindingGuardrail:
    """Guardrail: the app must only ever bind 127.0.0.1, never 0.0.0.0."""

    def test_binds_localhost_only(self):
        import inspect
        import re

        import web.app as app_module

        source = inspect.getsource(app_module)
        assert "host='127.0.0.1'" in source
        # Only the redaction/no-wildcard-bind explanation may mention
        # 0.0.0.0 in prose; app.run() itself must never pass it as host=.
        assert not re.search(r'host\s*=\s*[\'"]0\.0\.0\.0[\'"]', source)
