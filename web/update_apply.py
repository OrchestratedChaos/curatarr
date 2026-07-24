"""
Backing logic for the web UI's "Update now" button (source installs
only - see docs/BINARIES.md for why a binary can't self-update).

Two halves live in this one file:

  - UpdateManager: per-app-instance (like web/job_runner.py's
    JobManager) precondition check + single-run lock + spawning of the
    DETACHED worker that survives THIS server process being killed.
    Used directly by web/app.py's /update/apply route.

  - The detached worker itself (_run_worker / the `if __name__ ==
    '__main__':` block below), invoked as `python update_apply.py
    --project-root ... --pid ... --host ... --port ...` in its own
    session/process group with its own stdio (redirected to
    logs/update_apply.log by UpdateManager._spawn_worker) so it keeps
    running after the server process that spawned it exits.

Why a detached subprocess at all, instead of doing this in-process:
the sequence needs to (a) shut the current server down to free the
port, (b) run the actual git update, (c) start a brand new server -
step (a) obviously can't be done by the process that's shutting itself
down and then expected to keep executing Python afterward. A separate,
detached process is the only way this can "outlive" the restart.

SECURITY: neither this module nor the worker it spawns EVER decides
what code gets checked out. Both the precondition check
(check_verified_update) and the worker's actual apply step shell out
to run.sh's/run.ps1's own --check-verified-update/--apply-verified-update
modes, which reuse the exact same select_verified_release() (pinned
signer fingerprint, verified BEFORE any checkout) run.sh's existing
force-mode auto-update already uses - see run.sh's "WEB UI UPDATE NOW
SUPPORT" section. This file only ever decides WHEN to call that, never
WHAT to trust - the version number utils/update_check.py surfaces is
advisory-only and never reaches this module's checkout decision at all.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger('curatarr')

UPDATE_LOG_FILENAME = 'update_apply.log'

# A git fetch against a slow/unreachable remote shouldn't hang the
# /update/apply request itself - this is just the *precondition check*
# (read-only), run synchronously before responding.
CHECK_TIMEOUT_SECONDS = 20.0

# Worker-side timeouts/delays - module-level constants so tests can
# assert on them without magic numbers, and so a single change updates
# both the sleep and any doc/comment referencing it.
RESPONSE_FLUSH_DELAY_SECONDS = 1.5
OLD_SERVER_SHUTDOWN_TIMEOUT_SECONDS = 15.0
APPLY_TIMEOUT_SECONDS = 60.0


class UpdateNotSupportedError(Exception):
    """Binary (frozen) install - no local git checkout to update."""


class UpdateAlreadyInProgressError(Exception):
    """A second /update/apply request arrived while one was already
    being applied."""


class UpdateNotAvailableError(Exception):
    """The precondition check found no verified newer release."""


def _updater_script(project_root: str) -> str:
    return os.path.join(project_root, 'run.ps1' if os.name == 'nt' else 'run.sh')


def check_verified_update(project_root: str, timeout: float = CHECK_TIMEOUT_SECONDS) -> Optional[str]:
    """
    Precondition check: shells out to run.sh's/run.ps1's own
    --check-verified-update / -CheckVerifiedUpdate mode (read-only,
    never touches the working tree - see run.sh's "WEB UI UPDATE NOW
    SUPPORT" section). Returns the verified tag (e.g. "v2.9.0"), or
    None for both "nothing to update to" and any unexpected error alike
    - both mean the same thing to a caller deciding whether to offer an
    update, and neither should be able to crash a page render or a
    button click.
    """
    script = _updater_script(project_root)
    if not os.path.isfile(script):
        return None
    try:
        if os.name == 'nt':
            cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-File', script, '-CheckVerifiedUpdate']
        else:
            cmd = ['bash', script, '--check-verified-update']
        result = subprocess.run(
            cmd, cwd=project_root, capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return None
        tag = result.stdout.strip()
        return tag or None
    except Exception as e:
        logger.warning(f"Update precondition check failed (non-fatal): {e}")
        return None


class UpdateManager:
    """Per-app-instance state for the "Update now" button.

    Deliberately mirrors web/job_runner.py's JobManager - an
    instance-owned lock (not a bare module global) so every
    create_app() call, including every test, gets its own independent
    lock state instead of leaking across tests/instances.
    """

    def __init__(self, project_root: str, logs_dir: str):
        self.project_root = project_root
        self.logs_dir = logs_dir
        self._lock = threading.Lock()
        self._in_progress = False

    def is_in_progress(self) -> bool:
        return self._in_progress

    def begin_update(self, host: str, port: int) -> str:
        """
        The full gate the /update/apply route calls. Raises one of this
        module's exceptions - and applies/spawns nothing - unless every
        check passes:
          1. source-only (never for a frozen binary)
          2. single-run lock (no two overlapping updates)
          3. precondition check (a verified newer release must actually
             exist right now - re-checked from scratch by the worker
             too, since this check and the worker actually starting are
             not atomic; a race there just means the worker's own
             --apply-verified-update prints NO_UPDATE and it relaunches
             the unchanged current version, never a security gap)

        Returns the verified tag being applied.
        """
        if getattr(sys, 'frozen', False):
            raise UpdateNotSupportedError(
                "Binary installs can't self-update - download the new version instead."
            )

        with self._lock:
            if self._in_progress:
                raise UpdateAlreadyInProgressError("An update is already being applied.")
            self._in_progress = True

        try:
            tag = check_verified_update(self.project_root)
            if not tag:
                raise UpdateNotAvailableError("No verified signed release available to update to.")
            self._spawn_worker(host, port)
            return tag
        except Exception:
            with self._lock:
                self._in_progress = False
            raise

    def _spawn_worker(self, host: str, port: int) -> None:
        """
        Spawn the DETACHED worker and return immediately - see this
        module's docstring and _run_worker() below for what it does.

        start_new_session=True (POSIX) / CREATE_NEW_PROCESS_GROUP +
        DETACHED_PROCESS (Windows) are what let it outlive this
        process: without them, this server process exiting (which the
        worker itself triggers, moments later) would normally also
        terminate a still-attached child via the shared session/process
        group or controlling terminal - exactly backwards from what's
        needed here.
        """
        os.makedirs(self.logs_dir, exist_ok=True)
        log_path = os.path.join(self.logs_dir, UPDATE_LOG_FILENAME)
        log_file = open(log_path, 'a', encoding='utf-8')

        cmd = [
            sys.executable, os.path.abspath(__file__),
            '--project-root', self.project_root,
            '--pid', str(os.getpid()),
            '--host', host,
            '--port', str(port),
        ]
        popen_kwargs = dict(
            cwd=self.project_root,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        if os.name == 'nt':
            # getattr(...) defaults (not a bare subprocess.X reference)
            # for both flags - matches web/job_runner.py's own
            # CREATE_NO_WINDOW precedent: these constants only exist in
            # the subprocess module on win32 builds, so a bare reference
            # would raise AttributeError if this branch were ever
            # exercised (e.g. via a test monkeypatching os.name) on a
            # non-Windows Python.
            popen_kwargs['creationflags'] = (
                getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
                | getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
            )
        else:
            popen_kwargs['start_new_session'] = True

        subprocess.Popen(cmd, **popen_kwargs)
        logger.info(f"Update worker started for {host}:{port} (log: {log_path})")


# =============================================================================
# Detached worker - runs as its OWN process, spawned by
# UpdateManager._spawn_worker above. Never called in-process.
# =============================================================================

def _pid_alive(pid: int) -> bool:
    """Best-effort liveness probe - mirrors web/job_runner.py's
    _pid_alive."""
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
            return True  # can't confirm - fail toward "still running"
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    except OSError:
        return False


def _shut_down_old_server(pid: int, timeout: float) -> None:
    """Signal the old server to shut down gracefully - SIGTERM on
    POSIX, taskkill on Windows. web/app.py's main() already installs a
    handler for both that terminates any in-flight recommender job
    first, then exits cleanly - reused here rather than duplicated.
    Waits up to `timeout` seconds for the pid to actually disappear
    (i.e. for the port to actually be released), but never raises: a
    still-alive old process past the timeout just means the relaunch
    below has to lean on its own bind-retry (see web/app.py's
    _run_with_bind_retry) a bit longer - not a reason to abort and
    leave nothing listening at all."""
    if not _pid_alive(pid):
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid)], capture_output=True, timeout=5)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.2)


def _relaunch_ui(project_root: str, port: int) -> None:
    """Start a fresh, detached UI server on the given port - old code
    if the apply step below failed or found nothing, new code if it
    succeeded (whatever's currently checked out either way). Runs
    run-ui.sh/run-ui.ps1 rather than `python -m web.app` directly so
    dependency install runs again for any new requirements a
    just-applied release might need - same reasoning as run.sh's own
    force-mode restart (`exec "$0" "$@"`)."""
    script = os.path.join(project_root, 'run-ui.ps1' if os.name == 'nt' else 'run-ui.sh')
    env = dict(os.environ)
    env['CURATARR_UI_PORT'] = str(port)
    # The user's existing browser tab is already open and will reload
    # itself once /healthz comes back (see base.html) - don't also pop
    # open a brand new tab/window here.
    env['CURATARR_SKIP_BROWSER_OPEN'] = '1'

    if os.name == 'nt':
        cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-File', script]
        subprocess.Popen(
            cmd, cwd=project_root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=(
                getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
                | getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
            ),
        )
    else:
        cmd = ['bash', script]
        subprocess.Popen(
            cmd, cwd=project_root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def _run_worker(project_root: str, old_pid: int, host: str, port: int) -> None:
    """The actual detached sequence - see this module's docstring for
    the why. Plain print()s: stdout/stderr were already redirected to
    logs/update_apply.log by UpdateManager._spawn_worker before this
    process was started, so this doubles as the operator-visible log of
    what happened if something goes wrong."""
    print(f"[update-worker] starting, old pid={old_pid}, target={host}:{port}", flush=True)

    time.sleep(RESPONSE_FLUSH_DELAY_SECONDS)

    print("[update-worker] shutting down old server...", flush=True)
    _shut_down_old_server(old_pid, OLD_SERVER_SHUTDOWN_TIMEOUT_SECONDS)

    script = _updater_script(project_root)
    if os.name == 'nt':
        apply_cmd = ['powershell', '-ExecutionPolicy', 'Bypass', '-File', script, '-ApplyVerifiedUpdate']
    else:
        apply_cmd = ['bash', script, '--apply-verified-update']

    print("[update-worker] applying verified update...", flush=True)
    try:
        result = subprocess.run(
            apply_cmd, cwd=project_root, capture_output=True, text=True, timeout=APPLY_TIMEOUT_SECONDS,
        )
        output = (result.stdout or '').strip()
        print(f"[update-worker] apply result: {output!r} (exit {result.returncode})", flush=True)
        if result.stderr:
            print(f"[update-worker] apply stderr: {result.stderr.strip()}", flush=True)
    except Exception as e:
        # Whatever happened, fall through to relaunching below anyway -
        # an apply step that couldn't even run is exactly the same
        # "stay on the current version" outcome as NO_UPDATE/FAILED.
        print(f"[update-worker] apply step raised: {e}", flush=True)

    print(f"[update-worker] relaunching UI on port {port}...", flush=True)
    try:
        _relaunch_ui(project_root, port)
        print("[update-worker] relaunch command issued - worker exiting", flush=True)
    except Exception as e:
        # Last-resort log line: if even spawning the relaunch fails,
        # nothing else will bring the UI back up, so the operator needs
        # this in the log to know they must restart manually.
        print(f"[update-worker] FATAL: could not relaunch UI: {e}", flush=True)


def _parse_worker_args(argv):
    parser = argparse.ArgumentParser(
        description='Detached worker for the web UI update-apply flow - not meant to be run by hand.'
    )
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, required=True)
    return parser.parse_args(argv)


if __name__ == '__main__':  # pragma: no cover - detached-process entry point; the decision logic it calls (_run_worker and everything it calls) is exercised directly by tests/test_web_update_apply.py, but actually spawning/killing real processes from a unit test is neither safe nor meaningful - see that file's module docstring, matching this repo's existing precedent for excluding OS-process-boundary code (e.g. curatarr_app.py's _attach_or_setup_console).
    _args = _parse_worker_args(sys.argv[1:])
    _run_worker(_args.project_root, _args.pid, _args.host, _args.port)
