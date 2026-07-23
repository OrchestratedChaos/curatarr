"""Subprocess job runner for triggering curatarr recommendation runs
from the web UI.

Runs are always subprocesses, never in-process imports. The recommender
entry points (recommenders/movie.py, tv.py, external.py) hijack
sys.stdout with a TeeLogger and call sys.exit() - fine for a
short-lived CLI invocation, unsafe inside a long-running Flask process.

Only one job may run at a time (a run mutates shared caches under
cache/ and Plex collections), enforced by JobManager's lock (in-process)
and a PID lockfile (cross-process - see _foreign_run_in_progress).

Frozen (PyInstaller onefile) binary note: `sys.executable
recommenders/<x>.py` doesn't exist once packaged - there is no
`recommenders/` directory alongside the exe, and re-invoking
`sys.executable` just relaunches the UI. When running frozen,
_build_command instead re-invokes the packaged exe itself with
`--run-recommender <engine> [user]`, which curatarr_app.py's dispatcher
(see that module's docstring) recognizes and runs the requested
recommender in-process, in that *separate* subprocess - never inside
this long-lived Flask server process itself, so the stdout-hijacking/
sys.exit() behavior above stays safe.
"""

import os
import queue
import signal
import subprocess
import sys
import threading
from datetime import datetime
from typing import Dict, List, Optional

ENGINES = ('full', 'movie', 'tv', 'external')

# Sentinel pushed onto subscriber queues when a job finishes, so SSE
# consumers know to stop waiting for more output.
DONE_SENTINEL = object()

# Caps how many items a single SSE subscriber's queue can hold. Without
# a bound, a subscriber whose browser tab closed (or whose socket died)
# without its generator's `finally: unsubscribe()` running yet - see
# web/app.py's run_stream() - could have _append_line() pile lines into
# its queue for the rest of a long run with nothing ever reading them
# back out, growing without limit. Once full, the oldest queued item is
# dropped to make room for the newest (see _safe_queue_put).
SUBSCRIBER_QUEUE_MAXSIZE = 2000

# PID lockfile written for the duration of a run (in logs_dir, next to
# the run's own log file). Exists so a *different* curatarr process -
# e.g. a fresh server started after the previous one was killed without
# a clean shutdown - can detect and refuse to race an in-flight run it
# has no in-memory record of. The in-process JobManager._lock/_current
# state is authoritative for this process; the lockfile is the
# cross-process backstop.
LOCK_FILENAME = 'webui_job.lock'


class JobError(Exception):
    """Raised for invalid job requests (bad engine/user, etc)."""


class JobAlreadyRunningError(JobError):
    """Raised when a run is requested while another run is in progress."""


def _safe_queue_put(q: "queue.Queue", item) -> None:
    """put() onto a bounded subscriber queue without ever blocking the
    pump thread or growing without bound. Drops the oldest queued item
    to make room if full - a slow or disconnected SSE subscriber must
    never be able to stall a run or leak memory (see
    SUBSCRIBER_QUEUE_MAXSIZE)."""
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            pass  # pathological race under concurrent producers - drop it


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness probe for a PID recorded in the lockfile."""
    if pid <= 0:
        return False
    if os.name == 'nt':
        try:
            result = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}'],
                capture_output=True, text=True, timeout=3,
            )
            return str(pid) in result.stdout
        except Exception:
            # Can't confirm either way - fail toward "still running" so
            # we serialize a possible in-flight run rather than race it.
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    except OSError:
        return False


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
                _safe_queue_put(q, line)

    def _finish(self, returncode: int) -> None:
        with self._data_lock:
            self.returncode = returncode
            self.finished_at = datetime.now()
            for q in self._subscribers:
                _safe_queue_put(q, DONE_SENTINEL)

    def subscribe(self) -> "queue.Queue":
        """Register a new SSE listener.

        Returns a queue pre-loaded with any output already produced, so
        a browser tab that connects mid-run still sees the backlog
        before live lines start arriving.
        """
        q: "queue.Queue" = queue.Queue(maxsize=SUBSCRIBER_QUEUE_MAXSIZE)
        with self._data_lock:
            for line in self.lines:
                _safe_queue_put(q, line)
            if self.returncode is not None:
                _safe_queue_put(q, DONE_SENTINEL)
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

    def _lock_path(self) -> str:
        return os.path.join(self.logs_dir, LOCK_FILENAME)

    def _write_lock(self, pid: int) -> None:
        try:
            os.makedirs(self.logs_dir, exist_ok=True)
            with open(self._lock_path(), 'w', encoding='utf-8') as f:
                f.write(str(pid))
        except OSError:
            pass  # best-effort - in-process state is still authoritative here

    def _remove_lock(self) -> None:
        try:
            os.remove(self._lock_path())
        except OSError:
            pass

    def _foreign_run_in_progress(self) -> bool:
        """True if a lockfile left by a *different* process points at a
        PID that's still alive - i.e. a run this JobManager instance has
        no in-memory record of (its own server process was restarted
        without a clean shutdown) but that's still actually executing."""
        try:
            with open(self._lock_path(), 'r', encoding='utf-8') as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return False
        if pid == os.getpid():
            return False
        if _pid_alive(pid):
            return True
        self._remove_lock()  # stale - that process/child is gone
        return False

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
            if self._foreign_run_in_progress():
                raise JobAlreadyRunningError(
                    "A run started by a previous server process is still in progress"
                )

            cmd, env, log_name = self._build_command(engine, user)
            os.makedirs(self.logs_dir, exist_ok=True)
            log_path = os.path.join(self.logs_dir, log_name)

            job = Job(engine, user, cmd, log_path)
            popen_kwargs = dict(
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=env,
            )
            if os.name != 'nt':
                # Own session/process group so a server shutdown (see
                # JobManager.terminate_running) can kill the whole tree
                # in one shot - matters for the 'full' engine, whose
                # run.sh itself spawns movie.py/tv.py/external.py as
                # further children, not just the immediate bash process.
                popen_kwargs['start_new_session'] = True
            else:
                # Suppress the child's own console window - matters for
                # the windowed (console=False, see curatarr.spec) build:
                # without this, a console-subsystem child (powershell.exe
                # for the 'full' engine on a source install, or the
                # re-invoked frozen exe itself) would otherwise flash a
                # console window even though stdout/stderr are already
                # piped back to this process. getattr(...) default keeps
                # this importable/testable on non-Windows (the attribute
                # only exists in the subprocess module on win32).
                popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            try:
                process = subprocess.Popen(cmd, **popen_kwargs)
            except OSError as exc:
                # M3: a missing interpreter/shell (bash, powershell, or
                # even sys.executable itself in some broken install) must
                # surface as a normal, friendly JobError - the /run route
                # already turns that into a redirect with an error
                # message - not an unhandled 500.
                raise JobError(f"Could not start the {engine} run: {exc}") from exc

            job.process = process
            self._current = job
            self._write_lock(process.pid)

            thread = threading.Thread(target=self._pump, args=(job,), daemon=True)
            thread.start()
            return job

    def _build_command(self, engine: str, user: str):
        """Build the subprocess argv, environment, and job log filename.

        Source install: mirrors run.sh's own invocations (python3
        recommenders/<x>.py [username] [--debug]) so the UI-triggered
        run behaves exactly like a normal cron/manual run.

        Frozen (PyInstaller) binary: recommenders/<x>.py doesn't exist
        on disk, so this re-invokes the packaged exe itself with
        `--run-recommender <engine> [user]` - see curatarr_app.py's
        dispatcher and this module's docstring.
        """
        env = dict(os.environ)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        frozen = getattr(sys, 'frozen', False)

        if engine == 'full':
            if frozen:
                cmd = [sys.executable, '--run-recommender', 'full']
            elif os.name == 'nt':
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
            if frozen:
                cmd = [sys.executable, '--run-recommender', engine]
            else:
                script = os.path.join(self.project_root, 'recommenders', f'{engine}.py')
                cmd = [sys.executable, script]
            if user != 'all':
                cmd.append(user)
            target = user
        elif engine == 'external':
            if frozen:
                cmd = [sys.executable, '--run-recommender', 'external']
            else:
                script = os.path.join(self.project_root, 'recommenders', 'external.py')
                cmd = [sys.executable, script]
            target = 'all'
        else:  # pragma: no cover - guarded by start()'s validation above
            raise JobError(f"Unknown engine: {engine}")

        log_name = f'webui_{engine}_{target}_{ts}.log'
        return cmd, env, log_name

    def _pump(self, job: Job) -> None:
        """Background thread: read subprocess output, tee to a log file
        and to every SSE subscriber, then record the exit code.

        returncode stays None until the read loop finishes normally and
        Popen.wait() returns the real exit code. If anything above
        raises first (the log file couldn't be opened, the read loop
        itself raised, etc.) returncode is never set to a real value, so
        job._finish() below reports a synthetic failure (-1) rather than
        "succeeded" - a pump-level failure means the run's output/log
        wasn't reliably captured either way, and (this is what actually
        matters operationally) job._finish() is now *always* reached
        even on that path, so the job never gets stuck "running"
        forever and the single-run lock is always released.
        """
        log_file = None
        returncode: Optional[int] = None
        try:
            log_file = open(job.log_path, 'w', encoding='utf-8')
            assert job.process is not None and job.process.stdout is not None
            for line in job.process.stdout:
                log_file.write(line)
                log_file.flush()
                job._append_line(line.rstrip('\n'))
            returncode = job.process.wait()
        except Exception as exc:
            # Subprocess plumbing failure (e.g. couldn't open the log
            # file, couldn't read the pipe), not a recommender-level
            # error - surface it in the live output.
            job._append_line(f'[web UI] job runner error: {exc}')
        finally:
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    pass
            # Always reap the child, even on the failure path above -
            # otherwise a log-open failure (or any other exception
            # raised before Popen.wait() ran) leaves it a zombie.
            if job.process is not None and job.process.poll() is None:
                try:
                    job.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    job.process.kill()
                    job.process.wait()
            self._remove_lock()
            job._finish(returncode if returncode is not None else -1)

    def terminate_running(self) -> None:
        """Best-effort: terminate the in-flight subprocess (and its
        whole process group on POSIX, since it's launched with
        start_new_session=True) so a server shutdown never leaves an
        orphaned recommender run mutating caches/Plex collections in
        the background while a fresh server process might start a new
        one. Safe to call with no run in progress; see web/app.py's
        atexit/SIGTERM/SIGINT registration in main().
        """
        job = self._current
        if job is None or job.process is None:
            return
        if job.process.poll() is not None:
            return
        try:
            if os.name == 'nt':
                job.process.terminate()
            else:
                os.killpg(os.getpgid(job.process.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        self._remove_lock()
