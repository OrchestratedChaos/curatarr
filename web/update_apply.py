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
port, (b) run the actual update, (c) start a brand new server - step
(a) obviously can't be done by the process that's shutting itself down
and then expected to keep executing Python afterward. A separate,
detached process is the only way this can "outlive" the restart.

What "run the actual update" means differs sharply by install type:
  - Source installs (unchanged since v2.8.28): the worker shells out to
    run.sh's/run.ps1's own --apply-verified-update mode (git pull +
    verify), then relaunches run-ui.sh/run-ui.ps1 directly in-process -
    see _relaunch_and_verify. A source install's worker is a plain
    python.exe process, not a frozen PyInstaller build, so there is no
    onefile-extraction hazard to worry about here.
  - Frozen binaries: the worker itself only downloads+verifies the new
    binary (utils.self_update.download_and_verify_update - the
    cryptographically sensitive part, needs Python + the `cryptography`
    package). It does NOT swap the binary or relaunch anything itself.
    Both of those are handed off ENTIRELY to a plain external script
    (PowerShell/sh - see utils/self_update_handoff.py) that runs
    completely independently of any frozen Python process. This split
    exists because earlier iterations that DID swap+relaunch in-process
    (spawning a new curatarr.exe instance directly from within this
    frozen worker) kept hitting PyInstaller onefile extraction-directory
    corruption/inheritance issues under real end-to-end testing on
    Windows, regardless of how much the environment was sanitized - see
    utils/self_update_handoff.py's module docstring for the full story
    and why an external script sidesteps it entirely (a genuinely fresh,
    top-level launch of curatarr.exe was confirmed reliable in every
    test; a frozen process launching another instance of itself was
    not).

SECURITY: neither this module nor anything it spawns EVER decides what
code gets checked out/installed on its own authority.
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
    utils.self_update.download_and_verify_update(), called by the
    worker: a pinned-key SSHSIG signature check on SHA256SUMS.txt, THEN
    a SHA256 hash check of the downloaded binary against that
    now-trusted sums file. Only once that succeeds does the worker even
    write/launch the hand-off script - which itself never downloads or
    verifies anything new, it only moves an ALREADY-verified file into
    place. A race between this file's cheap advisory check and the
    worker's real verification is not a security gap (same reasoning as
    the source path's own comment on this): worst case, the worker's
    download_and_verify_update() raises NoUpdateAvailableError/
    SignatureVerificationError/HashMismatchError, nothing gets written,
    no script gets launched, and the CURRENT (old) server just keeps
    running untouched.
This file only ever decides WHEN to call into one of those two trust
chains, never WHAT to trust - the version number utils/update_check.py
surfaces is advisory-only and never reaches either checkout/swap
decision directly.
"""

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

from utils import self_update, self_update_handoff
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

# How long a single relaunch attempt gets to actually start accepting
# connections, and how many times to retry a full fresh spawn if it
# doesn't - see _relaunch_and_verify's docstring for why this exists:
# a frozen relaunch's startup time (onefile extraction, etc.) isn't
# perfectly deterministic, and a single fire-and-forget spawn that
# silently never came up would otherwise leave a genuinely dead port
# with no second chance.
RELAUNCH_VERIFY_TIMEOUT_SECONDS = 15.0
RELAUNCH_MAX_ATTEMPTS = 3


class UpdateAlreadyInProgressError(Exception):
    """A second /update/apply request arrived while one was already
    being applied."""


class UpdateNotAvailableError(Exception):
    """The precondition check found no verified newer release."""


def _fresh_worker_temp_dir() -> str:
    """A guaranteed-unique directory under the system temp root, used
    as the frozen self-update worker's own TEMP/TMP - see the frozen
    branch of UpdateManager._spawn_worker for why."""
    return tempfile.mkdtemp(prefix=f'curatarr-worker-{os.getpid()}-')


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
          3. RUNNING_IN_DOCKER=true (see Dockerfile) short-circuits
             straight to UpdateNotAvailableError, before either
             precondition check above ever runs. A container image has
             no on-disk .git to check out against and isn't a frozen
             binary to swap in place - it updates via `docker pull`
             instead (see docs/DOCKER.md) - so this is an explicit,
             intentional no-op rather than relying on run.sh's own
             `[ ! -d ".git" ]` guard to incidentally fail closed for the
             same reason. web/app.py's _update_banner_context() also
             checks this flag, so a container's update banner never
             even offers a non-functional "Update now" button in the
             first place - this is the belt-and-suspenders backstop for
             anyone hitting the route directly.

        Returns the (advisory, for a frozen binary) tag being applied.
        """
        with self._lock:
            if self._in_progress:
                raise UpdateAlreadyInProgressError("An update is already being applied.")
            self._in_progress = True

        try:
            if os.environ.get('RUNNING_IN_DOCKER') == 'true':
                raise UpdateNotAvailableError(
                    "Self-update isn't available in Docker - pull the new "
                    "image instead, e.g.: docker pull "
                    "ghcr.io/orchestratedchaos/curatarr:latest"
                )
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
            # temp directory.
            #
            # Beyond that: this worker also gets its own fresh TEMP/TMP,
            # not just a sanitized copy of ours - defense-in-depth
            # against the same class of PyInstaller onefile
            # extraction-directory issue that motivated moving the
            # actual swap+relaunch out of any frozen process entirely
            # (see utils/self_update_handoff.py's module docstring): if
            # this worker somehow shared an extraction identity with
            # THIS (soon to be force-killed) server process, that
            # process's own bootloader cleanup could tear apart a
            # shared extraction directory out from under the
            # still-running worker. A worker with its own extraction
            # from the start can never be affected by what happens to
            # the old server's - it only needs to survive long enough
            # to download+verify the update and hand off to the script.
            worker_env = self_update.sanitize_frozen_relaunch_env(os.environ)
            fresh_temp = _fresh_worker_temp_dir()
            worker_env['TEMP'] = fresh_temp
            worker_env['TMP'] = fresh_temp
            popen_kwargs['env'] = worker_env
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


# Same filename web/job_runner.py's JobManager writes/reads - see that
# module's own LOCK_FILENAME. Deliberately duplicated here rather than
# imported: this is a cross-process, filesystem-level contract (the
# PID of whatever recommender subprocess is currently running, if any),
# not something that needs (or should have) an in-process coupling
# between the two modules.
_JOB_LOCK_FILENAME = 'webui_job.lock'


def _recommender_job_in_progress(project_root: str) -> bool:
    """True if web/job_runner.py's own lockfile points at a still-alive
    PID - i.e. a recommender run (movie/tv/external/full) is currently
    executing, regardless of which server process spawned it.

    Why the worker checks this itself, cross-process, instead of
    trusting only the /update/apply route's own (in-process,
    synchronous) app.job_manager.is_running() check: a run could start
    in the gap between that route check passing and this worker
    actually getting to the shutdown step, and a frozen recommender
    subprocess is itself another instance of this same binary sharing
    PyInstaller onefile extraction state with the server that spawned
    it (see utils.self_update.fresh_extraction_temp_dir's docstring for
    the real crash this whole check exists to prevent) - killing/
    swapping that server out from under a still-running job could crash
    it. Fails toward "assume a job might be running" (never proceeds)
    on any read error, same fail-safe direction as _pid_alive's own
    "can't confirm, assume alive" branches.
    """
    lock_path = os.path.join(project_root, 'logs', _JOB_LOCK_FILENAME)
    try:
        with open(lock_path, 'r', encoding='utf-8') as f:
            pid = int(f.read().strip())
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True  # unreadable/corrupt lockfile - can't rule it out, fail safe
    return _pid_alive(pid)


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
    succeeded (whatever's currently checked out either way).

    Source installs only - runs run-ui.sh/run-ui.ps1 rather than
    `python -m web.app` directly so dependency install runs again for
    any new requirements a just-applied release might need - same
    reasoning as run.sh's own force-mode restart (`exec "$0" "$@"`).
    Frozen binaries never reach this function at all: _run_worker's
    frozen branch hands the entire swap+relaunch off to an external
    script instead (see utils/self_update_handoff.py's module
    docstring for why) and returns before ever getting here.
    """
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
            # NOT DETACHED_PROCESS - confirmed via real end-to-end
            # testing (see utils/self_update_handoff.py's identical
            # comment, and this repo's v2.8.29 PR description) that a
            # powershell.exe child launched with DETACHED_PROCESS
            # starts and exits immediately without running any of the
            # script's content - CREATE_NO_WINDOW is the flag that
            # actually keeps it invisible AND working.
            creationflags=(
                getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
                | getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
            ),
        )
    else:
        cmd = ['bash', script]
        subprocess.Popen(
            cmd, cwd=project_root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def _port_is_listening(port: int, timeout: float = 0.3) -> bool:
    """Bare TCP connect probe - mirrors web/app.py's own
    _wait_for_listening (same reasoning: cheaper and more meaningful
    than an HTTP round-trip when the only question is "did SOMETHING
    start accepting connections here")."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def _relaunch_and_verify(project_root: str, port: int) -> None:
    """Calls _relaunch_ui (source installs only - see that function's
    docstring) and then actively confirms the relaunched process
    actually started accepting connections on `port`, retrying a full
    fresh spawn (up to RELAUNCH_MAX_ATTEMPTS times) if it doesn't
    within RELAUNCH_VERIFY_TIMEOUT_SECONDS.

    Why this exists: _relaunch_ui's subprocess.Popen() is
    fire-and-forget by design (see that function's docstring - a
    DETACHED process this one intentionally doesn't wait on). That's
    normally fine, but a relaunch's startup time isn't perfectly
    deterministic (dependency install, antivirus scanning a freshly
    checked-out script, general OS scheduling noise), and a single
    spawn that silently never came up would otherwise leave a dead port
    with no second chance, exactly the outcome this whole self-update
    design exists to prevent. This is a generic reliability net,
    independent of - and in addition to - run.sh's/run.ps1's own
    apply-time fail-safes.

    Never raises - the caller (_run_worker) already treats "relaunch
    ultimately failed" as a logged, non-fatal outcome; there is nothing
    further downstream that could act on an exception here anyway.
    """
    for attempt in range(1, RELAUNCH_MAX_ATTEMPTS + 1):
        print(f"[update-worker] relaunch attempt {attempt}/{RELAUNCH_MAX_ATTEMPTS}...", flush=True)
        try:
            _relaunch_ui(project_root, port)
        except Exception as e:
            print(f"[update-worker] relaunch attempt {attempt} could not even start: {e}", flush=True)
            continue

        deadline = time.time() + RELAUNCH_VERIFY_TIMEOUT_SECONDS
        while time.time() < deadline:
            if _port_is_listening(port):
                print(
                    f"[update-worker] relaunch attempt {attempt} confirmed listening on port {port}",
                    flush=True,
                )
                return
            time.sleep(0.5)
        print(
            f"[update-worker] relaunch attempt {attempt} did not come up within "
            f"{RELAUNCH_VERIFY_TIMEOUT_SECONDS}s",
            flush=True,
        )

    print(
        f"[update-worker] FATAL: port {port} never came up after {RELAUNCH_MAX_ATTEMPTS} relaunch "
        f"attempts - manual restart required",
        flush=True,
    )


def _run_frozen_verify_and_handoff(project_root: str, old_pid: int, port: int) -> None:
    """The ENTIRE frozen-binary apply step. Only downloads+verifies the
    new binary itself (utils.self_update.download_and_verify_update -
    the cryptographically sensitive part) - never swaps or relaunches
    anything in-process. See this module's docstring and
    utils/self_update_handoff.py's module docstring for why: an
    external, plain script now owns the swap+relaunch entirely,
    completely decoupled from this (frozen) process.

    Never raises - every failure mode is caught and logged here, and
    every one of them means the SAME thing: don't touch the old server,
    it just keeps running/serving exactly as it was. There is nothing
    to roll back in any of these cases because nothing was ever
    touched - unlike the old in-process design, the old server isn't
    even signaled to shut down until AFTER a verified update is
    actually ready to apply.
    """
    try:
        verified = self_update.download_and_verify_update()
    except Exception as e:
        print(f"[update-worker] verify failed - old server left running: {e}", flush=True)
        return

    # Race-safe re-check: download+verify can take a while (a real
    # binary download), and a recommender run could have started during
    # that window even though none was running when this worker began -
    # see _recommender_job_in_progress's docstring.
    if _recommender_job_in_progress(project_root):
        print(
            "[update-worker] a recommender run started during download/verify - "
            "aborting, old server left untouched",
            flush=True,
        )
        if os.path.isfile(verified.asset_path):
            try:
                os.remove(verified.asset_path)
            except OSError:
                pass
        return

    print(f"[update-worker] verified v{verified.version} - shutting down old server...", flush=True)
    _shut_down_old_server(old_pid, OLD_SERVER_SHUTDOWN_TIMEOUT_SECONDS)

    print("[update-worker] handing off swap+relaunch to an external script...", flush=True)
    try:
        self_update_handoff.write_and_launch_handoff_script(
            old_pid=old_pid,
            current_exe_path=self_update.current_binary_path(),
            verified_asset_path=verified.asset_path,
            port=port,
            target_version=verified.version,
        )
    except Exception as e:
        # The old server is ALREADY down at this point (see
        # _shut_down_old_server above) - if the hand-off itself somehow
        # fails to even launch, there is no relaunch fallback available
        # to this (frozen) worker (see this module's docstring for why:
        # no run-ui.sh/run-ui.ps1 to fall back to). This is logged as
        # the true last-resort case; the verified download is left in
        # place so an operator can at least see what was downloaded.
        print(f"[update-worker] FATAL: could not launch hand-off script: {e}", flush=True)


def _run_worker(project_root: str, old_pid: int, host: str, port: int) -> None:
    """The actual detached sequence - see this module's docstring for
    the why. Plain print()s: stdout/stderr were already redirected to
    logs/update_apply.log by UpdateManager._spawn_worker before this
    process was started, so this doubles as the operator-visible log of
    what happened if something goes wrong.

    Crash-hardening: this process must NEVER exit via an unhandled
    exception - besides leaving the port dead, curatarr.spec builds
    Windows as console=False/windowed (no console for a traceback to
    even print to) and curatarr_app.py additionally calls
    _suppress_windows_crash_dialogs() as the very first thing on every
    frozen Windows launch specifically so a lower-level native fault
    can't pop a modal Windows Error Reporting dialog on the user's
    desktop either - see that function's docstring. Between the two,
    there is no path from "something went wrong in here" to anything
    visible on the user's desktop other than this log file.

    Frozen binaries: _run_frozen_verify_and_handoff does the ENTIRE job
    (verify, shutdown, hand off to the external script) and never
    raises - this function just calls it and returns; there is nothing
    left for THIS process to relaunch, that's the external script's job
    now (see this module's docstring).

    Source installs (unchanged): shuts down the old server, shells out
    to run.sh's/run.ps1's own --apply-verified-update, then relaunches
    run-ui.sh/run-ui.ps1 directly in-process (_relaunch_and_verify) -
    a source install's worker is a plain python.exe process, so none of
    the frozen-binary hand-off machinery applies to it.
    """
    print(f"[update-worker] starting, old pid={old_pid}, target={host}:{port}", flush=True)

    try:
        time.sleep(RESPONSE_FLUSH_DELAY_SECONDS)

        # Cross-process, race-safe re-check (see _recommender_job_in_progress's
        # docstring) - the /update/apply route already checked
        # app.job_manager.is_running() synchronously before spawning
        # this worker, but a run could have started in the gap since
        # then. If one's in flight now, do NOTHING: leave the old
        # server completely untouched (still healthy, still serving)
        # rather than kill/swap/relaunch out from under a job whose
        # subprocess may share this server's PyInstaller onefile
        # extraction state.
        if _recommender_job_in_progress(project_root):
            print(
                "[update-worker] a recommender run is currently in progress - "
                "aborting this update attempt, old server left untouched",
                flush=True,
            )
            return

        if getattr(sys, 'frozen', False):
            try:
                _run_frozen_verify_and_handoff(project_root, old_pid, port)
            except Exception as e:
                # Last-resort: _run_frozen_verify_and_handoff already
                # catches everything it anticipates - this is only for
                # something totally unexpected slipping past that. There
                # is nothing further this worker can safely do for a
                # frozen binary (no run-ui.sh/ps1 to fall back to, and
                # falling through to the source-only relaunch code below
                # would be wrong for a binary install) - log it and stop.
                print(f"[update-worker] UNEXPECTED ERROR in frozen apply/hand-off: {e}", flush=True)
            print("[update-worker] worker exiting (frozen path complete)", flush=True)
            return

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
    except Exception as e:
        # Last-resort catch-all for the SOURCE path above (the frozen
        # branch already returns before reaching here either way) -
        # nothing above this point may ever skip the unconditional
        # relaunch below.
        print(f"[update-worker] UNEXPECTED ERROR (still relaunching): {e}", flush=True)

    print(f"[update-worker] relaunching UI on port {port}...", flush=True)
    try:
        _relaunch_and_verify(project_root, port)
    except Exception as e:
        # _relaunch_and_verify itself never raises by design - this is
        # a final belt-and-suspenders catch anyway, since NOTHING may
        # ever escape this function unhandled (see its own docstring).
        print(f"[update-worker] FATAL: could not relaunch UI: {e}", flush=True)
    print("[update-worker] worker exiting", flush=True)


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
