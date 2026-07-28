"""Flask web UI for curatarr - MVP: dashboard, trigger-a-run with a live
log stream, and a read-only results/history viewer.

Design notes:
- Binds to 127.0.0.1 ONLY (see run-ui.sh / run-ui.ps1) - never 0.0.0.0.
- Recommender runs are always subprocesses (see web/job_runner.py). The
  entry points hijack sys.stdout and call sys.exit(), so importing them
  in-process into a long-lived Flask server would be unsafe.
- Config is read through utils.load_config / utils.get_users_from_config,
  same helpers the CLI uses - no ad hoc open() calls here, so a future
  multi-tenant refactor of the utils layer carries the web UI with it.
- This module does not alter any existing recommender/CLI behavior; it
  only shells out to it.
- Every request is guarded by web.security.register_origin_host_guard:
  the Host header must be 127.0.0.1/localhost (blocks DNS rebinding),
  and every state-changing request's Origin/Referer must be too (blocks
  a page on any other origin from driving /run or /config/* - this app
  has no other session/auth boundary to rely on).
- That guard alone is NOT authentication - it only stops browsers, not a
  non-browser client that sets Host/Origin itself. web/app.py's own
  main() is hardcoded to 127.0.0.1 only (see below), where that's an
  acceptable trust boundary; web/docker_server.py binds non-loopback for
  container use and additionally requires web.security.register_token_auth
  (CURATARR_AUTH_TOKEN) on every request - see that module for why.
"""

import atexit
import os
import queue
import re
import signal
import socket
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    stream_with_context,
    url_for,
)
from flask.testing import FlaskClient
from werkzeug.datastructures import Headers

from utils import (
    __version__,
    describe_next_run,
    get_integration_status,
    get_project_root,
    get_update_mode,
    get_users_from_config,
    is_dismissed,
    load_config,
    log_error,
    record_dismissal,
    record_unhandled_error,
    render_prometheus_text,
    update_available,
)

from .config_app import register_config_routes
from .job_runner import DONE_SENTINEL, JobAlreadyRunningError, JobError, JobManager
from .scheduler_runner import SchedulerThread
from .security import redact, register_origin_host_guard, register_token_auth
from .status import (
    LOG_VIEW_MAX_BYTES,
    find_user_watchlist,
    get_last_run_status,
    list_log_files,
    read_log_full,
    read_log_tail,
)
from .update_apply import (
    UpdateAlreadyInProgressError,
    UpdateManager,
    UpdateNotAvailableError,
)

DEFAULT_PORT = 8787

# How long the SSE stream waits for a new line before sending a
# keepalive comment - see run_stream()'s generate().
SSE_HEARTBEAT_SECONDS = 15.0

# #287: bounds how long a single /run/stream connection may occupy one
# of waitress's THREADS worker threads (see web/docker_server.py's own
# comment - sized "for one open SSE live-log stream", not many)
# before this proactively closes it. Confirmed in a real container:
# waitress dispatches a streaming WSGI response to ONE task thread for
# the connection's ENTIRE lifetime (it synchronously iterates the
# generator - see waitress/task.py's WSGITask.execute), so a run that
# takes many minutes previously let a single stream pin a thread for
# that whole time with no upper bound at all. EventSource's own
# default behavior auto-reconnects after any server-initiated close
# that isn't a fatal HTTP-level error (see generate() below - it just
# returns, ending the response normally, no error event) - the browser
# picks the stream back up on its own a few seconds later, replaying
# the backlog via Job.subscribe(), with no code needed here to make
# that happen.
MAX_STREAM_SECONDS = 120.0

# #287: caps concurrent /run/stream subscribers for the SAME running
# job. Only one job ever runs at a time (JobManager enforces this), so
# every additional viewer watching it live is a fully redundant stream
# of identical output, each pinning one more of only THREADS (8)
# waitress worker threads for as long as it stays open. Confirmed in a
# real container: as few as THREADS concurrently open streams during
# one live run - not stuck, not misbehaving, just genuinely still
# watching - exhausts the pool and freezes the ENTIRE app (every
# route, not just streaming ones) until a stream closes or the run
# ends. Reserves at least half the pool for everything else (the
# dashboard, /run/status polling, /results, config screens) even in
# the worst case where every viewer leaves a tab open. A viewer over
# the cap isn't left with nothing - app.js falls back to polling
# /run/status (see static/app.js) instead of a live-tailing stream.
MAX_STREAM_SUBSCRIBERS_PER_JOB = 4

# Applied to served watchlist HTML (see results_watchlist()). Primary
# XSS defense is escaping at generation time (recommenders/
# external_render.py); this is defense-in-depth so that even a gap
# there can't turn into a same-origin script able to reach this app's
# own state-changing endpoints via object embeds / cross-frame tricks.
# script-src still needs 'unsafe-inline' since the watchlist page's own
# sort/filter/export UI is inline <script> - that's an existing,
# intentional part of the page, not something this CSP is meant to
# block.
WATCHLIST_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

# Baseline clickjacking/MIME-sniffing/CSP hardening applied to every
# response (see _set_security_headers below) - the config UI had no CSP
# and no X-Frame-Options at all before this, outside of
# results_watchlist()'s own (stricter, above) - meaning it was framable,
# and a click inside a same-origin iframe passes the Origin guard, so a
# clickjacking page could drive e.g. the Auto-sync toggle.
#
# No upgrade-insecure-requests: this app serves plain HTTP by design
# (see docs/DOCKER.md), that directive would break every resource load.
# 'unsafe-inline' on script-src/style-src: base.html and run.html each
# have one inline <script> block (the update-banner poll and the
# window.CURATARR_HAS_JOB handoff to app.js), and templates use inline
# style attributes - both confirmed by reading every template, nothing
# else needed it. font-src 'self': fonts are served locally from
# web/static/fonts/*.woff2 (confirmed - no template references an
# external font CDN outside of results_watchlist()'s own page, which
# keeps its stricter, separate CSP above).
BASELINE_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

_BASELINE_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    # same-origin (not no-referrer - see #260): still sends NO referrer on
    # a cross-origin request, so a link out of this app never leaks
    # anything to another site, but it DOES send one on a same-origin
    # request/navigation - which is what makes the browser attach a real
    # Origin header (not "null") to a plain <form method="post"> submit.
    # register_origin_host_guard (web/security.py) reads that Origin to
    # tell a same-origin form POST apart from a cross-site one; under
    # no-referrer every non-fetch() POST looked cross-site (Origin: null)
    # and got 403'd, including /login itself. A cross-origin form POST
    # still gets Origin: null under same-origin (browsers never send
    # Origin OR Referer cross-origin under this policy) and is still
    # correctly rejected - this only fixes the same-origin case, it does
    # not loosen the guard.
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": BASELINE_CSP,
}

# Used by update_dismiss() below to validate a 'next' redirect target is
# a genuine same-app relative path, not an open-redirect. A leading '/'
# alone isn't sufficient: '/\evil.com' also starts with '/' and doesn't
# start with '//', but some browsers normalize a leading backslash the
# same as a second forward slash, turning it into a protocol-relative
# URL to another origin (//evil.com) - a well-known open-redirect
# bypass. Requiring the character right after the leading '/' to be
# neither '/' nor '\' closes that off.
_SAFE_RELATIVE_REDIRECT_RE = re.compile(r"^/[^/\\]")


class _BrowserLikeTestClient(FlaskClient):
    """Flask's default test client sends bare requests (Host: localhost,
    no Origin header) that don't look like a browser hitting the UI's
    own origin - register_origin_host_guard would 403 every test POST
    as cross-origin otherwise. Stamp a same-origin Origin header by
    default so the existing test suite keeps modeling "the browser
    talking to the app it was served from"; a test that wants to
    exercise the guard's rejection path passes its own Origin/Host
    header, which takes precedence over this default.
    """

    def open(self, *args, **kwargs):
        headers = Headers(kwargs.pop("headers", None))
        if "Origin" not in headers:
            headers["Origin"] = "http://localhost"
        kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def create_app(
    project_root: Optional[str] = None, bind_host: Optional[str] = None, code_root: Optional[str] = None
) -> Flask:
    """Application factory. project_root is overridable so tests can
    point the app at a throwaway fixture repo instead of the real one.

    bind_host is the address this app is ABOUT to be served on (not
    something Flask/this factory binds itself) - passed through to
    web.security.register_token_auth so it knows whether real token
    auth needs to be enforced (non-loopback) or not (loopback - the
    default, and the only thing web/app.py's own main() ever uses).
    Defaults to CURATARR_UI_HOST if set (see web/docker_server.py, the
    only caller that ever sets that env var) else '127.0.0.1', so every
    existing call site - including every test - that doesn't pass this
    explicitly keeps today's loopback-only, no-token-required behavior.

    code_root is passed straight through to JobManager (see that
    module's docstring, and utils.helpers.get_code_root's) - it's
    deliberately independent of project_root: the latter defaults to
    get_project_root() (the *data* dir - config/cache/logs, which
    CURATARR_CONFIG_DIR points at a separate volume in Docker), while
    JobManager needs to know where recommenders/<x>.py and run.sh/
    run.ps1 actually live on disk to launch a UI-triggered run (#260) -
    get_code_root() by default when this is left unset, exactly like
    every real caller (native web/app.py's main(), web/docker_server.py)
    wants. Only tests that also stand up a fake recommenders/ tree (see
    tests/conftest.py's curatarr_web_root fixture) need to pass this
    explicitly, pointed at that same fixture root.
    """
    project_root = project_root or get_project_root()
    bind_host = bind_host or os.environ.get("CURATARR_UI_HOST", "127.0.0.1")
    logs_dir = os.path.join(project_root, "logs")
    external_dir = os.path.join(project_root, "recommendations", "external")

    app = Flask(__name__)
    app.config["PROJECT_ROOT"] = project_root
    app.config["LOGS_DIR"] = logs_dir
    app.config["EXTERNAL_DIR"] = external_dir
    # Flask's stub doesn't declare these - real, deliberate attribute
    # attachment (every route reads them back the same way), not a typo.
    app.job_manager = JobManager(project_root, logs_dir, code_root=code_root)  # type: ignore[attr-defined]
    app.update_manager = UpdateManager(project_root, logs_dir)  # type: ignore[attr-defined]
    # #264: None here - every existing test/caller that only calls
    # create_app() directly (which is nearly all of them) never gets a
    # real background thread. Only main() (this module's own, and
    # web/docker_server.py's) constructs a real SchedulerThread and
    # replaces this, right before actually start()-ing it - see there
    # for why it's deliberately NOT wired up inside create_app() itself.
    # Every route that reads this must treat None as "scheduler not
    # running" (e.g. under the test client), never assume it's set.
    app.scheduler_thread = None  # type: ignore[attr-defined]
    app.test_client_class = _BrowserLikeTestClient

    register_origin_host_guard(app)
    register_token_auth(app, bind_host)
    register_config_routes(app)

    @app.after_request
    def _set_security_headers(response):
        """Only sets a header if the route hasn't already set one of its
        own - results_watchlist() sets a stricter Content-Security-
        Policy for served watchlist HTML (WATCHLIST_CSP above), and that
        must always win over this baseline, never get overwritten by
        it."""
        for name, value in _BASELINE_SECURITY_HEADERS.items():
            if name not in response.headers:
                response.headers[name] = value
        return response

    # #262: load_config() re-reads and re-parses config.yml (plus up to
    # 4 module files - tuning/trakt/radarr/sonarr.yml) from disk on
    # EVERY call, and this closure gets called 1-2x per page render (the
    # update-banner context processor below plus whichever route handler
    # is serving the request each call _load_config() independently) -
    # most of what made a Docker container's logs look like it was
    # repeatedly restarting (utils/config.py's own log_info calls for
    # this are now level-controlled, but the disk I/O + YAML parsing
    # itself was still happening every time regardless). Cached below,
    # keyed on the mtimes of every file load_config() reads, so a page
    # view costs at most one disk read - and a config change (through
    # the web UI's own save routes, or a hand edit to tuning.yml on
    # disk) invalidates the cache and is picked up on the very next
    # request, no restart needed. Nanosecond mtimes (not the coarser
    # whole-second os.path.getmtime) so two saves landing within the
    # same second still invalidate correctly.
    _config_cache_lock = threading.Lock()
    _config_cache: Dict[str, Any] = {"populated": False, "mtimes": None, "config": None}
    _CONFIG_MODULE_NAMES = ("tuning", "trakt", "radarr", "sonarr")

    def _config_file_mtimes(config_dir: str) -> Tuple[Optional[int], ...]:
        paths = [os.path.join(config_dir, "config.yml")] + [
            os.path.join(config_dir, f"{name}.yml") for name in _CONFIG_MODULE_NAMES
        ]
        mtimes: List[Optional[int]] = []
        for path in paths:
            try:
                mtimes.append(os.stat(path).st_mtime_ns)
            except OSError:
                mtimes.append(None)
        return tuple(mtimes)

    def _load_config():
        config_dir = os.path.join(project_root, "config")
        config_path = os.path.join(config_dir, "config.yml")
        mtimes = _config_file_mtimes(config_dir)
        with _config_cache_lock:
            if _config_cache["populated"] and _config_cache["mtimes"] == mtimes:
                return _config_cache["config"]
        try:
            config = load_config(config_path)
        except Exception:
            config = None
        with _config_cache_lock:
            _config_cache["populated"] = True
            _config_cache["mtimes"] = mtimes
            _config_cache["config"] = config
        return config

    # #264: exposed so main() (this module's own, and web/docker_server.py's)
    # can hand the SAME cached loader to the scheduler background
    # thread it constructs - a schedule saved through the web UI takes
    # effect on the thread's very next tick, no restart needed, exactly
    # like every other config value this cache already serves.
    app.load_config_cached = _load_config  # type: ignore[attr-defined]

    def _load_users():
        config = _load_config()
        return get_users_from_config(config) if config else []

    # #262: update_available() (itself already interval-gated against
    # hitting the network more than once every UPDATE_CHECK_INTERVAL_HOURS
    # - see utils/update_check.py) still re-reads its own small on-disk
    # cache file on every call, and the context processor below calls it
    # on every single page render. Cached here too, same spirit as
    # _load_config above, but deliberately NOT wrapping is_dismissed()
    # below - a dismiss must take effect on the very next render, and
    # only update_mode is part of this cache's key (so a config change
    # invalidates it immediately, same as _load_config), so a short TTL
    # is enough to dedupe the common case of several renders in a row
    # with nothing having changed.
    _update_check_cache_lock = threading.Lock()
    _update_check_cache: Dict[str, Any] = {"update_mode": None, "computed_at": 0.0, "result": None}
    _UPDATE_CHECK_CACHE_TTL_SECONDS = 60

    def _cached_update_available(update_mode: str):
        now = time.time()
        with _update_check_cache_lock:
            cached = _update_check_cache
            if (
                cached["result"] is not None
                and cached["update_mode"] == update_mode
                and now - cached["computed_at"] < _UPDATE_CHECK_CACHE_TTL_SECONDS
            ):
                return cached["result"]
        result = update_available(update_mode=update_mode)
        with _update_check_cache_lock:
            _update_check_cache["update_mode"] = update_mode
            _update_check_cache["computed_at"] = now
            _update_check_cache["result"] = result
        return result

    @app.context_processor
    def _update_banner_context():
        """Injected into every rendered template (see base.html's
        dismissible banner) so update state doesn't need to be threaded
        through every route individually - this covers config_app.py's
        routes too since they render through this same Flask app.

        As of v2.8.31, the banner renders for EVERY general.update_mode,
        including 'off' - 'off' only ever meant "don't auto-apply", not
        "don't tell me" (see utils.config.get_update_mode's docstring),
        so an opted-out install silently missing updates was the actual
        bug this fixed. It's still hidden when there's genuinely nothing
        to show (no config loaded yet, no newer version known, or the
        offered version is within its 7-day dismiss snooze - see
        utils.update_dismissal). 'force' still shows it too - run.sh/
        run.ps1 (not this banner) is what auto-applies in force mode, so
        a force-mode source install can still have a pending update this
        banner is the only place it surfaces (e.g. between runs).

        Fails open just like utils.update_check: any exception here
        (config missing/unreadable, network error, whatever) just means
        no banner, never a 500 - a broken update check must never break
        normal page rendering.
        """
        try:
            config = _load_config()
            # No config at all (missing/unreadable) is already a degraded
            # state the app can't really run normally in - skip the check
            # entirely (never even calls update_available) rather than
            # defaulting to some mode, which would mean an HTTP call on
            # every single page render for an install that can't even
            # load its config yet.
            if not config:
                return {"update_banner": None}
            update_mode = get_update_mode(config)
            latest, current, is_newer = _cached_update_available(update_mode)
            if not is_newer:
                return {"update_banner": None}
            # 7-day dismiss snooze, keyed to the exact version offered -
            # see utils.update_dismissal.is_dismissed's docstring for why
            # a newer release than the one dismissed always overrides an
            # active snooze instead of also being suppressed.
            if is_dismissed(latest):
                return {"update_banner": None}
            return {
                "update_banner": {
                    "latest": latest,
                    "current": current,
                    "frozen": getattr(sys, "frozen", False),
                    # Docker images update via `docker pull`, not this
                    # banner's "Update now" button (see
                    # web/update_apply.py's UpdateManager.begin_update
                    # RUNNING_IN_DOCKER gate, which refuses that button
                    # anyway) - base.html renders a pull-the-new-image
                    # instruction instead of the button when this is set.
                    "docker": os.environ.get("RUNNING_IN_DOCKER") == "true",
                }
            }
        except Exception:
            return {"update_banner": None}

    @app.context_processor
    def _trakt_health_context():
        """Injected into every rendered template (see base.html's banner
        block) - surfaces a Trakt export/sync failure explicitly,
        without requiring anyone to open a log file. Reads the
        structured status utils.integration_status.record_integration_status
        writes from recommenders/external_sync.py's export_to_trakt /
        sync_watch_history_to_trakt - deliberately NOT log-string
        matching (see CHANGELOG's Trakt token-refresh-persistence entry
        for how fragile/silent that approach turned out to be: every run
        exited 0 and the dashboard reported "succeeded" for months while
        every single Trakt export actually failed).

        Fails open (no banner) on any error, same spirit as
        _update_banner_context above - a broken health check must never
        break normal page rendering. Also hidden whenever Trakt isn't
        even enabled, so an install that's never configured Trakt at
        all never sees this.
        """
        try:
            config = _load_config()
            if not config or not (config.get("trakt") or {}).get("enabled", False):
                return {"trakt_banner": None}
            cache_dir = os.path.join(project_root, config.get("cache_dir", "cache"))
            status = get_integration_status(cache_dir, "trakt_export")
            if not status or status.get("success", True):
                return {"trakt_banner": None}
            return {
                "trakt_banner": {
                    "detail": status.get("detail") or "Trakt export/sync failed - see logs for detail.",
                    "timestamp": status.get("timestamp"),
                }
            }
        except Exception:
            return {"trakt_banner": None}

    @app.context_processor
    def _version_context():
        """#265: the running version, injected into every rendered
        template (see base.html's topbar) so it's visible on every page,
        not just Settings (the issue's original ask) - with "so many
        versions shipped every day" (the issue's own words), a single
        page felt like the wrong place to bury it. Same __version__
        already exposed via /healthz and /status.json - just never
        rendered anywhere a human looks at it directly before this."""
        return {"curatarr_version": __version__}

    @app.post("/update/dismiss")
    def update_dismiss():
        """Snooze the update banner for this version for 7 days (see
        utils.update_dismissal) - server-side state, not a cookie (as of
        v2.8.31): this app is single-tenant/localhost-only with no other
        session boundary (see this module's docstring), and server-side
        state is what lets utils.cli's print_update_notice respect the
        same dismissal, which a browser cookie never could. Redirects
        back to wherever the dismiss button was clicked from."""
        version = request.form.get("version", "")
        next_url = request.form.get("next") or url_for("dashboard")
        # Only ever redirect to a same-app relative path - never let an
        # attacker-controlled 'next' turn this into an open redirect
        # (see _SAFE_RELATIVE_REDIRECT_RE's comment for the '/\' bypass
        # a plain startswith('/') check alone would miss).
        if not _SAFE_RELATIVE_REDIRECT_RE.match(next_url):
            next_url = url_for("dashboard")
        record_dismissal(version)
        return redirect(next_url, code=303)

    @app.post("/update/apply")
    def update_apply_route():
        """ "Update now": source installs verify a newer signed release
        actually exists (see web.update_apply.check_verified_update -
        shells out to run.sh's/run.ps1's own verification, never
        reimplemented here); frozen binaries do a cheap advisory check
        (see web.update_apply._check_update_available_for_binary) and
        leave the real cryptographic verification to the worker's call
        into utils.self_update - see web/update_apply.py's module
        docstring for the full sequence and trust model of each. Either
        way, this hands off to a DETACHED worker process that outlives
        this request/this server process and returns immediately; the
        frontend (base.html) polls /healthz to detect the server coming
        back up on the new version.
        """
        # Refuse to even attempt an update while a recommender run is in
        # flight: that job's subprocess is itself another instance of
        # this same binary (frozen), and killing/swapping this server
        # out from under it while it's running is simply not something
        # to risk. web/job_runner.py's own LOCK_FILENAME is checked
        # again, cross-process, by the detached worker itself right
        # before it shuts anything down (see web/update_apply.py's
        # _run_worker / _recommender_job_in_progress) as a race-safe
        # second gate - this route-level check is just the immediate,
        # synchronous "no" for the common case of a user clicking
        # Update now while a run they can see is still going.
        if app.job_manager.is_running():
            return jsonify(
                {"error": "A recommender run is currently in progress - wait for it to finish before updating."}
            ), 409

        host = "127.0.0.1"
        port = int(os.environ.get("CURATARR_UI_PORT", DEFAULT_PORT))
        try:
            tag = app.update_manager.begin_update(host, port)
        except UpdateAlreadyInProgressError as exc:
            return jsonify({"error": str(exc)}), 409
        except UpdateNotAvailableError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify({"status": "started", "tag": tag}), 202

    @app.get("/healthz")
    def healthz():
        """Unauthenticated-by-design (matches every other GET route -
        this app has no auth boundary beyond binding 127.0.0.1 and the
        origin/host guard, and a version number isn't sensitive).
        Polled by base.html's "Update now" flow to detect the server
        coming back up after a restart, and by whatever launches it
        (see _wait_for_listening) as a liveness probe.

        Deliberately stays THIS boring: liveness + version, nothing
        else. No library names, user names, integration hostnames, or
        any other config detail - unlike /status.json below (which
        needs a valid token/loopback bind the same as every other
        route), widening this unauthenticated endpoint with any of that
        would be an information-disclosure regression."""
        return jsonify({"version": __version__})

    @app.get("/status.json")
    def status_json():
        """Authenticated (NOT in web.security's _TOKEN_EXEMPT_PATHS,
        same as every route below) readiness/status detail that would be
        inappropriate on the unauthenticated /healthz above: last run
        time/outcome across configured users, whether config.yml is
        currently loadable, and whether a run is in progress right now.
        Still deliberately narrow - no library names, user names, or
        integration hostnames - see /metrics below for the endpoint that
        actually exposes that level of detail (and is gated accordingly).
        """
        config = _load_config()
        users = get_users_from_config(config) if config else []
        last_run = None
        for user in users:
            status = get_last_run_status(logs_dir, user)
            if status.get("timestamp") is None:
                continue
            if last_run is None or status["timestamp"] > last_run["timestamp"]:
                last_run = status

        # Trakt export/sync health (see utils.integration_status and
        # recommenders/external_sync.py) - an explicit, structured signal
        # rather than log-string matching, so a Trakt auth/refresh
        # failure is visible here even though it isn't a `last_run`
        # failure (movie.py/tv.py's own run can - and did, for months -
        # exit 0 while Trakt export silently failed underneath it).
        # None whenever Trakt isn't enabled or nothing's been recorded yet.
        trakt_export = None
        if config and (config.get("trakt") or {}).get("enabled", False):
            cache_dir = os.path.join(project_root, config.get("cache_dir", "cache"))
            trakt_export = get_integration_status(cache_dir, "trakt_export")

        return jsonify(
            {
                "version": __version__,
                "config_valid": config is not None,
                "job_running": app.job_manager.is_running(),
                "last_run": {
                    "timestamp": last_run["timestamp"].isoformat(),
                    "status": last_run["status"],
                    # Only non-None when status is "unknown" (#263) - see
                    # web/status.py's get_last_run_status docstring.
                    "reason": last_run.get("reason"),
                }
                if last_run
                else None,
                "trakt_export": trakt_export,
                # #292: the last (or in-progress) web-UI-triggered job's
                # own per-stage breakdown (see web/job_runner.py's
                # Job.stage_results/external_produced_output, #282/#288) -
                # None whenever nothing has been triggered from the web
                # UI at all yet in this process's lifetime. This is a
                # DIFFERENT signal than last_run above (which is
                # per-user, spans movie.py AND tv.py, and survives a
                # server restart via logs/run_status_*.json) - this one
                # is specifically "what did the most recent Run-page
                # click do", including which of movie/tv/external ran,
                # was skipped, or failed, which last_run's single
                # success/failed/unknown can't express on its own.
                "last_job": app.job_manager.status(),
            }
        )

    @app.get("/metrics")
    def metrics_endpoint():
        """Prometheus text-format metrics (see utils/metrics.py) - NOT
        in web.security's _TOKEN_EXEMPT_PATHS, so it's behind the exact
        same token gate as every other route once bound non-loopback
        (see register_token_auth): library names, user counts, and
        integration topology surface indirectly through the metric
        labels this exposes (e.g. which *arr/Trakt/Simkl/etc. services
        are actually configured and being called), which isn't public
        data any more than /config/connections is. Cheap to serve - see
        utils.metrics.render_prometheus_text's docstring: exactly one
        local file read, never a network call, never a Plex/TMDB/etc.
        request, so scraping this on any interval never adds load to
        anything curatarr talks to."""
        return Response(
            render_prometheus_text(),
            mimetype="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.errorhandler(Exception)
    def _handle_unhandled_exception(exc):
        """Records curatarr_unhandled_errors_total (see utils/metrics.py)
        for any exception that escapes a route handler, then re-raises
        so Flask's own default error handling (a 500 in production, the
        interactive debugger under app.debug, which this app never
        enables - see main()) behaves exactly as it did before this
        handler existed. HTTPException (abort(400)/abort(401)/abort(404)/
        etc. - every deliberate, expected non-200 this app already
        returns) is returned AS-IS instead: those aren't unhandled
        errors, they're routes working as designed, and an HTTPException
        instance is itself a valid Flask response (returning it - not
        raising it - is what makes Flask render its normal status
        code/body instead of this becoming a second, wrongly-classified
        exception)."""
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return exc
        record_unhandled_error(component="web")
        # #284: structured line (component, route, stack trace -
        # asctime/timestamp comes from the log formatter itself) so an
        # error visible in curatarr_unhandled_errors_total can actually
        # be traced back to a cause in the logs, not just counted.
        log_error(
            f"Unhandled error in web request: component=web route={request.path} "
            f"method={request.method}: {exc}\n{traceback.format_exc()}"
        )
        raise exc

    @app.get("/")
    def dashboard():
        config = _load_config()
        rows = [
            {
                "username": user,
                **get_last_run_status(logs_dir, user),
                "watchlist_file": find_user_watchlist(external_dir, config, user),
            }
            for user in (get_users_from_config(config) if config else [])
        ]
        # #264: "next run"/"last scheduled attempt" - visible here rather
        # than only on Settings, so a skipped occurrence (run lock held
        # elsewhere) or a disabled/misconfigured schedule is never a
        # silent mystery. app.scheduler_thread is None under the test
        # client and any caller that only calls create_app() directly
        # (see create_app's own comment on that attribute) - never
        # assume it's set.
        scheduler_status = describe_next_run(config or {})
        scheduler_status["last_attempt"] = (
            app.scheduler_thread.state.snapshot() if app.scheduler_thread is not None else None
        )
        return render_template("dashboard.html", rows=rows, job=app.job_manager.status(), scheduler=scheduler_status)

    @app.get("/run")
    def run_form():
        return render_template(
            "run.html",
            users=_load_users(),
            job=app.job_manager.status(),
            running=app.job_manager.is_running(),
            error=request.args.get("error"),
        )

    @app.post("/run")
    def run_trigger():
        engine = request.form.get("engine", "full")
        user = request.form.get("user", "all")
        try:
            app.job_manager.start(engine, user, _load_users())
        except JobAlreadyRunningError:
            return redirect(url_for("run_form", error="busy"), code=303)
        except JobError as exc:
            return redirect(url_for("run_form", error=str(exc)), code=303)
        return redirect(url_for("run_form"), code=303)

    @app.get("/run/stream")
    def run_stream():
        job = app.job_manager.current_job()
        if job is None:
            abort(404)

        # #287: a late subscriber to an already-finished job already
        # gets its full backlog replayed followed by an immediate
        # `done` (Job.subscribe()/try_subscribe() below - confirmed
        # correct in a real container, this was never the actual
        # thread-exhaustion mechanism) and closes right away rather
        # than hanging - cheap enough that it's simplest to let that
        # existing path handle it rather than special-casing it here.
        # app.js now only opens a stream at all for a job it already
        # knows is running (see static/app.js) - this route still
        # serves any other caller that reaches it directly for an
        # already-finished job exactly as it always has.
        q = job.try_subscribe(MAX_STREAM_SUBSCRIBERS_PER_JOB)
        if q is None:
            # #287: already at the concurrent-viewer cap for this job -
            # tell the client to fall back to polling instead of
            # opening a connection that would just make the thread
            # exhaustion this cap exists to prevent one connection
            # closer.
            def generate_busy():
                yield (
                    "event: busy\n"
                    "data: too many viewers already watching this run - "
                    "falling back to polling /run/status\n\n"
                )

            return Response(
                stream_with_context(generate_busy()),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        def generate():
            start = time.monotonic()
            try:
                while True:
                    remaining = MAX_STREAM_SECONDS - (time.monotonic() - start)
                    if remaining <= 0:
                        # #287: proactively end this response instead of
                        # ever letting one connection hold its thread
                        # indefinitely (see MAX_STREAM_SECONDS's own
                        # comment) - not an error, so EventSource
                        # reconnects on its own and picks up right where
                        # it left off via Job.subscribe()'s backlog
                        # replay.
                        return
                    try:
                        item = q.get(timeout=min(SSE_HEARTBEAT_SECONDS, remaining))
                    except queue.Empty:
                        # No new output in a while - send a keepalive
                        # comment instead of blocking forever. A closed
                        # browser socket makes the yield below raise
                        # (Werkzeug detects the write failure), which
                        # unwinds into the finally clause and
                        # unsubscribes - without this, a client that
                        # vanished mid-run (closed tab, dead wifi) would
                        # never unsubscribe and its queue would sit
                        # subscribed (bounded, but pointlessly) for the
                        # rest of the run.
                        yield ": keepalive\n\n"
                        continue
                    if item is DONE_SENTINEL:
                        yield f"event: done\ndata: {job.returncode}\n\n"
                        break
                    yield f"data: {redact(item)}\n\n"
            finally:
                job.unsubscribe(q)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/run/status")
    def run_status():
        return jsonify(app.job_manager.status() or {"state": "idle"})

    @app.get("/results")
    def results():
        watchlists = []
        if os.path.isdir(external_dir):
            for name in sorted(os.listdir(external_dir)):
                if name.endswith((".html", ".md")):
                    path = os.path.join(external_dir, name)
                    watchlists.append(
                        {
                            "name": name,
                            "mtime": datetime.fromtimestamp(os.path.getmtime(path)),
                        }
                    )
        # #288: "No watchlists generated yet" is the right message
        # before anyone has ever run anything, but was ALSO the only
        # message ever shown when a full/external run had already
        # completed and produced nothing - whether because an earlier
        # stage failed (stage_results), external itself failed, or
        # external ran and reported success while writing nothing (see
        # web/job_runner.py's external_produced_output). Passing the
        # last job's status dict lets results.html tell those apart
        # instead of always showing the same "run recommendations
        # first" text even right after a run that clearly did.
        return render_template(
            "results.html",
            watchlists=watchlists,
            logs=list_log_files(logs_dir),
            job=app.job_manager.status(),
        )

    @app.get("/results/watchlist/<path:filename>")
    def results_watchlist(filename):
        if not filename.endswith((".html", ".md")) or not os.path.isdir(external_dir):
            abort(404)
        # send_from_directory refuses path traversal on its own; the
        # extension check above is belt-and-suspenders since this only
        # ever serves generated watchlist output, not arbitrary files.
        response = send_from_directory(external_dir, filename)
        if filename.endswith(".html"):
            # Defense-in-depth on top of the escaping fix in
            # recommenders/external_render.py (TMDB-derived fields are
            # HTML-escaped at generation time now) - even a future gap
            # there shouldn't be able to turn into a script that can
            # drive this app's own state-changing endpoints.
            response.headers["Content-Security-Policy"] = WATCHLIST_CSP
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/results/log/<path:filename>")
    def results_log(filename):
        # #283: default view is still the last-500-lines tail (cheap,
        # fine for the common case of "what just happened") - `?view=full`
        # opts into read_log_full() instead, the only way to reach the
        # true START of a long run's output (cleanup_old_logs() retains
        # up to ~20MB per log, so the tail alone can genuinely never
        # reach it).
        full = request.args.get("view") == "full"
        try:
            if full:
                content, empty_reason, truncated = read_log_full(logs_dir, filename)
            else:
                content, empty_reason = read_log_tail(logs_dir, filename)
                truncated = False
        except FileNotFoundError:
            abort(404)
        return render_template(
            "log_view.html",
            filename=filename,
            content=content,
            empty_reason=empty_reason,
            full=full,
            truncated=truncated,
            max_bytes=LOG_VIEW_MAX_BYTES,
        )

    return app


def _wait_for_listening(port: int, timeout: float = 15.0) -> bool:
    """Poll 127.0.0.1:port until it accepts connections or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


# How long the post-update relaunch (see web/update_apply.py's
# _relaunch_ui) is willing to retry binding the port if it's still
# held by the just-terminated old server (e.g. a brief TIME_WAIT-style
# OS delay between that process exiting and the socket actually being
# free) - without this, "never leave a dead port" would depend on OS
# timing the update worker doesn't control.
BIND_RETRY_ATTEMPTS = 20
BIND_RETRY_DELAY_SECONDS = 0.5


def _skip_slow_server_name_lookup() -> None:
    """Werkzeug's own BaseWSGIServer.server_bind() calls
    socket.getfqdn(host) to set self.server_name - a reverse DNS lookup
    that's irrelevant here (this app only ever binds 127.0.0.1 - see
    main()'s docstring) but confirmed, via real end-to-end self-update
    testing (see this repo's v2.8.29 PR description), to take 30+
    seconds on some networks. That delay eats directly into the
    self-update hand-off script's own health-check window (see
    utils/self_update_handoff.py's HANDOFF_HEALTH_TIMEOUT_SECONDS) -
    a perfectly good just-installed update could get spuriously rolled
    back simply because ITS OWN server took too long to finish binding,
    not because anything was actually wrong with it. Patches
    socket.getfqdn globally (idempotent - safe to call more than once)
    rather than subclassing the server Flask constructs internally,
    since Flask's own app.run() doesn't expose a server class hook."""
    import socket as _socket

    if getattr(_socket.getfqdn, "_curatarr_fast_path", False):
        return

    _real_getfqdn = _socket.getfqdn

    def _fast_getfqdn(name=""):
        if not name or name in ("127.0.0.1", "localhost", "::1"):
            return "localhost"
        return _real_getfqdn(name)

    _fast_getfqdn._curatarr_fast_path = True  # type: ignore[attr-defined]  # deliberate marker attribute on a plain function, re-entrancy guard for the check above
    _socket.getfqdn = _fast_getfqdn


def _run_with_bind_retry(app: Flask, host: str, port: int) -> None:
    """Wraps app.run() with a short bind-retry loop - see
    BIND_RETRY_ATTEMPTS above. app.run() blocks for the life of a
    successful bind (returning only on shutdown), so a retry loop
    around it only ever actually iterates on an immediate bind failure,
    never once the server is actually up and serving."""
    _skip_slow_server_name_lookup()
    for attempt in range(BIND_RETRY_ATTEMPTS):
        try:
            app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
            return
        except OSError:
            if attempt == BIND_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(BIND_RETRY_DELAY_SECONDS)


def main():
    """Launcher entry point - see run-ui.sh / run-ui.ps1.

    Starts Flask bound to 127.0.0.1 only, and opens the browser once the
    server is actually accepting connections (not on a fixed timer).
    """
    port = int(os.environ.get("CURATARR_UI_PORT", DEFAULT_PORT))
    app = create_app()

    # #264: started here (main()), never inside create_app() itself -
    # every test/caller that only calls create_app() directly (nearly
    # all of them) must never spawn a real background thread. Daemon
    # thread (see SchedulerThread) - no explicit stop() wired into
    # shutdown below; a scheduled run already in flight is killed the
    # same way any other run is (JobManager.terminate_running(), right
    # below - the scheduler and the web UI share one JobManager).
    app.scheduler_thread = SchedulerThread(app.job_manager, app.load_config_cached)
    app.scheduler_thread.start()

    # H3: a server shutdown (Ctrl+C, SIGTERM from a process manager, or
    # a clean interpreter exit) must never leave an orphaned recommender
    # subprocess running in the background - it would keep mutating
    # caches/Plex collections while a freshly-started server could try
    # to launch a new run at the same time. Covers both a graceful exit
    # (atexit) and a signal-driven one; JobManager.terminate_running()
    # is a no-op if nothing is running.
    atexit.register(app.job_manager.terminate_running)

    def _handle_shutdown_signal(signum, frame):
        app.job_manager.terminate_running()
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_shutdown_signal)
        except (ValueError, OSError):
            # signal.signal() only works on the main thread, and not
            # every signal is available on every platform - atexit above
            # still covers a normal interpreter shutdown either way.
            pass

    # Skipped when this is a post-"Update now" relaunch (see
    # web/update_apply.py's _relaunch_ui) - the user's existing browser
    # tab is already open and will reload itself once /healthz comes
    # back, so auto-opening a second one here would just be an
    # unexpected extra tab popping up after an update.
    if os.environ.get("CURATARR_SKIP_BROWSER_OPEN") != "1":

        def _open_when_ready():
            if _wait_for_listening(port):
                webbrowser.open(f"http://127.0.0.1:{port}/")

        threading.Thread(target=_open_when_ready, daemon=True).start()

    # 127.0.0.1 ONLY - never 0.0.0.0. threaded=True so the SSE stream
    # doesn't block other requests (dashboard/results while a run is
    # live). See _run_with_bind_retry for why this isn't a bare
    # app.run() call.
    _run_with_bind_retry(app, "127.0.0.1", port)


if __name__ == "__main__":
    main()
