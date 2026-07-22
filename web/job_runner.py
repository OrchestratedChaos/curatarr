"""Subprocess job runner for triggering curatarr recommendation runs
from the web UI.

Runs are always subprocesses, never in-process imports. The recommender
entry points (recommenders/movie.py, tv.py, external.py) hijack
sys.stdout with a TeeLogger and call sys.exit() - fine for a
short-lived CLI invocation, unsafe inside a long-running Flask process.

Only one job may run at a time (a run mutates shared caches under
cache/ and Plex collections), enforced by JobManager's lock.
"""

import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from typing import Dict, List, Optional

ENGINES = ('full', 'movie', 'tv', 'external')

# Sentinel pushed onto subscriber queues when a job finishes, so SSE
# consumers know to stop waiting for more output.
DONE_SENTINEL = object()


class JobError(Exception):
    """Raised for invalid job requests (bad engine/user, etc)."""


class JobAlreadyRunningError(JobError):
    """Raised when a run is requested while another run is in progress."""


class Job:
    """State for a single triggered run. Construct via JobManager.start()."""

    def __init__(self, engine: str, user: str, cmd: List[str], log_path: str):
        self.engine = engine
        self.user = user
        self.cmd = cmd
        self.log_path = log_path
        self.started_at = datetime.now()
        self.finished_at: Optional[datetime] = None
        self.returncode: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None

        self._data_lock = threading.Lock()
        self.lines: List[str] = []
        self._subscribers: List["queue.Queue"] = []

    @property
    def state(self) -> str:
        if self.returncode is None:
            return 'running'
        return 'succeeded' if self.returncode == 0 else 'failed'

    def _append_line(self, line: str) -> None:
        with self._data_lock:
            self.lines.append(line)
            for q in self._subscribers:
                q.put(line)

    def _finish(self, returncode: int) -> None:
        with self._data_lock:
            self.returncode = returncode
            self.finished_at = datetime.now()
            for q in self._subscribers:
                q.put(DONE_SENTINEL)

    def subscribe(self) -> "queue.Queue":
        """Register a new SSE listener.

        Returns a queue pre-loaded with any output already produced, so
        a browser tab that connects mid-run still sees the backlog
        before live lines start arriving.
        """
        q: "queue.Queue" = queue.Queue()
        with self._data_lock:
            for line in self.lines:
                q.put(line)
            if self.returncode is not None:
                q.put(DONE_SENTINEL)
            else:
                self._subscribers.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue") -> None:
        with self._data_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def to_dict(self) -> Dict:
        return {
            'engine': self.engine,
            'user': self.user,
            'state': self.state,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'returncode': self.returncode,
            'log_file': os.path.basename(self.log_path),
        }


class JobManager:
    """Owns the single-run lock and launches recommender subprocesses."""

    def __init__(self, project_root: str, logs_dir: str):
        self.project_root = project_root
        self.logs_dir = logs_dir
        self._lock = threading.Lock()
        self._current: Optional[Job] = None

    def status(self) -> Optional[Dict]:
        job = self._current
        return job.to_dict() if job else None

    def current_job(self) -> Optional[Job]:
        return self._current

    def is_running(self) -> bool:
        job = self._current
        return job is not None and job.state == 'running'

    def start(self, engine: str, user: str, allowed_users: List[str]) -> Job:
        """Validate and launch a run. Raises JobError/JobAlreadyRunningError."""
        if engine not in ENGINES:
            raise JobError(f"Unknown engine: {engine}")
        if user != 'all' and user not in allowed_users:
            raise JobError(f"Unknown user: {user}")
        if engine in ('full', 'external') and user != 'all':
            raise JobError(f"The '{engine}' engine does not support a single-user run")

        with self._lock:
            if self.is_running():
                raise JobAlreadyRunningError("A run is already in progress")

            cmd, env, log_name = self._build_command(engine, user)
            os.makedirs(self.logs_dir, exist_ok=True)
            log_path = os.path.join(self.logs_dir, log_name)

            job = Job(engine, user, cmd, log_path)
            process = subprocess.Popen(
                cmd,
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            job.process = process
            self._current = job

            thread = threading.Thread(target=self._pump, args=(job,), daemon=True)
            thread.start()
            return job

    def _build_command(self, engine: str, user: str):
        """Build the subprocess argv, environment, and job log filename.

        Mirrors run.sh's own invocations (python3 recommenders/<x>.py
        [username] [--debug]) so the UI-triggered run behaves exactly
        like a normal cron/manual run.
        """
        env = dict(os.environ)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')

        if engine == 'full':
            if os.name == 'nt':
                cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-File',
                       os.path.join(self.project_root, 'run.ps1')]
            else:
                cmd = ['bash', os.path.join(self.project_root, 'run.sh')]
            # Skip the interactive setup wizard / auto-update git-checkout
            # dance for UI-triggered runs - config is assumed already
            # set up, same bypass run.sh already supports for Docker.
            env['RUNNING_IN_DOCKER'] = 'true'
            target = 'all'
        elif engine in ('movie', 'tv'):
            script = os.path.join(self.project_root, 'recommenders', f'{engine}.py')
            cmd = [sys.executable, script]
            if user != 'all':
                cmd.append(user)
            target = user
        elif engine == 'external':
            script = os.path.join(self.project_root, 'recommenders', 'external.py')
            cmd = [sys.executable, script]
            target = 'all'
        else:  # pragma: no cover - guarded by start()'s validation above
            raise JobError(f"Unknown engine: {engine}")

        log_name = f'webui_{engine}_{target}_{ts}.log'
        return cmd, env, log_name

    def _pump(self, job: Job) -> None:
        """Background thread: read subprocess output, tee to a log file
        and to every SSE subscriber, then record the exit code."""
        log_file = open(job.log_path, 'w', encoding='utf-8')
        returncode = -1
        try:
            assert job.process is not None and job.process.stdout is not None
            for line in job.process.stdout:
                log_file.write(line)
                log_file.flush()
                job._append_line(line.rstrip('\n'))
            returncode = job.process.wait()
        except Exception as exc:
            # Subprocess plumbing failure (e.g. couldn't read pipe), not
            # a recommender-level error - surface it in the live output.
            job._append_line(f'[web UI] job runner error: {exc}')
        finally:
            log_file.close()
            job._finish(returncode)
