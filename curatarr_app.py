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
install.
"""

import sys

from web.app import main


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
    else:
        main()
