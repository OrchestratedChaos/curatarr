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
"""

import atexit
import os
import queue
import signal
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime

from flask import (
    Flask, Response, abort, jsonify, redirect, render_template,
    request, send_from_directory, stream_with_context, url_for,
)
from flask.testing import FlaskClient
from werkzeug.datastructures import Headers

from utils import __version__, get_project_root, get_update_mode, get_users_from_config, load_config, update_available

from .config_app import register_config_routes
from .job_runner import DONE_SENTINEL, JobAlreadyRunningError, JobError, JobManager
from .security import redact, register_origin_host_guard
from .status import find_user_watchlist, get_last_run_status, list_log_files, read_log_tail
from .update_apply import (
    UpdateAlreadyInProgressError,
    UpdateManager,
    UpdateNotAvailableError,
)

DEFAULT_PORT = 8787

# Cookie used to persist a per-version dismissal of the update banner
# (see create_app()'s _update_banner_context / update_dismiss). One
# year is effectively "until the next release the user hasn't seen",
# since dismissal is keyed to the specific 'latest' version string.
UPDATE_DISMISS_COOKIE = 'curatarr_update_dismissed'
UPDATE_DISMISS_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

# How long the SSE stream waits for a new line before sending a
# keepalive comment - see run_stream()'s generate().
SSE_HEARTBEAT_SECONDS = 15.0

# Applied to served watchlist HTML (see results_watchlist()). Primary
# XSS defense is escaping at generation time (recommenders/
# external_output.py); this is defense-in-depth so that even a gap
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
        headers = Headers(kwargs.pop('headers', None))
        if 'Origin' not in headers:
            headers['Origin'] = 'http://localhost'
        kwargs['headers'] = headers
        return super().open(*args, **kwargs)


def create_app(project_root: str = None) -> Flask:
    """Application factory. project_root is overridable so tests can
    point the app at a throwaway fixture repo instead of the real one.
    """
    project_root = project_root or get_project_root()
    logs_dir = os.path.join(project_root, 'logs')
    external_dir = os.path.join(project_root, 'recommendations', 'external')

    app = Flask(__name__)
    app.config['PROJECT_ROOT'] = project_root
    app.config['LOGS_DIR'] = logs_dir
    app.config['EXTERNAL_DIR'] = external_dir
    app.job_manager = JobManager(project_root, logs_dir)
    app.update_manager = UpdateManager(project_root, logs_dir)
    app.test_client_class = _BrowserLikeTestClient

    register_origin_host_guard(app)
    register_config_routes(app)

    def _load_config():
        config_path = os.path.join(project_root, 'config', 'config.yml')
        try:
            return load_config(config_path)
        except Exception:
            return None

    def _load_users():
        config = _load_config()
        return get_users_from_config(config) if config else []

    @app.context_processor
    def _update_banner_context():
        """Injected into every rendered template (see base.html's
        dismissible banner) so update state doesn't need to be threaded
        through every route individually - this covers config_app.py's
        routes too since they render through this same Flask app.

        Fails open just like utils.update_check: any exception here
        (config missing/unreadable, network error, whatever) just means
        no banner, never a 500 - a broken update check must never break
        normal page rendering.
        """
        try:
            config = _load_config()
            # No config at all (missing/unreadable) is already a degraded
            # state the app can't really run normally in - skip the check
            # rather than defaulting to 'notify', which would mean an
            # HTTP call on every single page render for an install that
            # can't even load its config yet.
            update_mode = get_update_mode(config) if config else 'off'
            if update_mode == 'off':
                return {'update_banner': None}
            latest, current, is_newer = update_available(update_mode=update_mode)
            if not is_newer:
                return {'update_banner': None}
            # Dismissal is per-version: bumping to a newer release than
            # the one that was dismissed shows the banner again.
            if request.cookies.get(UPDATE_DISMISS_COOKIE) == latest:
                return {'update_banner': None}
            return {
                'update_banner': {
                    'latest': latest,
                    'current': current,
                    'frozen': getattr(sys, 'frozen', False),
                    # Docker images update via `docker pull`, not this
                    # banner's "Update now" button (see
                    # web/update_apply.py's UpdateManager.begin_update
                    # RUNNING_IN_DOCKER gate, which refuses that button
                    # anyway) - base.html renders a pull-the-new-image
                    # instruction instead of the button when this is set.
                    'docker': os.environ.get('RUNNING_IN_DOCKER') == 'true',
                }
            }
        except Exception:
            return {'update_banner': None}

    @app.post('/update/dismiss')
    def update_dismiss():
        """Persist a per-version banner dismissal as a cookie (no server-
        side state needed - this is purely a display preference, not a
        security-relevant setting). Redirects back to wherever the
        dismiss button was clicked from."""
        version = request.form.get('version', '')
        next_url = request.form.get('next') or url_for('dashboard')
        # Only ever redirect to a same-app relative path - never let an
        # attacker-controlled 'next' turn this into an open redirect.
        if not next_url.startswith('/') or next_url.startswith('//'):
            next_url = url_for('dashboard')
        response = redirect(next_url, code=303)
        if version:
            response.set_cookie(
                UPDATE_DISMISS_COOKIE, version,
                max_age=UPDATE_DISMISS_COOKIE_MAX_AGE,
                httponly=True, samesite='Lax',
            )
        return response

    @app.post('/update/apply')
    def update_apply_route():
        """"Update now": source installs verify a newer signed release
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
            return jsonify({'error': 'A recommender run is currently in progress - wait for it to finish before updating.'}), 409

        host = '127.0.0.1'
        port = int(os.environ.get('CURATARR_UI_PORT', DEFAULT_PORT))
        try:
            tag = app.update_manager.begin_update(host, port)
        except UpdateAlreadyInProgressError as exc:
            return jsonify({'error': str(exc)}), 409
        except UpdateNotAvailableError as exc:
            return jsonify({'error': str(exc)}), 404
        return jsonify({'status': 'started', 'tag': tag}), 202

    @app.get('/healthz')
    def healthz():
        """Unauthenticated-by-design (matches every other GET route -
        this app has no auth boundary beyond binding 127.0.0.1 and the
        origin/host guard, and a version number isn't sensitive).
        Polled by base.html's "Update now" flow to detect the server
        coming back up after a restart, and by whatever launches it
        (see _wait_for_listening) as a liveness probe."""
        return jsonify({'version': __version__})

    @app.get('/')
    def dashboard():
        config = _load_config()
        rows = [
            {
                'username': user,
                **get_last_run_status(logs_dir, user),
                'watchlist_file': find_user_watchlist(external_dir, config, user),
            }
            for user in (get_users_from_config(config) if config else [])
        ]
        return render_template('dashboard.html', rows=rows, job=app.job_manager.status())

    @app.get('/run')
    def run_form():
        return render_template(
            'run.html',
            users=_load_users(),
            job=app.job_manager.status(),
            running=app.job_manager.is_running(),
            error=request.args.get('error'),
        )

    @app.post('/run')
    def run_trigger():
        engine = request.form.get('engine', 'full')
        user = request.form.get('user', 'all')
        try:
            app.job_manager.start(engine, user, _load_users())
        except JobAlreadyRunningError:
            return redirect(url_for('run_form', error='busy'), code=303)
        except JobError as exc:
            return redirect(url_for('run_form', error=str(exc)), code=303)
        return redirect(url_for('run_form'), code=303)

    @app.get('/run/stream')
    def run_stream():
        job = app.job_manager.current_job()
        if job is None:
            abort(404)

        def generate():
            q = job.subscribe()
            try:
                while True:
                    try:
                        item = q.get(timeout=SSE_HEARTBEAT_SECONDS)
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
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )

    @app.get('/run/status')
    def run_status():
        return jsonify(app.job_manager.status() or {'state': 'idle'})

    @app.get('/results')
    def results():
        watchlists = []
        if os.path.isdir(external_dir):
            for name in sorted(os.listdir(external_dir)):
                if name.endswith(('.html', '.md')):
                    path = os.path.join(external_dir, name)
                    watchlists.append({
                        'name': name,
                        'mtime': datetime.fromtimestamp(os.path.getmtime(path)),
                    })
        return render_template('results.html', watchlists=watchlists, logs=list_log_files(logs_dir))

    @app.get('/results/watchlist/<path:filename>')
    def results_watchlist(filename):
        if not filename.endswith(('.html', '.md')) or not os.path.isdir(external_dir):
            abort(404)
        # send_from_directory refuses path traversal on its own; the
        # extension check above is belt-and-suspenders since this only
        # ever serves generated watchlist output, not arbitrary files.
        response = send_from_directory(external_dir, filename)
        if filename.endswith('.html'):
            # Defense-in-depth on top of the escaping fix in
            # recommenders/external_output.py (TMDB-derived fields are
            # HTML-escaped at generation time now) - even a future gap
            # there shouldn't be able to turn into a script that can
            # drive this app's own state-changing endpoints.
            response.headers['Content-Security-Policy'] = WATCHLIST_CSP
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/results/log/<path:filename>')
    def results_log(filename):
        try:
            tail = read_log_tail(logs_dir, filename)
        except FileNotFoundError:
            abort(404)
        return render_template('log_view.html', filename=filename, content=tail)

    return app


def _wait_for_listening(port: int, timeout: float = 15.0) -> bool:
    """Poll 127.0.0.1:port until it accepts connections or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(('127.0.0.1', port)) == 0:
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

    if getattr(_socket.getfqdn, '_curatarr_fast_path', False):
        return

    _real_getfqdn = _socket.getfqdn

    def _fast_getfqdn(name=''):
        if not name or name in ('127.0.0.1', 'localhost', '::1'):
            return 'localhost'
        return _real_getfqdn(name)

    _fast_getfqdn._curatarr_fast_path = True
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
    port = int(os.environ.get('CURATARR_UI_PORT', DEFAULT_PORT))
    app = create_app()

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
    if os.environ.get('CURATARR_SKIP_BROWSER_OPEN') != '1':
        def _open_when_ready():
            if _wait_for_listening(port):
                webbrowser.open(f'http://127.0.0.1:{port}/')

        threading.Thread(target=_open_when_ready, daemon=True).start()

    # 127.0.0.1 ONLY - never 0.0.0.0. threaded=True so the SSE stream
    # doesn't block other requests (dashboard/results while a run is
    # live). See _run_with_bind_retry for why this isn't a bare
    # app.run() call.
    _run_with_bind_retry(app, '127.0.0.1', port)


if __name__ == '__main__':
    main()
