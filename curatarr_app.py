"""PyInstaller entry point for the curatarr standalone binary.

Packaged via `pyinstaller curatarr.spec` (see docs/BINARIES.md and
RELEASING.md). With no arguments, this just calls the same
web.app.main() that run-ui.sh / run-ui.ps1 already use for a source
install, so the binary and the source-install web UI stay identical in
behavior. All UI logic lives in web/app.py - keep it that way rather
than adding anything here.

Where a packaged binary reads/writes config/cache/logs: see
utils.helpers.get_project_root() - when running frozen (this file,
built by PyInstaller), that resolves to a per-user data directory
instead of a repo checkout, since a downloaded binary has no repo
checkout to anchor to.

Windowed (no-console) launch on Windows
------------------------------------------------------------------
curatarr.spec builds the Windows exe with console=False, so
double-clicking it never flashes a console window - see
_configure_windowed_launch()/_attach_or_setup_console() below for how
that interacts with CLI use (`curatarr.exe --run-recommender ...` from
an existing cmd/PowerShell), --debug/CURATARR_DEBUG=1, and file
logging. macOS/Linux builds are unaffected (console=True there,
unchanged).

Dispatcher mode - what makes the web UI's Run button work in a frozen
binary
------------------------------------------------------------------
The web UI normally triggers a run by shelling out to
`sys.executable recommenders/<x>.py [user]` (see web/job_runner.py).
That file doesn't exist next to a packaged onefile exe - there is no
`recommenders/` directory on disk once everything is bundled into the
binary, and `sys.executable` for a frozen process IS the curatarr exe
itself, not a python.exe that could run an arbitrary .py path anyway.

So when frozen, web/job_runner.py instead re-invokes this exe as:

    curatarr --run-recommender <engine> [user]

and THIS file recognizes that flag and runs the requested recommender
module's own main() in-process - but "in-process" here means inside
that fresh, short-lived subprocess the web UI just spawned, never
inside the long-lived Flask server process itself. That distinction is
what keeps this safe: the recommender entry points hijack sys.stdout
and call sys.exit() (see web/app.py's module docstring for why that's
unsafe inside the server), which is exactly fine for a process whose
only job is to run one recommender and exit - the same contract as the
`python3 recommenders/<x>.py` invocation this replaces for a source
install. That subprocess already gets its stdout/stderr piped back to
the server via web/job_runner.py's Popen(stdout=PIPE) call, so it never
runs _configure_windowed_launch() below - only the primary UI launch
does.

Self-update dispatch (v2.8.29) - the same trick, for two more flags
------------------------------------------------------------------
`--self-update-worker <--pid P --host H --port PORT>`: the DETACHED
worker web/update_apply.py's UpdateManager spawns for the web UI's
"Update now" button when frozen (see that module's docstring for why
it can't just re-invoke `sys.executable
os.path.abspath(update_apply.py)` the way a source install does - there
is no separate Python interpreter and no on-disk update_apply.py next
to a onefile exe). Recognized here for exactly the same underlying
reason `--run-recommender` is: it's the only way anything can hand a
frozen exe "run this specific internal entry point instead of the
normal UI launch" on the command line.

`--self-update`: the user-facing CLI flag (docs/BINARIES.md) - download,
verify, and swap the binary in place, then exit. Not a hidden dispatch
flag like the two above (it's meant to be run directly by a user/
script), but lives in the same `if len(sys.argv) > 1` chain since it's
the same "frozen-only alternate entry point" shape.
"""

import ctypes
import logging
import os
import sys

from web.app import main


def _debug_requested() -> bool:
    """True if --debug was passed or CURATARR_DEBUG=1 is set - gates
    both _attach_or_setup_console()'s AllocConsole fallback and the log
    level used when logging to file instead."""
    return '--debug' in sys.argv[1:] or os.environ.get('CURATARR_DEBUG') == '1'


def _boot_log_path() -> str:
    """Where the windowed (no-console) Windows build logs to, since
    there's no console to print to: %APPDATA%\\curatarr\\logs\\curatarr.log
    on Windows, ~/.curatarr/logs/curatarr.log elsewhere - the same
    per-user data dir a frozen binary already uses for config/cache/logs
    (see utils.helpers.get_project_root)."""
    from utils import get_project_root
    return os.path.join(get_project_root(), 'logs', 'curatarr.log')


def _configure_windowed_launch() -> None:
    """Only meaningful for the frozen Windows build (curatarr.spec sets
    console=False there). No-op everywhere else - macOS/Linux builds
    (console=True, unchanged) and non-frozen dev runs (`python
    curatarr_app.py` from an already-open terminal) already have a
    normal, working console.
    """
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    _attach_or_setup_console(_debug_requested())


def _attach_or_setup_console(debug: bool) -> None:  # pragma: no cover - real Windows console/ctypes API, exercised by the Windows build test in the release PR, not unit-testable on Linux CI
    """Three cases, in order:

    1. Launched from an existing cmd/PowerShell: AttachConsole finds
       that parent console, so CLI use (`curatarr.exe
       --run-recommender ...`, or just running the exe directly from a
       shell) keeps printing normally, exactly like the old
       console=True build did.
    2. Double-clicked with --debug/CURATARR_DEBUG=1 and no parent
       console found: AllocConsole gives it a fresh one so debug output
       is visible.
    3. Double-clicked normally (the common case) and neither of the
       above applied: no console at all. PyInstaller's windowed
       (console=False) builds otherwise leave sys.stdout/sys.stderr in
       a state that crashes the first time anything prints - point them
       at a log file instead so nothing ever crashes trying to write to
       them, and the output isn't just silently lost either.
    """
    kernel32 = ctypes.windll.kernel32
    attach_parent_process = -1
    attached = bool(kernel32.AttachConsole(attach_parent_process))
    if not attached and debug:
        attached = bool(kernel32.AllocConsole())

    if attached:
        for stream_name, handle_name, mode in (
            ('stdin', 'CONIN$', 'r'),
            ('stdout', 'CONOUT$', 'w'),
            ('stderr', 'CONOUT$', 'w'),
        ):
            try:
                setattr(sys, stream_name, open(handle_name, mode, encoding='utf-8', buffering=1))
            except OSError:
                pass  # keep whatever sys.stdout/stderr already were
        return

    log_path = _boot_log_path()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log_file = open(log_path, 'a', encoding='utf-8', buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[logging.StreamHandler(log_file)],
        force=True,
    )


def _run_one_recommender(engine: str, rest: list) -> None:
    """Run a single recommender's own main() with sys.argv rewritten to
    look like a normal `recommenders/<engine>.py [user] [--debug]`
    invocation, since that's what each one's own argparse expects.

    Deliberately plain `from recommenders.X import main` statements
    (rather than e.g. a dict of lazy __import__() lambdas) even though
    they're inside a function body and only one branch ever actually
    runs - PyInstaller's static analysis walks the AST for import
    statements wherever they appear, but can't see into a dynamic
    __import__("recommenders." + engine) call, so that form would
    silently leave the recommenders package out of the frozen build.
    """
    sys.argv = [f'curatarr --run-recommender {engine}'] + list(rest)
    if engine == 'movie':
        from recommenders.movie import main as run
    elif engine == 'tv':
        from recommenders.tv import main as run
    elif engine == 'external':
        from recommenders.external import main as run
    else:
        print(f"curatarr: unknown recommender engine: {engine}", file=sys.stderr)
        sys.exit(2)
        return
    run()


def _run_self_update_cli() -> None:
    """`curatarr --self-update` / `curatarr.exe --self-update` - the
    user-facing CLI surface of utils/self_update.py (see that module's
    docstring for the full download/verify/swap trust chain). Frozen
    binaries only; a source install has no exe to swap and gets a clear
    message pointing at run.sh/run.ps1 instead - never a stack trace.

    Deliberately does NOT relaunch itself afterward the way the web
    UI's "Update now" flow does (see web/update_apply.py): this is a
    short-lived CLI invocation, not a server that needs to keep
    something bound to a port - printing the result and exiting is
    the whole job. The freshly-swapped binary is simply THERE on disk,
    ready for the next normal launch.
    """
    from utils.self_update import SelfUpdateError, NoUpdateAvailableError, perform_self_update

    if not getattr(sys, 'frozen', False):
        print(
            "curatarr: --self-update only applies to a downloaded binary. "
            "Source installs update via ./run.sh or run.ps1 instead.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        applied_version = perform_self_update()
    except NoUpdateAvailableError as e:
        print(f"curatarr: {e}")
        sys.exit(0)
    except SelfUpdateError as e:
        print(f"curatarr: self-update failed - {e}", file=sys.stderr)
        print("curatarr: the current binary was left unchanged.", file=sys.stderr)
        sys.exit(1)
    print(f"curatarr: updated to v{applied_version} - run curatarr again to use it.")


def _dispatch_recommender(argv: list) -> None:
    """argv is sys.argv[2:], e.g. ['movie', 'alice'] or ['external'] or
    ['full']. See the module docstring above for why this exists."""
    if not argv:
        print(
            "curatarr: --run-recommender requires an engine "
            "(movie, tv, external, full)",
            file=sys.stderr,
        )
        sys.exit(2)

    engine, rest = argv[0], argv[1:]

    if engine == 'full':
        # Mirrors run.sh's RUNNING_IN_DOCKER-bypassed core path: no
        # dependency-install / auto-update / setup-wizard / cron-prompt
        # steps - those are source-install-only concerns that don't
        # apply to a packaged binary - just movie, then tv, then
        # external, in sequence. A fatal error in one (sys.exit from
        # run_recommender_main) stops the rest, same as run.sh's own
        # `... || exit 1` after each step.
        for sub_engine in ('movie', 'tv', 'external'):
            _run_one_recommender(sub_engine, [])
        return

    _run_one_recommender(engine, rest)


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--run-recommender':
        _dispatch_recommender(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == '--self-update-worker':
        # DETACHED worker for the web UI's "Update now" button when
        # frozen - see web/update_apply.py's module docstring and this
        # file's own docstring for why a frozen binary dispatches this
        # way instead of re-invoking update_apply.py as a script.
        from web.update_apply import run_self_update_worker
        run_self_update_worker(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == '--self-update':
        _run_self_update_cli()
    else:
        if getattr(sys, 'frozen', False):
            # Best-effort cleanup of a leftover <exe>.old from a
            # previous self-update swap (see
            # utils/self_update.py's cleanup_stale_old_binary
            # docstring) - runs on every normal frozen startup, not
            # just right after an update.
            from utils.self_update import cleanup_stale_old_binary
            cleanup_stale_old_binary()
        _configure_windowed_launch()
        main()
