"""Tests for web/app.py - Flask routes for the dashboard, run, and
results screens, plus the localhost-only binding guardrail.
"""

import concurrent.futures
import os
import sys
import time
from unittest.mock import Mock, patch

from flask.testing import FlaskClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.app as app_module
from web.app import BIND_RETRY_ATTEMPTS, _run_with_bind_retry, _wait_for_listening, create_app


@pytest.fixture
def client(curatarr_web_root):
    # code_root=curatarr_web_root too: that fixture also contains fake
    # recommenders/*.py + run.sh/run.ps1 precisely so a POST /run in
    # these tests launches THOSE instead of the real repo's (#260 -
    # see web/app.py's create_app docstring for why project_root and
    # code_root are independent).
    app = create_app(project_root=curatarr_web_root, code_root=curatarr_web_root)
    app.testing = True
    return app.test_client(), app, curatarr_web_root


def _wait_until_idle(app, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not app.job_manager.is_running():
            return
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


class TestConfigLoadCaching:
    """#262: load_config() re-read+re-parsed config.yml (plus every
    module file it merges in) from disk on EVERY call, and web/app.py
    called it 1-2x per page render - which read as a container that was
    repeatedly restarting. Pins both halves of the fix: a render costs
    at most one real load_config() call, AND a config change made
    through the web UI is still picked up on the very next request (no
    stale cache surviving a save).
    """

    def test_dashboard_render_triggers_at_most_one_config_load(self, client):
        c, app, root = client
        with patch("web.app.load_config", wraps=app_module.load_config) as mock_load_config:
            resp = c.get("/")
        assert resp.status_code == 200
        assert mock_load_config.call_count == 1

    def test_repeated_renders_reuse_the_cached_config(self, client):
        c, app, root = client
        with patch("web.app.load_config", wraps=app_module.load_config) as mock_load_config:
            c.get("/")
            c.get("/")
            c.get("/")
        # Three renders, config.yml/tuning.yml untouched in between -
        # still exactly one real disk load, not three.
        assert mock_load_config.call_count == 1

    def test_web_ui_save_invalidates_the_cache_immediately(self, client):
        """The critical round-trip: a save through /config/users must be
        visible on the very next dashboard render, not served stale from
        the cache populated by an earlier render."""
        c, app, root = client
        first = c.get("/")
        assert b"alice" in first.data
        assert b"carol" not in first.data

        c.post(
            "/config/users",
            data={
                "user_count": "2",
                "username_0": "alice",
                "display_name_0": "",
                "exclude_genres_0": "",
                "max_rating_0": "",
                "streaming_services_0": "",
                "username_1": "bob",
                "display_name_1": "",
                "exclude_genres_1": "",
                "max_rating_1": "",
                "streaming_services_1": "",
                "new_username": "carol",
            },
        )

        second = c.get("/")
        assert b"carol" in second.data


class TestVersionDisplay:
    """#265: the running version is shown in the UI, not just exposed
    via /healthz and /status.json - on every page (the topbar), not
    just Settings (the issue's literal ask), since a page reload is all
    it takes to check it either way."""

    def test_version_shown_on_dashboard(self, client):
        c, app, root = client
        resp = c.get("/")
        assert f"v{app_module.__version__}".encode() in resp.data

    def test_version_shown_on_settings_page(self, client):
        c, app, root = client
        resp = c.get("/config/settings")
        assert f"v{app_module.__version__}".encode() in resp.data


class TestDashboard:
    """Tests for GET /"""

    def test_renders_users_from_config(self, client):
        c, app, root = client
        resp = c.get("/")
        assert resp.status_code == 200
        assert b"alice" in resp.data
        assert b"bob" in resp.data

    def test_shows_never_run_when_no_logs(self, client):
        c, app, root = client
        resp = c.get("/")
        assert b"never_run" in resp.data

    def test_shows_success_status_from_log(self, client):
        c, app, root = client
        log_path = os.path.join(root, "logs", "recommendations_alice_20260101_030000.log")
        with open(log_path, "w") as f:
            f.write("Processing alice\nDone\n")
        resp = c.get("/")
        assert b"success" in resp.data

    def test_links_to_per_user_watchlist_when_generated(self, client):
        c, app, root = client
        ext_dir = os.path.join(root, "recommendations", "external")
        with open(os.path.join(ext_dir, "alice_a_watchlist.html"), "w") as f:
            f.write("<html>alice list</html>")
        resp = c.get("/")
        assert resp.status_code == 200
        assert b"alice_a_watchlist.html" in resp.data

    def test_dashboard_watchlist_link_absent_when_nothing_generated(self, client):
        c, app, root = client
        resp = c.get("/")
        assert resp.status_code == 200
        assert b"_watchlist.html" not in resp.data

    def test_scheduler_disabled_by_default(self, client):
        c, app, root = client
        resp = c.get("/")
        assert b"Scheduler:" in resp.data
        assert b"disabled" in resp.data

    def test_scheduler_shows_next_run_when_enabled(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setenv("TZ", "UTC")
        config_path = os.path.join(root, "config", "config.yml")
        with open(config_path, "w") as f:
            f.write(
                'plex:\n  url: "http://localhost:32400"\n  token: "not-a-real-token"\n'
                'users:\n  list: "alice"\n'
                'schedule:\n  enabled: true\n  time: "03:00"\n'
            )
        resp = c.get("/")
        assert b"next run" in resp.data

    def test_scheduler_shows_error_for_invalid_schedule(self, client):
        c, app, root = client
        config_path = os.path.join(root, "config", "config.yml")
        with open(config_path, "w") as f:
            f.write(
                'plex:\n  url: "http://localhost:32400"\n  token: "not-a-real-token"\n'
                'users:\n  list: "alice"\n'
                'schedule:\n  enabled: true\n  time: "not-a-time"\n'
            )
        resp = c.get("/")
        assert b"invalid schedule" in resp.data

    def test_scheduler_shows_last_attempt_when_thread_attached(self, client):
        """app.scheduler_thread is None under create_app() by default
        (see that attribute's own comment) - a real SchedulerThread is
        only attached by main(), so this simulates that to prove the
        dashboard reads its state correctly when one IS attached."""
        from datetime import datetime, timezone
        from unittest.mock import Mock

        c, app, root = client
        fake_thread = Mock()
        fake_thread.state.snapshot.return_value = {
            "last_attempt_at": datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc),
            "last_result": "skipped - A run is already in progress",
        }
        app.scheduler_thread = fake_thread

        resp = c.get("/")

        assert b"last scheduled attempt" in resp.data
        assert b"skipped" in resp.data

    def test_handles_missing_config_gracefully(self, tmp_path):
        (tmp_path / "logs").mkdir()
        (tmp_path / "recommendations" / "external").mkdir(parents=True)
        app = create_app(project_root=str(tmp_path))
        app.testing = True
        resp = app.test_client().get("/")
        assert resp.status_code == 200
        assert b"No users configured" in resp.data


class TestBaselineSecurityHeaders:
    """FIX 6: every response gets a baseline set of hardening headers
    (see web/app.py's _set_security_headers/_BASELINE_SECURITY_HEADERS),
    not just the watchlist route's own stricter CSP (see
    TestResults::test_watchlist_html_gets_restrictive_csp_header /
    test_watchlist_md_gets_baseline_not_the_stricter_html_csp for that
    one's own coverage)."""

    def test_normal_route_gets_baseline_headers(self, client):
        c, app, root = client
        resp = c.get("/")
        assert resp.status_code == 200
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "same-origin"
        assert resp.headers.get("Content-Security-Policy") == app_module.BASELINE_CSP

    def test_referrer_policy_preserves_origin_on_same_origin_form_posts(self, client):
        """Regression test for #260: every HTML form POST returned 403.

        Root cause was Referrer-Policy: no-referrer - per the Fetch spec, a
        browser sends Origin: null (and no Referer) on a same-origin plain
        <form method="post"> navigation under that policy, which made
        register_origin_host_guard's Origin check (web/security.py) treat
        every non-fetch() POST as cross-site and reject it with 403 -
        including /login, since that guard runs before token auth.

        This test pins the actual invariant, not just today's chosen
        value: the response's Referrer-Policy must be one that still
        sends a real Origin on a same-origin form POST. no-referrer and
        same-origin (or stricter-when-safe) look identical from the
        single-assertion test that pinned no-referrer before this fix -
        that test passed while the product was broken - so this
        explicitly allowlists only policies that preserve Origin
        same-origin, and explicitly rejects no-referrer.
        """
        c, app, root = client
        resp = c.get("/")
        policy = resp.headers.get("Referrer-Policy")
        allowed = {"same-origin", "strict-origin-when-cross-origin", "origin", "no-referrer-when-downgrade"}
        assert policy in allowed, f"Referrer-Policy {policy!r} not in allowlist {allowed}"
        assert policy != "no-referrer"

    def test_baseline_csp_has_no_upgrade_insecure_requests(self, client):
        """This app serves plain HTTP by design (see docs/DOCKER.md) -
        upgrade-insecure-requests would break every resource load."""
        c, app, root = client
        resp = c.get("/")
        assert "upgrade-insecure-requests" not in resp.headers.get("Content-Security-Policy", "")


class TestRunPage:
    """Tests for GET/POST /run and /run/stream, /run/status"""

    def test_get_run_form(self, client):
        c, app, root = client
        resp = c.get("/run")
        assert resp.status_code == 200
        assert b"alice" in resp.data
        assert b"bob" in resp.data

    def test_post_run_triggers_job_and_redirects(self, client):
        c, app, root = client
        resp = c.post("/run", data={"engine": "external", "user": "all"})
        assert resp.status_code == 303
        assert app.job_manager.current_job() is not None
        _wait_until_idle(app)

    def test_post_run_rejects_concurrent_run(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setenv("CURATARR_TEST_SLOW", "1")
        resp1 = c.post("/run", data={"engine": "movie", "user": "alice"})
        assert resp1.status_code == 303
        resp2 = c.post("/run", data={"engine": "movie", "user": "bob"})
        assert resp2.status_code == 303
        assert "error=busy" in resp2.headers["Location"]
        _wait_until_idle(app)

    def test_post_run_rejects_unknown_user(self, client):
        c, app, root = client
        resp = c.post("/run", data={"engine": "movie", "user": "mallory"})
        assert resp.status_code == 303
        assert "error=" in resp.headers["Location"]

    def test_run_stream_before_any_job_404s(self, client):
        c, app, root = client
        resp = c.get("/run/stream")
        assert resp.status_code == 404

    def test_run_stream_after_job_streams_events(self, client):
        c, app, root = client
        c.post("/run", data={"engine": "external", "user": "all"})
        _wait_until_idle(app)
        resp = c.get("/run/stream")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        body = resp.get_data(as_text=True)
        assert "event: done" in body

    def test_run_stream_emits_heartbeat_when_idle(self, client, monkeypatch):
        """H2: with no new output for SSE_HEARTBEAT_SECONDS, generate()
        must send a keepalive comment instead of blocking forever."""
        c, app, root = client
        monkeypatch.setattr(app_module, "SSE_HEARTBEAT_SECONDS", 0.05)
        monkeypatch.setenv("CURATARR_TEST_SLOW", "0.5")
        c.post("/run", data={"engine": "movie", "user": "alice"})
        resp = c.get("/run/stream")
        body = resp.get_data(as_text=True)
        assert ": keepalive" in body
        assert "event: done" in body
        _wait_until_idle(app)

    def test_run_stream_disconnect_mid_run_unsubscribes(self, client, monkeypatch):
        """H2: a client that disappears mid-stream (closed tab, dead
        socket) must be unsubscribed - not left in job._subscribers
        piling up output nobody will ever read."""
        c, app, root = client
        monkeypatch.setenv("CURATARR_TEST_SLOW", "2")
        c.post("/run", data={"engine": "movie", "user": "alice"})
        job = app.job_manager.current_job()

        resp = c.get("/run/stream")
        chunks = iter(resp.response)
        next(chunks)  # pull one chunk so the generator has actually subscribed
        assert len(job._subscribers) == 1

        resp.close()  # simulates the browser socket going away mid-stream

        assert len(job._subscribers) == 0
        _wait_until_idle(app)

    def test_run_stream_over_subscriber_cap_gets_busy_event(self, client, monkeypatch):
        """#287: confirmed in a real container that as few as THREADS
        (8) concurrently open, perfectly legitimate SSE streams for one
        still-running job exhausts waitress's whole thread pool and
        freezes the entire app. MAX_STREAM_SUBSCRIBERS_PER_JOB caps
        concurrent viewers of the SAME job well below that - a viewer
        over the cap gets a `busy` event (app.js falls back to polling
        /run/status - see static/app.js) instead of a connection that
        would just make the exhaustion this cap exists to prevent one
        connection closer, and is never added to job._subscribers."""
        c, app, root = client
        monkeypatch.setattr(app_module, "MAX_STREAM_SUBSCRIBERS_PER_JOB", 1)
        monkeypatch.setenv("CURATARR_TEST_SLOW", "2")
        c.post("/run", data={"engine": "movie", "user": "alice"})
        job = app.job_manager.current_job()

        resp1 = c.get("/run/stream")
        chunks = iter(resp1.response)
        next(chunks)  # pull one chunk so the first stream has actually subscribed
        assert len(job._subscribers) == 1

        resp2 = c.get("/run/stream")
        assert resp2.status_code == 200
        body2 = resp2.get_data(as_text=True)
        assert "event: busy" in body2
        assert len(job._subscribers) == 1  # the rejected stream was never added

        resp1.close()
        _wait_until_idle(app)

    def test_run_stream_max_seconds_closes_without_done_event(self, client, monkeypatch):
        """#287: a single stream must never be allowed to hold its
        server-side thread indefinitely regardless of how long the job
        itself takes (confirmed in a real container: a run can take
        many minutes with no prior upper bound at all on one
        connection's lifetime). Closing without a `done` event (not an
        error) lets EventSource's own default auto-reconnect behavior
        pick the stream back up - app.js deliberately has no onerror
        handler that would call .close() and defeat that (see
        static/app.js)."""
        c, app, root = client
        monkeypatch.setattr(app_module, "MAX_STREAM_SECONDS", 0.05)
        monkeypatch.setenv("CURATARR_TEST_SLOW", "2")
        c.post("/run", data={"engine": "movie", "user": "alice"})
        job = app.job_manager.current_job()

        resp = c.get("/run/stream")
        body = resp.get_data(as_text=True)
        assert "event: done" not in body
        assert len(job._subscribers) == 0  # unsubscribed on the way out
        assert app.job_manager.is_running()  # the job itself is unaffected

        _wait_until_idle(app)
        _wait_until_idle(app)

    def test_run_status_json_idle(self, client):
        c, app, root = client
        resp = c.get("/run/status")
        assert resp.status_code == 200
        assert resp.get_json() == {"state": "idle"}

    def test_run_status_json_after_run(self, client):
        c, app, root = client
        c.post("/run", data={"engine": "external", "user": "all"})
        _wait_until_idle(app)
        resp = c.get("/run/status")
        assert resp.get_json()["state"] == "succeeded"


class TestResults:
    """Tests for GET /results, /results/watchlist/<file>, /results/log/<file>"""

    def test_lists_watchlists_and_logs(self, client):
        c, app, root = client
        ext_dir = os.path.join(root, "recommendations", "external")
        with open(os.path.join(ext_dir, "watchlist.html"), "w") as f:
            f.write("<html>hi</html>")
        with open(os.path.join(root, "logs", "daily-run.log"), "w") as f:
            f.write("cron output\n")
        resp = c.get("/results")
        assert resp.status_code == 200
        assert b"watchlist.html" in resp.data
        assert b"daily-run.log" in resp.data

    def test_no_watchlists_or_logs_yet(self, client):
        c, app, root = client
        resp = c.get("/results")
        assert resp.status_code == 200
        assert b"No watchlists generated yet" in resp.data
        assert b"No logs yet" in resp.data

    def test_serves_watchlist_file(self, client):
        c, app, root = client
        ext_dir = os.path.join(root, "recommendations", "external")
        with open(os.path.join(ext_dir, "watchlist.html"), "w") as f:
            f.write("<html>hello world</html>")
        resp = c.get("/results/watchlist/watchlist.html")
        assert resp.status_code == 200
        assert b"hello world" in resp.data

    def test_watchlist_html_gets_restrictive_csp_header(self, client):
        """Defense-in-depth on top of the escaping fix in
        recommenders/external_render.py - even a future gap there
        shouldn't be able to turn into a script driving this app's own
        state-changing endpoints."""
        c, app, root = client
        ext_dir = os.path.join(root, "recommendations", "external")
        with open(os.path.join(ext_dir, "watchlist.html"), "w") as f:
            f.write("<html>hello</html>")
        resp = c.get("/results/watchlist/watchlist.html")
        assert resp.status_code == 200
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_watchlist_md_gets_baseline_not_the_stricter_html_csp(self, client):
        """results_watchlist() only ever sets its OWN (stricter -
        WATCHLIST_CSP) header for a .html response - a .md one still
        gets a Content-Security-Policy, but the app-wide BASELINE_CSP
        set by the after_request hook (see FIX 6/_set_security_headers),
        not results_watchlist()'s own Google Fonts allowance, which
        would be wrong for a plain-text response."""
        c, app, root = client
        ext_dir = os.path.join(root, "recommendations", "external")
        with open(os.path.join(ext_dir, "alice_watchlist.md"), "w") as f:
            f.write("# hello")
        resp = c.get("/results/watchlist/alice_watchlist.md")
        assert resp.status_code == 200
        csp = resp.headers.get("Content-Security-Policy", "")
        assert csp == app_module.BASELINE_CSP
        assert "fonts.googleapis.com" not in csp

    def test_watchlist_rejects_non_html_md_extension(self, client):
        c, app, root = client
        resp = c.get("/results/watchlist/evil.txt")
        assert resp.status_code == 404

    def test_watchlist_rejects_traversal(self, client):
        c, app, root = client
        # A secret one directory above recommendations/external/, that a
        # traversal attempt with an allow-listed extension might target.
        with open(os.path.join(root, "secret.html"), "w") as f:
            f.write("TOP SECRET MARKER")
        resp = c.get("/results/watchlist/..%2Fsecret.html")
        assert resp.status_code == 404
        assert b"TOP SECRET MARKER" not in resp.data

    def test_views_log_tail(self, client):
        c, app, root = client
        with open(os.path.join(root, "logs", "a.log"), "w") as f:
            f.write("line one\ntoken=abcdef123456\n")
        resp = c.get("/results/log/a.log")
        assert resp.status_code == 200
        assert b"line one" in resp.data
        assert b"abcdef123456" not in resp.data

    def test_log_view_missing_file_404s(self, client):
        c, app, root = client
        resp = c.get("/results/log/missing.log")
        assert resp.status_code == 404

    def test_log_view_rejects_traversal(self, client):
        c, app, root = client
        resp = c.get("/results/log/..%2Fconfig%2Fconfig.yml")
        assert resp.status_code == 404

    def test_empty_log_shows_reason_instead_of_blank_pane(self, client):
        """#263: a 0-byte log used to render as a silent blank pane with
        no indication of why - now shows why."""
        c, app, root = client
        open(os.path.join(root, "logs", "empty.log"), "w").close()
        resp = c.get("/results/log/empty.log")
        assert resp.status_code == 200
        assert b"empty" in resp.data.lower()


class TestWaitForListening:
    """Tests for the launcher's "server is actually listening" poll."""

    def test_returns_true_when_port_open(self):
        import socket

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            assert _wait_for_listening(port, timeout=2) is True
        finally:
            server.close()

    def test_returns_false_when_nothing_listening(self):
        import socket

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
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
        _run_with_bind_retry(fake_app, "127.0.0.1", 8787)
        fake_app.run.assert_called_once()

    def test_retries_then_succeeds(self):
        fake_app = Mock()
        fake_app.run = Mock(side_effect=[OSError("address in use"), OSError("address in use"), None])
        with patch("web.app.time.sleep"):
            _run_with_bind_retry(fake_app, "127.0.0.1", 8787)
        assert fake_app.run.call_count == 3

    def test_gives_up_after_max_attempts(self):
        fake_app = Mock()
        fake_app.run = Mock(side_effect=OSError("address in use"))
        with patch("web.app.time.sleep"), pytest.raises(OSError):
            _run_with_bind_retry(fake_app, "127.0.0.1", 8787)
        assert fake_app.run.call_count == BIND_RETRY_ATTEMPTS


class TestBindingGuardrail:
    """Guardrail: the app must only ever bind 127.0.0.1, never 0.0.0.0."""

    def test_binds_localhost_only(self):
        import inspect
        import re

        import web.app as app_module

        source = inspect.getsource(app_module)
        # main() calls _run_with_bind_retry(app, "127.0.0.1", port) -
        # not a bare app.run(host="127.0.0.1", ...) - since the bind
        # retry loop (see _run_with_bind_retry) needs to be able to
        # call app.run() more than once. The literal "127.0.0.1" at
        # that one call site is what actually matters here.
        assert '_run_with_bind_retry(app, "127.0.0.1", port)' in source
        # Only the redaction/no-wildcard-bind explanation may mention
        # 0.0.0.0 in prose; nothing must ever pass it as an actual host.
        assert not re.search(r'host\s*=\s*[\'"]0\.0\.0\.0[\'"]', source)
        assert not re.search(r"_run_with_bind_retry\([^)]*0\.0\.0\.0", source)


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
            "/run",
            data={"engine": "movie", "user": "alice"},
            headers={"Origin": "http://evil.example.com"},
        )
        assert resp.status_code == 403
        assert app.job_manager.current_job() is None

    def test_cross_origin_config_post_rejected_403(self, client):
        c, app, root = client
        resp = c.post(
            "/config/users",
            data={"user_count": "0", "new_username": ""},
            headers={"Origin": "https://attacker.example.com"},
        )
        assert resp.status_code == 403

    def test_post_with_no_origin_or_referer_rejected_403(self, client):
        c, app, root = client
        resp = c.post(
            "/run",
            data={"engine": "movie", "user": "alice"},
            headers={"Origin": ""},
        )
        assert resp.status_code == 403

    def test_null_origin_post_rejected_403_raw_client(self, curatarr_web_root):
        """#260 follow-up: confirms the fix does NOT loosen the guard.

        Uses the raw Flask test client (not the 'client' fixture's
        _BrowserLikeTestClient, which stamps a same-origin Origin header
        onto every request) to model what a genuinely cross-origin or
        opaque-origin request looks like: Origin: null with no Referer -
        exactly what a sandboxed iframe, a data:/blob: document, or a
        cross-site form POST produces. Both /login and /run must still
        403 this - same-origin Referrer-Policy only fixes the same-
        origin case (#260), it never makes the guard accept "null".
        """
        app = create_app(project_root=curatarr_web_root)
        app.testing = True
        app.test_client_class = FlaskClient
        raw_client = app.test_client()

        login_resp = raw_client.post("/login", data={"token": "whatever"}, headers={"Origin": "null"})
        assert login_resp.status_code == 403

        run_resp = raw_client.post("/run", data={"engine": "movie", "user": "alice"}, headers={"Origin": "null"})
        assert run_resp.status_code == 403

    def test_referer_fallback_accepted_when_origin_absent(self, client):
        c, app, root = client
        resp = c.post(
            "/run",
            data={"engine": "external", "user": "all"},
            headers={"Origin": "", "Referer": "http://localhost/run"},
        )
        assert resp.status_code == 303
        _wait_until_idle(app)

    def test_same_origin_post_with_port_accepted(self, client):
        c, app, root = client
        resp = c.post(
            "/run",
            data={"engine": "external", "user": "all"},
            headers={"Origin": "http://127.0.0.1:8787"},
        )
        assert resp.status_code == 303
        _wait_until_idle(app)

    def test_get_requests_ignore_origin(self, client):
        c, app, root = client
        resp = c.get("/", headers={"Origin": "http://evil.example.com"})
        assert resp.status_code == 200

    def test_bad_host_header_rejected_400(self, client):
        c, app, root = client
        resp = c.get("/", headers={"Host": "evil.example.com"})
        assert resp.status_code == 400

    def test_bad_host_header_with_port_rejected_400(self, client):
        c, app, root = client
        resp = c.get("/", headers={"Host": "evil.example.com:8787"})
        assert resp.status_code == 400

    def test_valid_host_with_port_accepted(self, client):
        c, app, root = client
        resp = c.get("/", headers={"Host": "127.0.0.1:8787"})
        assert resp.status_code == 200

    def test_valid_bare_localhost_host_accepted(self, client):
        c, app, root = client
        resp = c.get("/", headers={"Host": "localhost"})
        assert resp.status_code == 200


class TestConcurrentRun:
    """Concurrency test for JobManager's single-run lock, driven through
    the actual HTTP route rather than calling JobManager.start()
    directly - a true race, not a sequential simulation."""

    def test_concurrent_double_post_run_only_one_launches(self, client, monkeypatch):
        c, app, root = client
        monkeypatch.setenv("CURATARR_TEST_SLOW", "1")

        def _post(_):
            return c.post("/run", data={"engine": "movie", "user": "alice"})

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(_post, range(6)))

        busy = [r for r in responses if "error=busy" in r.headers.get("Location", "")]
        launched = [r for r in responses if "error=busy" not in r.headers.get("Location", "")]
        assert len(launched) == 1
        assert len(busy) == 5
        _wait_until_idle(app)
