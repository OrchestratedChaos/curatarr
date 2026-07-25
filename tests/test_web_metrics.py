"""Tests for the observability endpoints added to web/app.py:

- GET /metrics - Prometheus text-format metrics (utils/metrics.py),
  auth-gated exactly like every other route once bound non-loopback.
- GET /status.json - authenticated readiness detail.
- GET /healthz - unchanged: still liveness + version only, nothing else.

See tests/test_web_security.py's TestRegisterTokenAuth for the same
non-loopback-bind-requires-a-token pattern this file's TestMetricsAuth/
TestStatusJsonAuth classes reuse for these two new routes specifically.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils import metrics
from web.app import create_app


@pytest.fixture
def client(curatarr_web_root):
    app = create_app(project_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


@pytest.fixture(autouse=True)
def _stable_metrics_dir(tmp_path, monkeypatch):
    """Overrides tests/conftest.py's suite-wide _isolated_metrics_dir
    (fresh dir per call) with one STABLE dir for the lifetime of each
    test - needed so a direct utils.metrics.record_*() call and a
    subsequent GET /metrics in the same test see the same on-disk
    state (see utils/metrics.py's module docstring: metrics storage is
    resolved via utils.metrics.get_project_root(), which is entirely
    independent of whatever project_root a given test's Flask app was
    constructed with)."""
    monkeypatch.setattr('utils.metrics.get_project_root', lambda: str(tmp_path))
    return tmp_path


class TestMetricsAuth:
    """GET /metrics must be behind the same token gate as the rest of
    the app once bound non-loopback - it exposes library names, user
    counts, and integration topology via its labels, same reasoning as
    every other authenticated route (see web/app.py's metrics_endpoint
    docstring)."""

    NON_LOOPBACK_HOST = '0.0.0.0'
    TOKEN = 'a' * 32

    def _client(self, curatarr_web_root, bind_host, monkeypatch, token=None):
        if token is not None:
            monkeypatch.setenv('CURATARR_AUTH_TOKEN', token)
        else:
            monkeypatch.delenv('CURATARR_AUTH_TOKEN', raising=False)
        monkeypatch.delenv('CURATARR_TRUSTED_NETWORK', raising=False)
        app = create_app(project_root=curatarr_web_root, bind_host=bind_host)
        app.testing = True
        return app.test_client()

    def test_metrics_401_without_token_on_non_loopback_bind(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/metrics')
        assert resp.status_code == 401

    def test_metrics_200_with_correct_token_on_non_loopback_bind(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/metrics', headers={'X-Curatarr-Token': self.TOKEN})
        assert resp.status_code == 200

    def test_metrics_401_with_wrong_token_on_non_loopback_bind(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/metrics', headers={'X-Curatarr-Token': 'wrong' * 8})
        assert resp.status_code == 401

    def test_metrics_not_in_the_unauthenticated_exemption_list(self):
        """Direct assertion on the exemption set itself - /login and
        /healthz are the only two routes ever meant to skip the token
        guard (see web/security.py's _TOKEN_EXEMPT_PATHS docstring)."""
        from web.security import _TOKEN_EXEMPT_PATHS
        assert '/metrics' not in _TOKEN_EXEMPT_PATHS

    def test_metrics_reachable_on_loopback_bind_with_no_token(self, curatarr_web_root, monkeypatch):
        """Byte-for-byte unchanged native-install behavior, same as
        every other route - loopback bind never requires a token."""
        c = self._client(curatarr_web_root, '127.0.0.1', monkeypatch, token=None)
        resp = c.get('/metrics')
        assert resp.status_code == 200


class TestMetricsContent:
    def test_returns_prometheus_text_content_type(self, client):
        c, app, root = client
        resp = c.get('/metrics')
        assert resp.status_code == 200
        assert 'text/plain' in resp.content_type

    def test_includes_build_info_with_current_version(self, client):
        from utils import __version__
        c, app, root = client
        resp = c.get('/metrics')
        body = resp.get_data(as_text=True)
        assert f'curatarr_build_info{{version="{__version__}"}} 1' in body

    def test_scraping_never_hits_the_network(self, client, monkeypatch):
        """Scraping must never trigger a Plex/TMDB/etc. request (see
        utils.metrics.render_prometheus_text's docstring) - assert
        directly that a real socket connect during the request would be
        caught (the suite's own autouse _block_non_loopback_sockets
        fixture already guards against this at the socket layer for
        every test; this just confirms the response still comes back
        successfully with that guard active)."""
        c, app, root = client
        resp = c.get('/metrics')
        assert resp.status_code == 200

    def test_metric_values_change_after_a_simulated_run(self, client):
        """Simulates a completed recommender run the exact way real
        production code does - utils.cli.run_recommender_main and
        recommenders/external.py's main() both call
        utils.metrics.record_recommender_run() directly once a run
        finishes (see those modules) - then confirms /metrics reflects
        it. Proves cross-process aggregation actually works: this call
        happens in the SAME process as the test, but through the exact
        same on-disk state file (see utils/metrics.py's module
        docstring) a real subprocess run would use, and the /metrics
        route only ever reads that file fresh on each request - it
        never caches in-process state across scrapes."""
        c, app, root = client

        before = c.get('/metrics').get_data(as_text=True)
        assert 'curatarr_recommender_runs_total{engine="movie",outcome="success"}' not in before

        metrics.record_recommender_run('movie', 'success', 42.0)

        after = c.get('/metrics').get_data(as_text=True)
        assert 'curatarr_recommender_runs_total{engine="movie",outcome="success"} 1.0' in after
        assert 'curatarr_recommender_run_duration_seconds_sum{engine="movie",outcome="success"} 42.0' in after

    def test_metric_values_increment_further_after_a_second_run(self, client):
        c, app, root = client
        metrics.record_recommender_run('tv', 'success', 3.0)
        first = c.get('/metrics').get_data(as_text=True)
        assert 'curatarr_recommender_runs_total{engine="tv",outcome="success"} 1.0' in first

        metrics.record_recommender_run('tv', 'success', 4.0)
        second = c.get('/metrics').get_data(as_text=True)
        assert 'curatarr_recommender_runs_total{engine="tv",outcome="success"} 2.0' in second


class TestStatusJsonAuth:
    """GET /status.json - authenticated readiness detail, same gate as
    /metrics above (not in _TOKEN_EXEMPT_PATHS)."""

    NON_LOOPBACK_HOST = '0.0.0.0'
    TOKEN = 'b' * 32

    def _client(self, curatarr_web_root, bind_host, monkeypatch, token=None):
        if token is not None:
            monkeypatch.setenv('CURATARR_AUTH_TOKEN', token)
        else:
            monkeypatch.delenv('CURATARR_AUTH_TOKEN', raising=False)
        monkeypatch.delenv('CURATARR_TRUSTED_NETWORK', raising=False)
        app = create_app(project_root=curatarr_web_root, bind_host=bind_host)
        app.testing = True
        return app.test_client()

    def test_401_without_token_on_non_loopback_bind(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/status.json')
        assert resp.status_code == 401

    def test_200_with_correct_token_on_non_loopback_bind(self, curatarr_web_root, monkeypatch):
        c = self._client(curatarr_web_root, self.NON_LOOPBACK_HOST, monkeypatch, token=self.TOKEN)
        resp = c.get('/status.json', headers={'X-Curatarr-Token': self.TOKEN})
        assert resp.status_code == 200


class TestStatusJsonContent:
    def test_shape_on_a_fresh_install(self, client):
        c, app, root = client
        resp = c.get('/status.json')
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload['config_valid'] is True
        assert payload['job_running'] is False
        assert payload['last_run'] is None
        assert 'version' in payload

    def test_reflects_last_run_after_a_log_exists(self, client):
        c, app, root = client
        log_path = os.path.join(root, 'logs', 'recommendations_alice_20260101_030000.log')
        with open(log_path, 'w') as f:
            f.write('Processing alice\nDone\n')
        resp = c.get('/status.json')
        payload = resp.get_json()
        assert payload['last_run']['status'] == 'success'
        assert payload['last_run']['timestamp'] is not None

    def test_config_invalid_when_config_missing(self, tmp_path):
        (tmp_path / 'logs').mkdir()
        (tmp_path / 'recommendations' / 'external').mkdir(parents=True)
        app = create_app(project_root=str(tmp_path))
        app.testing = True
        resp = app.test_client().get('/status.json')
        assert resp.get_json()['config_valid'] is False

    def test_does_not_leak_library_or_hostname_details(self, client):
        """Narrower than /metrics on purpose - only version/config-valid/
        job-running/last-run, never library names, usernames, or
        integration hostnames (config.yml's fixture data below would
        appear in the response body if this endpoint were ever widened
        to include raw config)."""
        c, app, root = client
        resp = c.get('/status.json')
        body = resp.get_data(as_text=True)
        assert 'not-a-real-radarr-key' not in body
        assert 'localhost:7878' not in body


class TestHealthzUnchanged:
    """/healthz stays unauthenticated AND boring - liveness + version
    only, no library/user/integration detail (that lives behind auth on
    /status.json and /metrics instead - see web/app.py's healthz()
    docstring)."""

    def test_exact_response_shape_is_version_only(self, client):
        c, app, root = client
        resp = c.get('/healthz')
        assert resp.status_code == 200
        payload = resp.get_json()
        assert set(payload.keys()) == {'version'}

    def test_reachable_without_any_auth_even_non_loopback(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv('CURATARR_AUTH_TOKEN', 'c' * 32)
        monkeypatch.delenv('CURATARR_TRUSTED_NETWORK', raising=False)
        app = create_app(project_root=curatarr_web_root, bind_host='0.0.0.0')
        app.testing = True
        resp = app.test_client().get('/healthz')
        assert resp.status_code == 200
        assert set(resp.get_json().keys()) == {'version'}

    def test_does_not_leak_config_details(self, client):
        c, app, root = client
        resp = c.get('/healthz')
        body = resp.get_data(as_text=True)
        assert 'not-a-real-radarr-key' not in body
        assert 'alice' not in body
        assert 'Movies' not in body


class TestUnhandledErrorMetric:
    """web/app.py's generic @app.errorhandler(Exception) - records
    curatarr_unhandled_errors_total for a genuine unhandled exception,
    and must NEVER intercept a deliberate abort()/HTTPException (login/
    token-auth 401s, /run/stream's 404 before any job, etc.)."""

    def test_deliberate_404_is_not_recorded_as_an_unhandled_error(self, client):
        c, app, root = client
        before = c.get('/metrics').get_data(as_text=True)
        resp = c.get('/run/stream')  # 404 by design - no job started yet
        assert resp.status_code == 404
        after = c.get('/metrics').get_data(as_text=True)
        assert before.count('curatarr_unhandled_errors_total') == after.count('curatarr_unhandled_errors_total')
        assert 'curatarr_unhandled_errors_total{component="web"}' not in after

    def test_genuine_unhandled_exception_is_recorded(self, client, monkeypatch):
        c, app, root = client

        @app.get('/__boom')
        def _boom():
            raise RuntimeError("simulated unhandled error")

        with pytest.raises(RuntimeError):
            c.get('/__boom')

        text = c.get('/metrics').get_data(as_text=True)
        assert 'curatarr_unhandled_errors_total{component="web"} 1.0' in text
