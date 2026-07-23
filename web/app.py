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

from utils import get_project_root, get_users_from_config, load_config

from .config_app import register_config_routes
from .job_runner import DONE_SENTINEL, JobAlreadyRunningError, JobError, JobManager
from .security import redact, register_origin_host_guard
from .status import find_user_watchlist, get_last_run_status, list_log_files, read_log_tail

DEFAULT_PORT = 8787

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

    def _open_when_ready():
        if _wait_for_listening(port):
            webbrowser.open(f'http://127.0.0.1:{port}/')

    threading.Thread(target=_open_when_ready, daemon=True).start()

    # 127.0.0.1 ONLY - never 0.0.0.0. threaded=True so the SSE stream
    # doesn't block other requests (dashboard/results while a run is live).
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
