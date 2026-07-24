"""
Backing logic for the web UI's "Update now" button - for BOTH source
installs (git-based, unchanged since v2.8.28) and, as of v2.8.29,
frozen/PyInstaller binaries (in-binary self-update - see
utils/self_update.py's module docstring for that trust chain).

Two halves live in this one file:

  - UpdateManager: per-app-instance (like web/job_runner.py's
    JobManager) precondition check + single-run lock + spawning of the
    DETACHED worker that survives THIS server process being killed.
    Used directly by web/app.py's /update/apply route.

  - The detached worker itself (_run_worker / the `if __name__ ==
    '__main__':` block below), invoked as `python update_apply.py
    --project-root ... --pid ... --host ... --port ...` for a source
    install (its own session/process group with its own stdio,
    redirected to logs/update_apply.log by
    UpdateManager._spawn_worker so it keeps running after the server
    process that spawned it exits), or as `<curatarr-exe>
    --self-update-worker --pid ... --host ... --port ...` for a frozen
    binary (curatarr_app.py's dispatcher recognizes that flag and calls
    straight into _run_worker below - see that module's docstring for
    why a frozen binary can't spawn `sys.executable
    os.path.abspath(__file__)` the way a source install does: there's
    no separate Python interpreter and no on-disk update_apply.py next
    to a PyInstaller onefile exe).

Why a detached subprocess at all, instead of doing this in-process:
the sequence needs to (a) shut the current server down to free the
port, (b) run the actual update (git pull, or - for a frozen binary -
download+verify+swap the exe itself), (c) start a brand new server -
step (a) obviously can't be done by the process that's shutting itself
down and then expected to keep executing Python afterward. A separate,
detached process is the only way this can "outlive" the restart. For a
frozen binary this additionally lets step (b)'s binary swap happen
safely: on Windows in particular, the CURRENTLY-RUNNING web server
process is what's spawning this worker, and it's the worker (a
freshly-started, separate process using the STILL-OLD exe on disk at
spawn time) that ends up renaming/replacing that exe file - never the
long-lived server process trying to replace the file it's actively
serving requests from.

SECURITY: neither this module nor the worker it spawns EVER decides
what code gets checked out/installed on its own authority.
  - Source installs: both the precondition check
    (check_verified_update) and the worker's actual apply step shell
    out to run.sh's/run.ps1's own
    --check-verified-update/--apply-verified-update modes, which reuse
    the exact same select_verified_release() (pinned signer
    fingerprint, verified BEFORE any checkout) run.sh's existing
    force-mode auto-update already uses - see run.sh's "WEB UI UPDATE
    NOW SUPPORT" section.
  - Frozen binaries: the precondition check
    (_check_update_available_for_binary) is advisory-only (same
    unauthenticated utils.update_check.update_available() the CLI
    notice and the banner itself already use) - it decides nothing
    about what bytes end up on disk, only "is it worth spawning a
    worker at all". The ACTUAL trust boundary is entirely inside
    utils.self_update.perform_self_update(), called by the worker: a
    pinned-key SSHSIG signature check on SHA256SUMS.txt, THEN a SHA256
    hash check of the downloaded binary against that now-trusted sums
    file, and ONLY THEN a swap - see that module's docstring. A race
    between this file's cheap advisory check and the worker's real
    verification is not a security gap (same reasoning as the source
    path's own comment on this): worst case, the worker's
    perform_self_update() raises NoUpdateAvailableError/
    SignatureVerificationError/HashMismatchError, nothing gets swapped,
    and the CURRENT binary relaunches unchanged.
This file only ever decides WHEN to call into one of those two trust
chains, never WHAT to trust - the version number utils/update_check.py
surfaces is advisory-only and never reaches either checkout/swap
decision directly.
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

from utils import self_update
from utils.update_check import update_available

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


def _check_update_available_for_binary() -> Optional[str]:
    """Frozen-binary equivalent of check_verified_update() above -
    deliberately much cheaper: just the same advisory, unauthenticated
    utils.update_check.update_available() the CLI notice and the web
    banner already call, NOT a real download+verify (that would mean
    fetching a multi-ten-MB binary synchronously inside this HTTP
    request, which is neither this check's job nor acceptable request
    latency). See this module's docstring for why that's fine: the real
    trust boundary is entirely inside the worker's call to
    utils.self_update.perform_self_update(), not here. Returns a
    'vX.Y.Z'-style tag string (to match check_verified_update's return
    shape) if a newer version is known, else None - both "none known"
    and "check itself failed" collapse to the same None, same fail-open
    contract as update_available() itself."""
    try:
        latest, _current, is_newer = update_available(update_mode='notify', force_refresh=True)
    except Exception as e:
        logger.warning(f"Binary update precondition check failed (non-fatal): {e}")
        return None
    return f"v{latest}" if is_newer else None


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
          1. single-run lock (no two overlapping updates)
          2. precondition check - a newer release must actually be
             known right now. Source installs get a real, synchronous,
             signature-verified check here (check_verified_update);
             frozen binaries get a cheap advisory check
             (_check_update_available_for_binary) since the real
             cryptographic verification for a binary happens inside the
             worker's download, not here (see this module's docstring).
             Either way, this check and the worker actually starting
             are not atomic - a race here just means the worker's own
             apply step (git, or utils.self_update.perform_self_update)
             finds nothing to apply and relaunches the unchanged
             current version, never a security gap.

        Returns the (advisory, for a frozen binary) tag being applied.
        """
        with self._lock:
            if self._in_progress:
                raise UpdateAlreadyInProgressError("An update is already being applied.")
            self._in_progress = True

        try:
            if getattr(sys, 'frozen', False):
                tag = _check_update_available_for_binary()
            else:
                tag = check_verified_update(self.project_root)
            if not tag:
                raise UpdateNotAvailableError("No newer release available to update to.")
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

        Command shape differs by install type (see this module's
        docstring for the full reasoning): a source install re-invokes
        this exact file as a plain Python script (`sys.executable
        os.path.abspath(__file__) ...`); a frozen binary instead
        re-invokes ITSELF with a hidden `--self-update-worker` flag
        (`sys.executable` for a frozen process IS the curatarr exe, and
        there's no on-disk update_apply.py next to a PyInstaller
        onefile build to pass as a script path) - curatarr_app.py's
        dispatcher recognizes that flag and calls straight into
        _run_worker below, mirroring its existing `--run-recommender`
        dispatch for the exact same underlying reason.

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

        popen_kwargs = dict(
            cwd=self.project_root,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        if getattr(sys, 'frozen', False):
            cmd = [
                sys.executable, '--self-update-worker',
                '--pid', str(os.getpid()),
                '--host', host,
                '--port', str(port),
            ]
            # This process (the running server) is itself a frozen
            # curatarr.exe instance and may have PyInstaller onefile's
            # internal _MEIPASS2 bootloader hand-off variable in its own
            # environment - spawning ANOTHER independent instance must
            # not inherit it, or that new instance's bootloader will
            # wrongly skip its own extraction and reuse a stale/wrong
            # temp directory. See utils.self_update.sanitize_frozen_relaunch_env's
            # docstring for the full explanation (same fix applies here
            # for the exact same reason - this worker is itself another
            # fresh, independent curatarr.exe instance).
            popen_kwargs['env'] = self_update.sanitize_frozen_relaunch_env(os.environ)
        else:
            cmd = [
                sys.executable, os.path.abspath(__file__),
                '--project-root', self.project_root,
                '--pid', str(os.getpid()),
                '--host', host,
                '--port', str(port),
            ]
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
    """Signal the old server to shut down - SIGTERM on POSIX, forceful
    taskkill on Windows. web/app.py's main() installs a SIGTERM/SIGINT
    handler that terminates any in-flight recommender job first, then
    exits cleanly - POSIX gets that graceful path via os.kill(SIGTERM).

    Windows has no equivalent for a console-less (windowed,
    console=False - see curatarr.spec) process: plain `taskkill /PID`
    (no /F) sends a WM_CLOSE-style request that only a process with a
    window/message loop can receive, and reliably FAILS against a
    background Flask server with neither - confirmed against a real
    built binary (see this repo's v2.8.29 PR description for the actual
    end-to-end evidence: without /F, the old server never died, the
    relaunched new process couldn't bind the same port, and the whole
    update silently never took effect). `/F` (forceful termination) is
    therefore the only mechanism that actually works here, not a choice
    of graceful-vs-forceful - the graceful signal handler above simply
    has no Windows-console-less equivalent to be delivered through.

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
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True, timeout=5)
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
    succeeded (whatever's currently checked out/on-disk either way).

    Frozen binary: delegates straight to
    utils.self_update.relaunch_binary(), which spawns whatever's
    currently a WORKING executable at (or next to, if a Windows swap's
    own rollback somehow also failed) the running exe's path - see that
    function's docstring. There's no run-ui.sh/run-ui.ps1 next to a
    downloaded standalone binary to invoke.

    Source install (unchanged): runs run-ui.sh/run-ui.ps1 rather than
    `python -m web.app` directly so dependency install runs again for
    any new requirements a just-applied release might need - same
    reasoning as run.sh's own force-mode restart (`exec "$0" "$@"`).
    """
    if getattr(sys, 'frozen', False):
        self_update.relaunch_binary(port=port)
        return

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


def _apply_binary_self_update() -> None:
    """Frozen-binary apply step: the ACTUAL trust boundary for a binary
    install (see this module's docstring) - downloads the platform
    asset + SHA256SUMS.txt + .sig, verifies the pinned-key SSHSIG
    signature and the resulting hash, and only then swaps the running
    executable. Any failure (no update, download, verification, or
    swap) is caught here and logged - never re-raised - so the
    unconditional relaunch below always runs regardless of outcome,
    exactly like the source install's own subprocess.run() try/except
    immediately below this function."""
    try:
        applied_version = self_update.perform_self_update()
        print(f"[update-worker] apply result: UPDATED:v{applied_version}", flush=True)
    except Exception as e:
        print(f"[update-worker] apply result: FAILED:{e}", flush=True)


def _run_worker(project_root: str, old_pid: int, host: str, port: int) -> None:
    """The actual detached sequence - see this module's docstring for
    the why. Plain print()s: stdout/stderr were already redirected to
    logs/update_apply.log by UpdateManager._spawn_worker before this
    process was started, so this doubles as the operator-visible log of
    what happened if something goes wrong.

    Crash-hardening: everything from "shutting down old server" through
    "applying the update" runs under one outer try/except - ANY
    unexpected exception, not just the ones the per-step try/excepts
    already anticipate, is caught, logged, and treated exactly like a
    failed apply: fall through to the unconditional relaunch below.
    This process must NEVER exit via an unhandled exception - besides
    leaving the port dead, curatarr.spec builds Windows as
    console=False/windowed (no console for a traceback to even print
    to) and curatarr_app.py additionally calls
    _suppress_windows_crash_dialogs() as the very first thing on every
    frozen Windows launch specifically so a lower-level native fault
    can't pop a modal Windows Error Reporting dialog on the user's
    desktop either - see that function's docstring. Between the two,
    there is no path from "something went wrong in here" to anything
    visible on the user's desktop other than this log file and,
    moments later, the relaunched (old or new) binary working again.
    """
    print(f"[update-worker] starting, old pid={old_pid}, target={host}:{port}", flush=True)

    try:
        time.sleep(RESPONSE_FLUSH_DELAY_SECONDS)

        print("[update-worker] shutting down old server...", flush=True)
        _shut_down_old_server(old_pid, OLD_SERVER_SHUTDOWN_TIMEOUT_SECONDS)

        if getattr(sys, 'frozen', False):
            print("[update-worker] applying self-update (binary download+verify+swap)...", flush=True)
            try:
                # Belt-and-suspenders on top of _apply_binary_self_update()'s
                # own internal try/except (mirrors the source branch's
                # subprocess.run() try/except immediately below) - the
                # relaunch after this block must be unconditional no matter
                # what, even if something here raised in a way that helper's
                # own handling didn't anticipate.
                _apply_binary_self_update()
            except Exception as e:
                print(f"[update-worker] apply step raised: {e}", flush=True)
        else:
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
    except Exception as e:
        # Last-resort catch-all - see this function's docstring. Nothing
        # above this point (the shutdown wait, an apply step's own
        # try/except somehow not catching everything, etc.) may ever
        # skip the unconditional relaunch below.
        print(f"[update-worker] UNEXPECTED ERROR (still relaunching): {e}", flush=True)

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


def _parse_binary_worker_args(argv):
    """Same shape as _parse_worker_args, minus --project-root - a
    frozen binary's worker invocation (curatarr_app.py's
    `--self-update-worker` dispatch) has no separate checkout path to
    pass; it resolves utils.helpers.get_project_root() itself (the
    per-user data dir) the same way every other frozen entry point
    does."""
    parser = argparse.ArgumentParser(
        description='Detached self-update worker for a frozen binary - not meant to be run by hand.'
    )
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, required=True)
    return parser.parse_args(argv)


def run_self_update_worker(argv) -> None:
    """Entry point curatarr_app.py's `--self-update-worker` dispatch
    calls into directly (never invoked as a subprocess script the way
    this file's own `if __name__ == '__main__':` below is for a source
    install - see this module's docstring for why a frozen binary can't
    do that)."""
    from utils.helpers import get_project_root
    args = _parse_binary_worker_args(argv)
    _run_worker(get_project_root(), args.pid, args.host, args.port)


if __name__ == '__main__':  # pragma: no cover - detached-process entry point; the decision logic it calls (_run_worker and everything it calls) is exercised directly by tests/test_web_update_apply.py, but actually spawning/killing real processes from a unit test is neither safe nor meaningful - see that file's module docstring, matching this repo's existing precedent for excluding OS-process-boundary code (e.g. curatarr_app.py's _attach_or_setup_console).
    _args = _parse_worker_args(sys.argv[1:])
    _run_worker(_args.project_root, _args.pid, _args.host, _args.port)
