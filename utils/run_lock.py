"""Cross-process advisory lock for recommender runs (#233 audit
remediation batch D / PR1(c)).

docker-compose.yml can run two separate containers sharing the same
bind-mounted ./cache volume: the long-running `curatarr` web UI (which
triggers runs via web/job_runner.py's JobManager, as a subprocess) and
the one-shot `curatarr-recommend` service, meant to be triggered by a
host cron/Task Scheduler entry (see docs/DOCKER.md "Scheduling") via
docker-entrypoint.sh's `recommend` mode. A recommender run mutates
cache/*.json files (and Plex collections) in place - two of them
racing on that shared volume at once is a real interleaved-write
hazard, not just a "friendly error message" problem.

JobManager's existing single-run lock (in-process threading.Lock + a
PID lockfile - see web/job_runner.py) only ever gets taken when a run
is *triggered from the web UI*; docker-entrypoint.sh's `recommend` mode
execs the recommender scripts directly with no locking at all, and
PIDs aren't even comparable across two containers' separate PID
namespaces anyway.

This module is the actual fix for that specific gap: a real OS-level
advisory lock (flock(2)) on a file inside the shared cache/ directory.
docker-entrypoint.sh wraps its own `recommend` invocations with the
`flock` *command* (util-linux, present in the python:3.12-slim/Debian
base image - see Dockerfile) on the identical path;
JobManager.start() (web/job_runner.py) acquires the identical lock via
PosixRunLock below before spawning its subprocess and holds it for
that subprocess's entire lifetime. Both are the same kernel-level lock
on the same inode (bind-mounted into both containers), so they
correctly contend with each other regardless of which side goes
first - flock is exactly the "e.g. flock on a file in the shared
volume" fix this was scoped to.

POSIX only (Linux/macOS - what every actual deployment of the two
compose services runs; fcntl.flock works fine on macOS too, not just
Linux). On Windows (native, non-Docker desktop install - os.name ==
'nt') this is intentionally a no-op: Windows installs don't run
docker-compose's two-container setup, so JobManager's existing
in-process Lock + PID lockfile already covers the one race that can
actually happen there (a second web UI process on the same machine).

Residual risk (documented, not silently accepted): this only protects
the two paths docker-compose.yml actually wires up (job_runner.py's
subprocess launch and docker-entrypoint.sh's own `recommend` mode). A
user who execs `python3 recommenders/movie.py` directly inside a
container's shell, bypassing both of those, still bypasses this lock
too - threading it through every recommender entry point
(utils/cli.py's run_recommender_main, recommenders/external.py) as
well was judged too invasive for this pass (many existing tests
construct those entry points against fake, non-existent paths and
would need real-filesystem mocking added purely for this lock - see
PR description), so that residual gap is accepted rather than forced.
"""

import os
from typing import Optional

RUN_LOCK_FILENAME = ".recommender_run.lock"


def run_lock_path(project_root: str) -> str:
    """Path to the shared cross-container run lock file.

    Always <project_root>/cache/.recommender_run.lock, independent of
    a config-level cache_dir: override - kept deliberately simple
    rather than parsing config.yml here too (see module docstring). A
    custom cache_dir just means this lock file lives in the default
    ./cache alongside the (possibly relocated) real cache files, which
    is harmless - it doesn't need to be *the* cache directory, just
    *a* directory both containers have bind-mounted in common.
    """
    return os.path.join(project_root, "cache", RUN_LOCK_FILENAME)


class PosixRunLock:
    """Non-blocking fcntl.flock() wrapper on run_lock_path().

    acquire() raises OSError immediately (never blocks/waits) if
    another process already holds it - matches JobManager's existing
    fail-fast-with-a-clear-error behavior rather than silently queueing
    a run behind an unbounded wait. Safe to construct and call
    acquire()/release() on any POSIX platform; callers on Windows
    should simply not use this class (see module docstring).
    """

    def __init__(self, lock_path: str):
        self._lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        """Acquire the lock or raise OSError if already held elsewhere."""
        import fcntl

        os.makedirs(os.path.dirname(self._lock_path), exist_ok=True)
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        """Release the lock. Safe to call even if acquire() was never
        called or already failed (a no-op in that case)."""
        if self._fd is None:
            return
        import fcntl

        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
