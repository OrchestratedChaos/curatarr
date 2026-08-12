# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Subprocess job runner for triggering curatarr recommendation runs
from the web UI.

Runs are always subprocesses, never in-process imports. The recommender
entry points (recommenders/movie.py, tv.py, external.py) hijack
sys.stdout with a TeeLogger and call sys.exit() - fine for a
short-lived CLI invocation, unsafe inside a long-running Flask process.

Only one job may run at a time (a run mutates shared caches under
cache/ and Plex collections), enforced by JobManager's lock (in-process)
and a PID lockfile (cross-process - see _foreign_run_in_progress).
Neither of those is cross-container-aware (PIDs aren't even comparable
across two containers' separate PID namespaces) - see utils/run_lock.py
for the actual fix covering docker-compose.yml's two services, wired in
below (POSIX only; a no-op on Windows, see that module's docstring).

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

import logging
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
from datetime import datetime
from typing import Dict, List, Optional

from utils.helpers import get_code_root, no_window_kwargs
from utils.run_lock import PosixRunLock, run_lock_path

from .security import redact

logger = logging.getLogger("curatarr")

ENGINES = ("full", "movie", "tv", "external")

# Prefix for the per-stage result lines the Docker `full` engine's
# generated bash script (_build_docker_full_script) writes to stdout -
# "__CURATARR_STAGE__:<stage>:<returncode-or-skipped>" - so _pump() can
# tell a stage that never ran apart from one that ran and failed,
# something the raw log/output alone doesn't otherwise convey (#282/
# #288 - see _build_docker_full_script's docstring).
STAGE_MARKER_PREFIX = "__CURATARR_STAGE__"
_STAGE_MARKER_RE = re.compile(re.escape(STAGE_MARKER_PREFIX) + r":(\w+):(\S+)$")

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
LOCK_FILENAME = "webui_job.lock"


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
            logger.debug("_safe_queue_put: queue was full but emptied itself under race - nothing to drop")
        try:
            q.put_nowait(item)
        except queue.Full:
            pass  # pathological race under concurrent producers - drop it


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness probe for a PID recorded in the lockfile."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=3,
                **no_window_kwargs(),  # type: ignore[call-overload]  # mypy can't resolve subprocess.run's overloads against a **dict splat
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


def _build_docker_full_script(py: str, movie_script: str, tv_script: str, external_script: str) -> str:
    """Build the `bash -c` script body for the Docker `full` engine:
    movie -> tv -> external, explicitly gated and labeled (#282/#288 -
    see the call site in _build_command's own comment for the full
    history this replaces).

    Semantics (deliberately identical to the pre-existing `&&` chain,
    and to docker-entrypoint.sh's own `recommend full` under `set -e` -
    both verified in a real container, not just read): if movie exits
    non-zero, tv and external never run at all; if tv exits non-zero,
    external never runs. Either way the script's own exit code is that
    failing stage's returncode, so Job.state ("succeeded"/"failed") is
    unchanged from today. What's added is purely observability: an
    "=== X ===" banner per stage (matching docker-entrypoint.sh's own
    convention) - including an explicit "(skipped: ... failed)" banner
    for whichever stage(s) never ran, so the raw log itself says why,
    not just silence - plus an f"{STAGE_MARKER_PREFIX}:<stage>:<result>"
    line per stage (result is the stage's returncode, or the literal
    "skipped") that _pump() parses into Job.stage_results.

    py/movie_script/tv_script/external_script are all already
    shlex.quote()'d by the caller - kept as plain str params (not
    re-quoted here) so a test can assert on the exact paths/executable
    it passed in.
    """
    return (
        "set +e\n"
        "echo '=== Movie recommendations ==='\n"
        f"{py} {movie_script}\n"
        "MOVIE_RC=$?\n"
        f'echo "{STAGE_MARKER_PREFIX}:movie:$MOVIE_RC"\n'
        'if [ "$MOVIE_RC" -ne 0 ]; then\n'
        "  echo '=== TV recommendations === (skipped: movie recommendations failed)'\n"
        f"  echo '{STAGE_MARKER_PREFIX}:tv:skipped'\n"
        "  echo '=== External watchlists === (skipped: movie recommendations failed)'\n"
        f"  echo '{STAGE_MARKER_PREFIX}:external:skipped'\n"
        '  exit "$MOVIE_RC"\n'
        "fi\n"
        "echo '=== TV recommendations ==='\n"
        f"{py} {tv_script}\n"
        "TV_RC=$?\n"
        f'echo "{STAGE_MARKER_PREFIX}:tv:$TV_RC"\n'
        'if [ "$TV_RC" -ne 0 ]; then\n'
        "  echo '=== External watchlists === (skipped: TV recommendations failed)'\n"
        f"  echo '{STAGE_MARKER_PREFIX}:external:skipped'\n"
        '  exit "$TV_RC"\n'
        "fi\n"
        "echo '=== External watchlists ==='\n"
        f"{py} {external_script}\n"
        "EXT_RC=$?\n"
        f'echo "{STAGE_MARKER_PREFIX}:external:$EXT_RC"\n'
        'exit "$EXT_RC"\n'
    )


# A progress update: some stable prefix followed by "<n>/<total> (<pct>%)".
# The prefix is the "family" - two updates belong to the same run of
# progress only if their prefixes match, so "Processing movie 5/337 (1%)"
# collapses onto "Processing movie 4/337 (1%)" but NOT onto
# "Processing alice's watched 4/233 (1%)". A line carrying genuinely new
# information is never collapsed, only a counter advancing in place.
_PROGRESS_LINE_RE = re.compile(r"^(?P<prefix>.*?)\s*\d+\s*/\s*\d+\s*\(\s*\d+\s*%\s*\)\s*$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def progress_family(line: str) -> Optional[str]:
    """
    Return a progress line's stable prefix, or None if it isn't one.

    Color codes are stripped before matching - the recommender wraps
    these in CYAN/RESET, and the escape sequences would otherwise sit
    between the prefix and the counter and defeat the match.
    """
    plain = _ANSI_RE.sub("", line).strip()
    match = _PROGRESS_LINE_RE.match(plain)
    if not match:
        return None
    return match.group("prefix").strip()


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
        # Cross-container run lock (see utils/run_lock.py) held for this
        # job's subprocess lifetime, None on Windows (that lock is POSIX
        # only - see that module's docstring). Released in _pump()'s
        # finally alongside the existing PID lockfile cleanup.
        self._run_lock: Optional[PosixRunLock] = None

        self._data_lock = threading.Lock()
        self.lines: List[str] = []
        self._subscribers: List["queue.Queue"] = []

        # Per-stage breakdown for the Docker `full` engine (#282/#288) -
        # {"movie": "0", "tv": "1", "external": "skipped"}, in
        # completion order, populated as STAGE_MARKER_PREFIX lines are
        # read (see _pump()). Empty for every other engine/branch
        # (single-engine runs, frozen's --run-recommender full, the
        # non-Docker run.sh/run.ps1 path) - none of those emit stage
        # markers, so there is nothing finer-grained than `state` to
        # show for them.
        self.stage_results: "Dict[str, str]" = {}
        # Set (only ever to False) once _pump() confirms a `full`/
        # `external` run's external stage exited 0 but wrote no new
        # file under recommendations/external/ - the exact "succeeded,
        # but produced nothing" shape #288 reported. None means this
        # engine/run doesn't apply (external stage skipped, failed, or
        # never part of this run) - deliberately distinct from True so
        # a caller can tell "checked and fine" apart from "not
        # applicable" instead of collapsing both into one boolean.
        self.external_produced_output: Optional[bool] = None

    @property
    def state(self) -> str:
        if self.returncode is None:
            return "running"
        return "succeeded" if self.returncode == 0 else "failed"

    def _append_line(self, line: str) -> None:
        # Counter/percentage progress updates collapse onto the previous
        # line instead of piling up (see progress_family). The recommender
        # writes these with a bare \r and no newline - correct in a
        # terminal, where each overwrites the last - but Python opens the
        # subprocess pipe in text mode, and universal-newline translation
        # turns every \r into its own line. A 337-item scan therefore
        # arrived here as 337 separate lines. Collapsing here keeps
        # self.lines (which backs both backlog replay and the stored log)
        # to one line per progress run; web/static/app.js applies the
        # same rule to the live DOM, since subscribers still receive every
        # individual update in order to animate.
        family = progress_family(line)
        with self._data_lock:
            if family is not None and self.lines and progress_family(self.lines[-1]) == family:
                self.lines[-1] = line
            else:
                self.lines.append(line)
            for q in self._subscribers:
                _safe_queue_put(q, line)
        # Stage markers (see STAGE_MARKER_PREFIX/_STAGE_MARKER_RE) are
        # plain stdout lines from the recommender's point of view -
        # they still go through the redact()/log-file/subscriber path
        # above like any other line - this just additionally captures
        # them structurally. Checked on every line (cheap: one regex
        # match against an already-redacted, already-short line) rather
        # than only for engine == "full", so a future engine reusing
        # this marker format doesn't need a second call site.
        match = _STAGE_MARKER_RE.search(line)
        if match:
            stage, result = match.group(1), match.group(2)
            with self._data_lock:
                self.stage_results[stage] = result

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
        return self.try_subscribe(None)  # type: ignore[return-value]

    def try_subscribe(self, max_subscribers: Optional[int]) -> Optional["queue.Queue"]:
        """Like subscribe(), but returns None instead of registering a
        new live subscriber if this job is still running AND already
        has max_subscribers watching it (#287 - see
        MAX_STREAM_SUBSCRIBERS_PER_JOB in web/app.py). max_subscribers
        of None means no cap (this is what subscribe() itself calls
        through to), so existing callers/tests are unaffected.

        The cap only ever applies while running: a finished job's
        subscribe() always succeeds regardless, since it replays the
        backlog and immediately queues DONE_SENTINEL below rather than
        registering a live subscriber that would occupy a thread for
        any meaningful length of time - confirmed in a real container,
        this path was never the actual thread-exhaustion mechanism
        (that was many genuinely-still-watching subscribers of one
        still-RUNNING job - see MAX_STREAM_SUBSCRIBERS_PER_JOB/
        MAX_STREAM_SECONDS's own comments).
        """
        with self._data_lock:
            if self.returncode is None and max_subscribers is not None and len(self._subscribers) >= max_subscribers:
                return None
            q: "queue.Queue" = queue.Queue(maxsize=SUBSCRIBER_QUEUE_MAXSIZE)
            # Replay only what this queue can actually hold. `self.lines`
            # is unbounded (a long run accumulates every line), but `q`
            # caps at SUBSCRIBER_QUEUE_MAXSIZE and _safe_queue_put()
            # evicts oldest-first to stay there - so replaying the full
            # backlog did O(len(self.lines)) work to arrive at exactly
            # the same final queue contents as replaying the last
            # SUBSCRIBER_QUEUE_MAXSIZE. Identical result, bounded cost.
            #
            # This mattered because MAX_STREAM_SECONDS (web/app.py)
            # deliberately ends each SSE response so EventSource
            # reconnects, and every reconnect lands back here. On a long
            # run that made replay cost grow with elapsed output, on a
            # fixed interval - and all of it under _data_lock, which
            # _append_line() needs for every single line, so the thread
            # pumping the subprocess's stdout stalled behind it. The
            # slowdown compounded the longer a run went.
            #
            # Still inside the lock on purpose: releasing it between the
            # snapshot and _subscribers.append(q) below would drop any
            # line emitted in that window. Bounded work under the lock is
            # the fix, not less locking.
            for line in self.lines[-SUBSCRIBER_QUEUE_MAXSIZE:]:
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
            "engine": self.engine,
            "user": self.user,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "returncode": self.returncode,
            "log_file": os.path.basename(self.log_path),
            # See stage_results/external_produced_output's own
            # docstrings above (#282/#288) - both default to "nothing
            # to report" values ({} / None) for every engine/branch
            # that doesn't populate them, so existing consumers of
            # to_dict() that don't know about these two new keys are
            # unaffected.
            "stage_results": dict(self.stage_results),
            "external_produced_output": self.external_produced_output,
        }


class JobManager:
    """Owns the single-run lock and launches recommender subprocesses."""

    def __init__(self, project_root: str, logs_dir: str, code_root: Optional[str] = None):
        self.project_root = project_root
        self.logs_dir = logs_dir
        # #260 (second half): defaults to get_code_root() - the real
        # on-disk code location - independently of project_root (the
        # *data* dir: config/cache/logs, see get_project_root()'s
        # docstring for why these two genuinely differ in Docker).
        # Overridable (see web/app.py's create_app) so tests can point
        # both at the same throwaway fixture root, same as before this
        # split existed.
        self.code_root = code_root if code_root is not None else get_code_root()
        self._lock = threading.Lock()
        self._current: Optional[Job] = None

    def status(self) -> Optional[Dict]:
        job = self._current
        return job.to_dict() if job else None

    def current_job(self) -> Optional[Job]:
        return self._current

    def is_running(self) -> bool:
        job = self._current
        return job is not None and job.state == "running"

    def _lock_path(self) -> str:
        return os.path.join(self.logs_dir, LOCK_FILENAME)

    def _write_lock(self, pid: int) -> None:
        try:
            os.makedirs(self.logs_dir, exist_ok=True)
            with open(self._lock_path(), "w", encoding="utf-8") as f:
                f.write(str(pid))
        except OSError:
            pass  # best-effort - in-process state is still authoritative here

    def _remove_lock(self) -> None:
        try:
            os.remove(self._lock_path())
        except OSError as exc:
            logger.debug(f"_remove_lock: could not remove lockfile {self._lock_path()}: {exc}")

    def _foreign_run_in_progress(self) -> bool:
        """True if a lockfile left by a *different* process points at a
        PID that's still alive - i.e. a run this JobManager instance has
        no in-memory record of (its own server process was restarted
        without a clean shutdown) but that's still actually executing."""
        try:
            with open(self._lock_path(), "r", encoding="utf-8") as f:
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
        if user != "all" and user not in allowed_users:
            raise JobError(f"Unknown user: {user}")
        if engine in ("full", "external") and user != "all":
            raise JobError(f"The '{engine}' engine does not support a single-user run")

        with self._lock:
            if self.is_running():
                raise JobAlreadyRunningError("A run is already in progress")
            if self._foreign_run_in_progress():
                raise JobAlreadyRunningError("A run started by a previous server process is still in progress")

            # Cross-container run lock (see utils/run_lock.py): a
            # docker-entrypoint.sh `recommend` invocation in the sibling
            # curatarr-recommend container holds the identical flock on
            # the identical path while it runs - this is what actually
            # detects that case (the checks above only ever see runs
            # *this* process/container triggered). POSIX only; a no-op
            # on Windows, where this race can't happen (see that
            # module's docstring).
            run_lock = None
            if os.name != "nt":
                run_lock = PosixRunLock(run_lock_path(self.project_root))
                try:
                    run_lock.acquire()
                except OSError as exc:
                    raise JobAlreadyRunningError(
                        "A recommender run is already in progress in another container/process "
                        f"(cross-container lock held): {exc}"
                    ) from exc

            cmd, env, log_name = self._build_command(engine, user)
            os.makedirs(self.logs_dir, exist_ok=True)
            log_path = os.path.join(self.logs_dir, log_name)

            job = Job(engine, user, cmd, log_path)
            job._run_lock = run_lock
            popen_kwargs = dict(
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
            if os.name != "nt":
                # Own session/process group so a server shutdown (see
                # JobManager.terminate_running) can kill the whole tree
                # in one shot - matters for the 'full' engine, whose
                # run.sh itself spawns movie.py/tv.py/external.py as
                # further children, not just the immediate bash process.
                popen_kwargs["start_new_session"] = True
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
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process = subprocess.Popen(cmd, **popen_kwargs)
            except OSError as exc:
                # M3: a missing interpreter/shell (bash, powershell, or
                # even sys.executable itself in some broken install) must
                # surface as a normal, friendly JobError - the /run route
                # already turns that into a redirect with an error
                # message - not an unhandled 500.
                if run_lock is not None:
                    run_lock.release()
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

        #260 (second half): the non-frozen script/run.sh/run.ps1 paths
        below are resolved against get_code_root() - where the code
        actually lives - NEVER against self.project_root, which is
        get_project_root()'s *data* dir (config/cache/logs). Those are
        the same directory for a plain source checkout, which is
        exactly why this was never caught until Docker, where
        CURATARR_CONFIG_DIR points self.project_root at the separately
        mounted /data while the code stays at the image's fixed /app -
        every engine here failed with "can't open file
        '/data/recommenders/movie.py'" (or run.sh) before this fix, with
        the web UI still reporting a 200 because the HTTP request
        itself succeeded even though the subprocess it launched never
        started. See utils/helpers.get_code_root's own docstring.
        """
        env = dict(os.environ)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        frozen = getattr(sys, "frozen", False)
        code_root = self.code_root

        if engine == "full":
            if frozen:
                cmd = [sys.executable, "--run-recommender", "full"]
            elif os.environ.get("RUNNING_IN_DOCKER") == "true":
                # #260: run.sh assumes the directory it lives in
                # (SCRIPT_DIR, its own `cd` target) doubles as the data
                # directory it reads config/cache/logs from - true for
                # a source checkout, false in the Docker image, where
                # code lives at /app but config/cache/logs are a
                # separately mounted /data (CURATARR_CONFIG_DIR - see
                # this function's own docstring above re:
                # get_code_root()). run.sh's dependency-install step
                # was the first symptom (it always fails there - the
                # runtime image never ships requirements.lock/.txt at
                # all, only the already-built venv - see Dockerfile),
                # but is_first_run()'s identical SCRIPT_DIR-relative
                # config/config.yml check is a second, deeper instance
                # of the same assumption - teaching the whole,
                # host-oriented run.sh script (also used by non-Docker
                # installs, where that assumption is correct) about
                # CURATARR_CONFIG_DIR everywhere it reads a path would
                # be a much bigger change than this needs. Docker
                # already has a working, already-shipped way to run all
                # three recommenders in sequence that never goes
                # through run.sh at all - docker-entrypoint.sh's own
                # `recommend full` mode - so this mirrors that exact
                # movie -> tv -> external order directly, same as
                # frozen's own `--run-recommender full` (see
                # curatarr_app.py) bypassing run.sh/run.ps1 entirely.
                # #282/#288: a bare `cmd1 && cmd2 && cmd3` ran every
                # stage fine whenever each one exited 0 (verified in a
                # real container against the real recommenders), and
                # correctly stopped at whichever stage failed - `&&`
                # short-circuiting there matches docker-entrypoint.sh's
                # own `recommend full` under `set -e` (also verified in
                # a real container: it does NOT run every stage
                # unconditionally either). What was actually missing
                # was any indication, anywhere the web UI could see, of
                # *which* stage a failure stopped at - a movie failure
                # correctly skipping tv/external looked, from /run and
                # /run/status, identical to tv/external having been
                # silently dropped for no reason: no stage banners in
                # the log (docker-entrypoint.sh at least has its own
                # echo lines) and nothing in Job.to_dict() beyond one
                # overall state/returncode.
                # _build_docker_full_script keeps the exact same
                # fail-fast semantics (never a fourth variant of "what
                # does `full` mean" alongside run.sh/docker-
                # entrypoint.sh/the frozen dispatcher's
                # --run-recommender full) but adds explicit "=== X ==="
                # banners plus a machine-readable
                # f"{STAGE_MARKER_PREFIX}:<stage>:<rc-or-skipped>" line
                # per stage that _pump() parses into Job.stage_results.
                movie_script = os.path.join(code_root, "recommenders", "movie.py")
                tv_script = os.path.join(code_root, "recommenders", "tv.py")
                external_script = os.path.join(code_root, "recommenders", "external.py")
                py = shlex.quote(sys.executable)
                cmd = [
                    "bash",
                    "-c",
                    _build_docker_full_script(
                        py, shlex.quote(movie_script), shlex.quote(tv_script), shlex.quote(external_script)
                    ),
                ]
            elif os.name == "nt":
                cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", os.path.join(code_root, "run.ps1")]
            else:
                cmd = ["bash", os.path.join(code_root, "run.sh")]
            # Skip the interactive setup wizard / auto-update git-checkout
            # dance for UI-triggered runs on a source checkout - config
            # is assumed already set up. Unused (but harmless) for the
            # Docker branch above, which never touches run.sh at all.
            env["RUNNING_IN_DOCKER"] = "true"
            target = "all"
        elif engine in ("movie", "tv"):
            if frozen:
                cmd = [sys.executable, "--run-recommender", engine]
            else:
                script = os.path.join(code_root, "recommenders", f"{engine}.py")
                cmd = [sys.executable, script]
            if user != "all":
                cmd.append(user)
            target = user
        elif engine == "external":
            if frozen:
                cmd = [sys.executable, "--run-recommender", "external"]
            else:
                script = os.path.join(code_root, "recommenders", "external.py")
                cmd = [sys.executable, script]
            target = "all"
        else:  # pragma: no cover - guarded by start()'s validation above
            raise JobError(f"Unknown engine: {engine}")

        log_name = f"webui_{engine}_{target}_{ts}.log"
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
        forever and both the PID lockfile and the cross-container
        run_lock (see utils/run_lock.py) are always released.
        """
        log_file = None
        returncode: Optional[int] = None
        try:
            log_file = open(job.log_path, "w", encoding="utf-8")
            assert job.process is not None and job.process.stdout is not None
            for line in job.process.stdout:
                # Redact at write time, not just when the web UI later
                # reads this back (see utils/redact.py) - a recommender
                # subprocess could echo a token in its own output (e.g.
                # a stray X-Plex-Token query param in an error message),
                # and the log file on disk must never hold that in
                # plaintext, independent of whether anyone ever views it
                # through the UI.
                line = redact(line)
                log_file.write(line)
                log_file.flush()
                job._append_line(line.rstrip("\n"))
            returncode = job.process.wait()
        except Exception as exc:
            # Subprocess plumbing failure (e.g. couldn't open the log
            # file, couldn't read the pipe), not a recommender-level
            # error - surface it in the live output.
            job._append_line(f"[web UI] job runner error: {exc}")
        finally:
            if log_file is not None:
                try:
                    log_file.close()
                except Exception as exc:
                    logger.debug(f"Error closing job log file: {exc}")
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
            if job._run_lock is not None:
                job._run_lock.release()
            self._check_external_output(job)
            job._finish(returncode if returncode is not None else -1)

    def _check_external_output(self, job: Job) -> None:
        """#288: external.py can catch a per-user exception internally
        and still exit 0 having written nothing new - "succeeded" with
        no output is exactly the confusing shape that issue reported
        (confirmed directly: a real container run hit an unrelated
        per-user error, external.py logged it and moved on, and still
        printed its own "Watchlists saved to: ..." success line).

        Sets job.external_produced_output to False when the external
        stage ran (engine == "external", or engine == "full" with
        stage_results showing it wasn't skipped/failed - see
        STAGE_MARKER_PREFIX) and exited 0 but recommendations/external/
        has no file newer than the job's own start time; True when it
        does. Left at its default None ("not applicable") for every
        other case - a failed or skipped external stage already has its
        own, more specific signal (returncode/stage_results), and a
        second, contradictory "no output" flag on top of that would
        only be confusing. Deliberately does NOT know about
        tuning.yml's external_recommendations.enabled (a deliberately-
        disabled stage legitimately produces no *new* file either) -
        that's a display-layer concern for whatever renders this
        alongside the current config, not this subprocess-result data.
        """
        if job.engine == "full":
            if job.stage_results.get("external") != "0":
                return  # failed, skipped, or never reached a marker at all
        elif job.engine != "external" or job.returncode != 0:
            return

        external_dir = os.path.join(self.project_root, "recommendations", "external")
        cutoff = job.started_at.timestamp()
        try:
            produced = any(
                os.path.getmtime(os.path.join(external_dir, name)) >= cutoff for name in os.listdir(external_dir)
            )
        except OSError:
            produced = False
        job.external_produced_output = produced

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
            if os.name == "nt":
                job.process.terminate()
            else:
                os.killpg(os.getpgid(job.process.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError) as exc:
            logger.debug(f"Could not terminate job process {job.process.pid}: {exc}")
        self._remove_lock()
