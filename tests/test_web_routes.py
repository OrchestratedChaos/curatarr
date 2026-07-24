"""Tests for web/app.py - Flask routes for the dashboard, run, and
results screens, plus the localhost-only binding guardrail.
"""

import concurrent.futures
import os
import sys
import time
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.app as app_module
from web.app import BIND_RETRY_ATTEMPTS, create_app, _run_with_bind_retry, _wait_for_listening


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

    def test_links_to_per_user_watchlist_when_generated(self, client):
        c, app, root = client
        ext_dir = os.path.join(root, 'recommendations', 'external')
        with open(os.path.join(ext_dir, 'alice_a_watchlist.html'), 'w') as f:
            f.write('<html>alice list</html>')
        resp = c.get('/')
        assert resp.status_code == 200
        assert b'alice_a_watchlist.html' in resp.data

    def test_dashboard_watchlist_link_absent_when_nothing_generated(self, client):
        c, app, root = client
        resp = c.get('/')
        assert resp.status_code == 200
        assert b'_watchlist.html' not in resp.data

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

    def test_run_stream_emits_heartbeat_when_idle(self, client, monkeypatch):
        """H2: with no new output for SSE_HEARTBEAT_SECONDS, generate()
        must send a keepalive comment instead of blocking forever."""
        c, app, root = client
        monkeypatch.setattr(app_module, 'SSE_HEARTBEAT_SECONDS', 0.05)
        monkeypatch.setenv('CURATARR_TEST_SLOW', '0.5')
        c.post('/run', data={'engine': 'movie', 'user': 'alice'})
        resp = c.get('/run/stream')
        body = resp.get_data(as_text=True)
        assert ': keepalive' in body
        assert 'event: done' in body
        _wait_until_idle(app)

    def test_run_stream_disconnect_mid_run_unsubscribes(self, client, monkeypatch):
        """H2: a client that disappears mid-stream (closed tab, dead
        socket) must be unsubscribed - not left in job._subscribers
        piling up output nobody will ever read."""
        c, app, root = client
        monkeypatch.setenv('CURATARR_TEST_SLOW', '2')
        c.post('/run', data={'engine': 'movie', 'user': 'alice'})
        job = app.job_manager.current_job()

        resp = c.get('/run/stream')
        chunks = iter(resp.response)
        next(chunks)  # pull one chunk so the generator has actually subscribed
        assert len(job._subscribers) == 1

        resp.close()  # simulates the browser socket going away mid-stream

        assert len(job._subscribers) == 0
        _wait_until_idle(app)

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

    def test_watchlist_html_gets_restrictive_csp_header(self, client):
        """Defense-in-depth on top of the escaping fix in
        recommenders/external_output.py - even a future gap there
        shouldn't be able to turn into a script driving this app's own
        state-changing endpoints."""
        c, app, root = client
        ext_dir = os.path.join(root, 'recommendations', 'external')
        with open(os.path.join(ext_dir, 'watchlist.html'), 'w') as f:
            f.write('<html>hello</html>')
        resp = c.get('/results/watchlist/watchlist.html')
        assert resp.status_code == 200
        csp = resp.headers.get('Content-Security-Policy', '')
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'

    def test_watchlist_md_does_not_get_html_csp_header(self, client):
        c, app, root = client
        ext_dir = os.path.join(root, 'recommendations', 'external')
        with open(os.path.join(ext_dir, 'alice_watchlist.md'), 'w') as f:
            f.write('# hello')
        resp = c.get('/results/watchlist/alice_watchlist.md')
        assert resp.status_code == 200
        assert 'Content-Security-Policy' not in resp.headers

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


class TestBindRetry:
    """Tests for _run_with_bind_retry - lets a post-update relaunch
    (see web/update_apply.py's _relaunch_ui) tolerate a brief window
    where the OS hasn't fully released the port the just-killed old
    server was using yet."""

    def test_succeeds_immediately_when_bind_works(self):
        fake_app = Mock()
        fake_app.run = Mock(return_value=None)
        _run_with_bind_retry(fake_app, '127.0.0.1', 8787)
        fake_app.run.assert_called_once()

    def test_retries_then_succeeds(self):
        fake_app = Mock()
        fake_app.run = Mock(side_effect=[OSError('address in use'), OSError('address in use'), None])
        with patch('web.app.time.sleep'):
            _run_with_bind_retry(fake_app, '127.0.0.1', 8787)
        assert fake_app.run.call_count == 3

    def test_gives_up_after_max_attempts(self):
        fake_app = Mock()
        fake_app.run = Mock(side_effect=OSError('address in use'))
        with patch('web.app.time.sleep'), pytest.raises(OSError):
            _run_with_bind_retry(fake_app, '127.0.0.1', 8787)
        assert fake_app.run.call_count == BIND_RETRY_ATTEMPTS


class TestBindingGuardrail:
    """Guardrail: the app must only ever bind 127.0.0.1, never 0.0.0.0."""

    def test_binds_localhost_only(self):
        import inspect
        import re

        import web.app as app_module

        source = inspect.getsource(app_module)
        # main() calls _run_with_bind_retry(app, '127.0.0.1', port) -
        # not a bare app.run(host='127.0.0.1', ...) - since the bind
        # retry loop (see _run_with_bind_retry) needs to be able to
        # call app.run() more than once. The literal '127.0.0.1' at
        # that one call site is what actually matters here.
        assert "_run_with_bind_retry(app, '127.0.0.1', port)" in source
        # Only the redaction/no-wildcard-bind explanation may mention
        # 0.0.0.0 in prose; nothing must ever pass it as an actual host.
        assert not re.search(r'host\s*=\s*[\'"]0\.0\.0\.0[\'"]', source)
        assert not re.search(r'_run_with_bind_retry\([^)]*0\.0\.0\.0', source)


class TestOriginHostGuard:
    """Tests for web.security.register_origin_host_guard, wired into
    every request via create_app(). The `client` fixture's test client
    stamps a same-origin Origin header by default (see
    _BrowserLikeTestClient in web/app.py) so every *other* test in this
    module models a real same-origin browser request; these tests
    override that default to exercise rejection.
    """

    def test_cross_origin_post_rejected_403(self, client):
        c, app, root = client
        resp = c.post(
            '/run', data={'engine': 'movie', 'user': 'alice'},
            headers={'Origin': 'http://evil.example.com'},
        )
        assert resp.status_code == 403
        assert app.job_manager.current_job() is None

    def test_cross_origin_config_post_rejected_403(self, client):
        c, app, root = client
        resp = c.post(
            '/config/users', data={'user_count': '0', 'new_username': ''},
            headers={'Origin': 'https://attacker.example.com'},
        )
        assert resp.status_code == 403

    def test_post_with_no_origin_or_referer_rejected_403(self, client):
        c, app, root = client
        resp = c.post(
            '/run', data={'engine': 'movie', 'user': 'alice'},
            headers={'Origin': ''},
        )
        assert resp.status_code == 403

    def test_referer_fallback_accepted_when_origin_absent(self, client):
        c, app, root = client
        resp = c.post(
            '/run', data={'engine': 'external', 'user': 'all'},
            headers={'Origin': '', 'Referer': 'http://localhost/run'},
        )
        assert resp.status_code == 303
        _wait_until_idle(app)

    def test_same_origin_post_with_port_accepted(self, client):
        c, app, root = client
        resp = c.post(
            '/run', data={'engine': 'external', 'user': 'all'},
            headers={'Origin': 'http://127.0.0.1:8787'},
        )
        assert resp.status_code == 303
        _wait_until_idle(app)

    def test_get_requests_ignore_origin(self, client):
        c, app, root = client
        resp = c.get('/', headers={'Origin': 'http://evil.example.com'})
        assert resp.status_code == 200

    def test_bad_host_header_rejected_400(self, client):
        c, app, root = client
        resp = c.get('/', headers={'Host': 'evil.example.com'})
        assert resp.status_code == 400

    def test_bad_host_header_with_port_rejected_400(self, client):
        c, app, root = client
        resp = c.get('/', headers={'Host': 'evil.example.com:8787'})
        assert resp.status_code == 400

    def test_valid_host_with_port_accepted(self, client):
        c, app, root = client
        resp = c.get('/', headers={'Host': '127.0.0.1:8787'})
        assert resp.status_code == 200

    def test_valid_bare_localhost_host_accepted(self, client):
        c, app, root = client
        resp = c.get('/', headers={'Host': 'localhost'})
        assert resp.status_code == 200


class TestConcurrentRun:
    """Concurrency test for JobManager's single-run lock, driven through
    the actual HTTP route rather than calling JobManager.start()
    directly - a true race, not a sequential simulation."""

    def test_concurrent_double_post_run_only_one_launches(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setenv('CURATARR_TEST_SLOW', '1')

        def _post(_):
            return c.post('/run', data={'engine': 'movie', 'user': 'alice'})

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(_post, range(6)))

        busy = [r for r in responses if 'error=busy' in r.headers.get('Location', '')]
        launched = [r for r in responses if 'error=busy' not in r.headers.get('Location', '')]
        assert len(launched) == 1
        assert len(busy) == 5
        _wait_until_idle(app)
