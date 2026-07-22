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
"""

import os
import socket
import threading
import time
import webbrowser
from datetime import datetime

from flask import (
    Flask, Response, abort, jsonify, redirect, render_template,
    request, send_from_directory, stream_with_context, url_for,
)

from utils import get_project_root, get_users_from_config, load_config

from .job_runner import DONE_SENTINEL, JobAlreadyRunningError, JobError, JobManager
from .security import redact
from .status import get_last_run_status, list_log_files, read_log_tail

DEFAULT_PORT = 8787


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

    def _load_users():
        config_path = os.path.join(project_root, 'config', 'config.yml')
        try:
            config = load_config(config_path)
        except Exception:
            return []
        return get_users_from_config(config)

    @app.get('/')
    def dashboard():
        rows = [
            {'username': user, **get_last_run_status(logs_dir, user)}
            for user in _load_users()
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
                    item = q.get()
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
        return send_from_directory(external_dir, filename)

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

    def _open_when_ready():
        if _wait_for_listening(port):
            webbrowser.open(f'http://127.0.0.1:{port}/')

    threading.Thread(target=_open_when_ready, daemon=True).start()

    # 127.0.0.1 ONLY - never 0.0.0.0. threaded=True so the SSE stream
    # doesn't block other requests (dashboard/results while a run is live).
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
