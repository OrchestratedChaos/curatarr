"""Tests for web/job_runner.py - the subprocess job runner, single-run
lock, and SSE subscriber fan-out.

These use the curatarr_web_root fixture (see tests/conftest.py), which
provides fake recommenders/*.py + run.sh/run.ps1 scripts so tests are
fast and hermetic - they never touch Plex/TMDB or the real repo.
"""

import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import web.job_runner as job_runner_mod
from web.job_runner import DONE_SENTINEL, Job, JobAlreadyRunningError, JobError, JobManager


def _wait_until_done(job, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.state != "running":
            return
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def _manager(root):
    # code_root=root: this fixture directory also contains fake
    # recommenders/*.py + run.sh/run.ps1 (see curatarr_web_root/
    # _make_root below) precisely so JobManager finds THOSE instead of
    # the real repo's - see web/app.py's create_app docstring for why
    # code_root is independent of project_root (#260).
    return JobManager(root, os.path.join(root, "logs"), code_root=root)


def _make_root(tmp_path, movie_py):
    """Like the curatarr_web_root fixture (tests/conftest.py) but with a
    caller-supplied recommenders/movie.py body, for tests that need
    control over exactly what the child process prints/does."""
    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "config.yml").write_text(
        'plex:\n  url: "http://localhost:32400"\n  token: "not-a-real-token"\nusers:\n  list: "alice, bob"\n',
        encoding="utf-8",
    )
    (root / "logs").mkdir()
    (root / "recommendations" / "external").mkdir(parents=True)
    (root / "recommenders").mkdir()
    (root / "recommenders" / "movie.py").write_text(movie_py, encoding="utf-8")
    (root / "recommenders" / "tv.py").write_text('print("tv done")\n', encoding="utf-8")
    (root / "recommenders" / "external.py").write_text('print("external done")\n', encoding="utf-8")
    (root / "run.sh").write_text("#!/bin/bash\necho full done\n", encoding="utf-8")
    (root / "run.ps1").write_text('Write-Host "full done"\n', encoding="utf-8")
    return str(root)


class TestBuildCommandCodeRootDivergence:
    """#260 (second half) regression: JobManager must resolve
    recommenders/<x>.py and run.sh/run.ps1 against the CODE directory,
    never against project_root (the *data* dir - config/cache/logs).
    In Docker, CURATARR_CONFIG_DIR points project_root at a separately
    mounted /data while the code stays at the image's fixed /app - the
    exact divergence that made every UI-triggered movie/tv/external/full
    run fail with "can't open file '/data/recommenders/movie.py'" while
    the /run POST itself still returned a normal redirect (a 200/303
    with a dead subprocess behind it - the failure #260 was actually
    about, on top of the 403 PR1 fixed in front of it).

    Uses two DELIBERATELY DIFFERENT directories for project_root and
    code_root (neither needs to exist on disk - _build_command only
    constructs the path string, it doesn't open the file) so a
    regression that resolves against the wrong one is caught
    immediately, not masked by a fixture where both happen to coincide.
    """

    def _manager(self, project_root, code_root):
        return JobManager(project_root, os.path.join(project_root, "logs"), code_root=code_root)

    def test_movie_script_path_uses_code_root_not_project_root(self):
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("movie", "alice")
        script = cmd[1]
        assert script.startswith("/app")
        assert not script.startswith("/data")
        assert script == os.path.join("/app", "recommenders", "movie.py")

    def test_tv_script_path_uses_code_root_not_project_root(self):
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("tv", "alice")
        script = cmd[1]
        assert script == os.path.join("/app", "recommenders", "tv.py")

    def test_external_script_path_uses_code_root_not_project_root(self):
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("external", "all")
        script = cmd[1]
        assert script == os.path.join("/app", "recommenders", "external.py")

    def test_full_engine_run_sh_uses_code_root_not_project_root(self):
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        script = cmd[1]
        assert script == os.path.join("/app", "run.sh")

    def test_full_engine_run_ps1_uses_code_root_not_project_root(self, monkeypatch):
        monkeypatch.setattr(job_runner_mod.os, "name", "nt")
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        script = cmd[4]
        assert script == os.path.join("/app", "run.ps1")

    def test_code_root_defaults_to_get_code_root_when_not_given(self):
        """No code_root passed at all - must resolve to the real
        installed code location (utils.helpers.get_code_root()),
        never silently fall back to project_root."""
        from utils.helpers import get_code_root

        manager = JobManager("/some/data/dir", "/some/data/dir/logs")
        cmd, _env, _log_name = manager._build_command("movie", "alice")
        script = cmd[1]
        assert script == os.path.join(get_code_root(), "recommenders", "movie.py")
        assert not script.startswith("/some/data/dir")

    def test_frozen_branch_unaffected_by_code_root_divergence(self, monkeypatch):
        """The 4th deployment shape (PyInstaller --onefile binary) never
        had this bug: it doesn't reference a code_root-resolved script
        path at all, re-invoking sys.executable itself with
        --run-recommender instead (see this module's own docstring for
        why - recommenders/<x>.py isn't shipped as loose files once
        packaged). Confirms this PR's fix left that branch untouched
        even with project_root/code_root pointing at completely
        different, nonexistent directories."""
        monkeypatch.setattr(job_runner_mod.sys, "frozen", True, raising=False)
        manager = self._manager("/data", "/app")

        for engine, user in (("movie", "alice"), ("tv", "alice"), ("external", "all"), ("full", "all")):
            cmd, _env, _log_name = manager._build_command(engine, user)
            assert cmd[0] == job_runner_mod.sys.executable
            assert "--run-recommender" in cmd
            assert not any("/data" in part or "/app" in part for part in cmd)


class TestBuildCommandDockerFullEngine:
    """#260 (second half, continued): the `full` engine still failed
    inside the real Docker image even after the code_root fix above -
    run.sh's own is_first_run() does an identical SCRIPT_DIR-relative
    config/config.yml check (and its dependency-install step assumes
    requirements.lock/.txt are on disk, which they never are in the
    runtime image - see Dockerfile) - both false in Docker, where
    SCRIPT_DIR (/app) and the real data directory (/data,
    CURATARR_CONFIG_DIR) are different places. Inside the real image
    (RUNNING_IN_DOCKER=true), `full` now bypasses run.sh entirely and
    chains movie -> tv -> external directly instead - the same order
    docker-entrypoint.sh's own `recommend full` mode (and frozen's
    `--run-recommender full`) already use in this same image.
    """

    def _manager(self, project_root, code_root):
        return JobManager(project_root, os.path.join(project_root, "logs"), code_root=code_root)

    def test_docker_full_engine_bypasses_run_sh(self, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        assert cmd[0] == "bash"
        assert cmd[1] == "-c"
        assert "run.sh" not in cmd[2]

    def test_docker_full_engine_chains_movie_tv_external_in_order(self, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        script = cmd[2]
        movie_pos = script.index("movie.py")
        tv_pos = script.index("tv.py")
        external_pos = script.index("external.py")
        assert movie_pos < tv_pos < external_pos

    def test_docker_full_engine_no_longer_uses_bare_and_chain(self, monkeypatch):
        """#282/#288: a bare `cmd1 && cmd2 && cmd3` gave no indication of
        *which* stage a failure stopped at - see _build_docker_full_script's
        docstring. Replaced with explicit per-stage gating + markers."""
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        script = cmd[2]
        assert " && " not in script

    def test_docker_full_engine_emits_stage_markers_for_every_stage(self, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        script = cmd[2]
        for stage in ("movie", "tv", "external"):
            assert f"{job_runner_mod.STAGE_MARKER_PREFIX}:{stage}:" in script

    def test_docker_full_engine_skips_later_stages_on_movie_failure(self, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        script = cmd[2]
        assert f"{job_runner_mod.STAGE_MARKER_PREFIX}:tv:skipped" in script
        assert f"{job_runner_mod.STAGE_MARKER_PREFIX}:external:skipped" in script

    def test_docker_full_engine_uses_code_root_not_project_root(self, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        script = cmd[2]
        assert os.path.join("/app", "recommenders", "movie.py") in script
        assert "/data" not in script

    def test_docker_full_engine_invokes_sys_executable(self, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        script = cmd[2]
        assert script.count(shlex.quote(job_runner_mod.sys.executable)) == 3

    def test_non_docker_full_engine_still_uses_run_sh(self, monkeypatch):
        monkeypatch.delenv("RUNNING_IN_DOCKER", raising=False)
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        assert cmd == ["bash", os.path.join("/app", "run.sh")]

    def test_frozen_full_engine_wins_even_if_running_in_docker_is_set(self, monkeypatch):
        """The frozen check is first in the if/elif chain - it must
        never be shadowed by RUNNING_IN_DOCKER (not a real combination
        today, but the ordering itself is what makes that safe)."""
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        monkeypatch.setattr(job_runner_mod.sys, "frozen", True, raising=False)
        manager = self._manager("/data", "/app")
        cmd, _env, _log_name = manager._build_command("full", "all")
        assert cmd == [job_runner_mod.sys.executable, "--run-recommender", "full"]


class TestJobManagerStart:
    """Tests for JobManager.start()"""

    def test_runs_movie_engine_for_single_user(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start("movie", "alice", ["alice", "bob"])
        _wait_until_done(job)
        assert job.returncode == 0
        assert any("user=alice" in line for line in job.lines)
        assert os.path.isfile(job.log_path)
        with open(job.log_path) as f:
            assert "user=alice" in f.read()

    def test_runs_movie_engine_for_all_users(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start("movie", "all", ["alice", "bob"])
        _wait_until_done(job)
        assert job.returncode == 0
        assert any("user=all" in line for line in job.lines)

    def test_runs_full_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start("full", "all", ["alice", "bob"])
        _wait_until_done(job)
        assert job.returncode == 0
        assert any("full run" in line for line in job.lines)

    def test_runs_full_engine_in_docker_without_run_sh(self, curatarr_web_root, monkeypatch):
        """#260: inside the real Docker image, `full` bypasses run.sh
        entirely (see TestBuildCommandDockerFullEngine) and chains the
        three recommenders directly instead - confirms that actually
        executes end to end (not just that _build_command constructs
        the right argv), in the right order, exit code 0.

        #282/#288: also confirms Job.stage_results shows all three
        stages actually ran and succeeded - the structured signal
        /run/status and /status.json now expose (see web/job_runner.py's
        STAGE_MARKER_PREFIX)."""
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        manager = _manager(curatarr_web_root)
        job = manager.start("full", "all", ["alice", "bob"])
        _wait_until_done(job)
        assert job.returncode == 0
        assert not any("full run" in line for line in job.lines)  # run.sh never ran

        movie_idx = job.lines.index("Movie recommendations done")
        tv_idx = job.lines.index("TV recommendations done")
        external_idx = job.lines.index("External watchlists done")
        assert movie_idx < tv_idx < external_idx

        assert job.stage_results == {"movie": "0", "tv": "0", "external": "0"}

    def test_full_engine_in_docker_stops_after_movie_failure(self, tmp_path, monkeypatch):
        """#282 regression: movie failing must stop tv/external from
        ever running (matching the pre-existing `&&`/docker-
        entrypoint.sh `set -e` semantics - verified against the real
        production scripts in a real container, not just this fixture)
        - AND must now say so structurally via stage_results instead of
        leaving the web UI to guess why tv/external never ran."""
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        root = _make_root(tmp_path, 'import sys\nprint("Movie recommendations starting")\nsys.exit(3)\n')
        manager = _manager(root)
        job = manager.start("full", "all", ["alice", "bob"])
        _wait_until_done(job)

        assert job.returncode == 3
        assert job.state == "failed"
        # tv.py/external.py (the fixture's default fakes - see
        # _make_root) never actually ran at all - only the skip
        # banners do, which is the whole point of this fix (#282's own
        # report: previously there was no banner or marker at all, just
        # silence about why tv/external were missing).
        assert not any("tv done" in line for line in job.lines)
        assert not any("external done" in line for line in job.lines)
        assert any("skipped: movie recommendations failed" in line for line in job.lines)
        assert job.stage_results == {"movie": "3", "tv": "skipped", "external": "skipped"}

    def test_full_engine_in_docker_stops_after_tv_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        root = _make_root(tmp_path, 'print("Movie recommendations done")\n')
        with open(os.path.join(root, "recommenders", "tv.py"), "w", encoding="utf-8") as f:
            f.write("import sys\nprint('TV recommendations starting')\nsys.exit(2)\n")
        manager = _manager(root)
        job = manager.start("full", "all", ["alice", "bob"])
        _wait_until_done(job)

        assert job.returncode == 2
        assert job.state == "failed"
        assert any("Movie recommendations done" in line for line in job.lines)
        assert any("TV recommendations starting" in line for line in job.lines)
        # external.py (the fixture's default fake) never actually ran.
        assert not any("external done" in line for line in job.lines)
        assert any("skipped: TV recommendations failed" in line for line in job.lines)
        assert job.stage_results == {"movie": "0", "tv": "2", "external": "skipped"}

    def test_full_engine_external_produced_output_true_when_file_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        root = _make_root(tmp_path, 'print("Movie recommendations done")\n')
        external_dir = os.path.join(root, "recommendations", "external")
        with open(os.path.join(root, "recommenders", "external.py"), "w", encoding="utf-8") as f:
            f.write(
                "import os\n"
                f"open(os.path.join({external_dir!r}, 'alice.html'), 'w').close()\n"
                "print('External watchlists done')\n"
            )
        manager = _manager(root)
        job = manager.start("full", "all", ["alice", "bob"])
        _wait_until_done(job)

        assert job.returncode == 0
        assert job.stage_results == {"movie": "0", "tv": "0", "external": "0"}
        assert job.external_produced_output is True

    def test_full_engine_external_produced_output_false_when_no_file_written(self, curatarr_web_root, monkeypatch):
        """#288: external.py exiting 0 without writing anything new
        (e.g. a per-user exception it caught internally, as observed in
        a real container run) must be distinguishable from a genuine
        success with output - the fixture's own fake external.py only
        prints, it never writes recommendations/external/*, so this is
        exactly that shape."""
        monkeypatch.setenv("RUNNING_IN_DOCKER", "true")
        manager = _manager(curatarr_web_root)
        job = manager.start("full", "all", ["alice", "bob"])
        _wait_until_done(job)

        assert job.returncode == 0
        assert job.external_produced_output is False

    def test_external_engine_produced_output_not_applicable_on_failure(self, curatarr_web_root):
        """external_produced_output stays None (not False) when the
        stage itself failed - that failure already has its own signal
        (returncode); a second, contradictory "no output" flag on top
        of it would be confusing, not additionally informative."""
        root = curatarr_web_root
        with open(os.path.join(root, "recommenders", "external.py"), "w", encoding="utf-8") as f:
            f.write("import sys\nsys.exit(1)\n")
        manager = _manager(root)
        job = manager.start("external", "all", ["alice", "bob"])
        _wait_until_done(job)

        assert job.returncode == 1
        assert job.external_produced_output is None

    def test_runs_external_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start("external", "all", ["alice", "bob"])
        _wait_until_done(job)
        assert job.returncode == 0

    def test_rejects_unknown_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        with pytest.raises(JobError):
            manager.start("bogus", "all", ["alice"])

    def test_rejects_unknown_user(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        with pytest.raises(JobError):
            manager.start("movie", "mallory", ["alice", "bob"])

    def test_rejects_single_user_for_full_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        with pytest.raises(JobError):
            manager.start("full", "alice", ["alice"])

    def test_rejects_single_user_for_external_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        with pytest.raises(JobError):
            manager.start("external", "alice", ["alice"])

    def test_rejects_concurrent_run(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_TEST_SLOW", "1")
        manager = _manager(curatarr_web_root)
        job = manager.start("movie", "alice", ["alice", "bob"])
        with pytest.raises(JobAlreadyRunningError):
            manager.start("movie", "bob", ["alice", "bob"])
        _wait_until_done(job)

    def test_second_run_allowed_after_first_completes(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job1 = manager.start("movie", "alice", ["alice", "bob"])
        _wait_until_done(job1)
        job2 = manager.start("movie", "bob", ["alice", "bob"])
        _wait_until_done(job2)
        assert job2.returncode == 0


class TestJobManagerStatus:
    """Tests for JobManager.status()/current_job()/is_running()"""

    def test_status_none_before_any_run(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        assert manager.status() is None
        assert manager.current_job() is None
        assert manager.is_running() is False

    def test_status_reflects_running_then_finished_job(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_TEST_SLOW", "1")
        manager = _manager(curatarr_web_root)
        job = manager.start("movie", "alice", ["alice", "bob"])
        assert manager.is_running() is True
        status = manager.status()
        assert status["state"] == "running"
        assert status["engine"] == "movie"
        assert status["user"] == "alice"
        _wait_until_done(job)
        assert manager.is_running() is False
        assert manager.status()["state"] == "succeeded"


class TestJobSubscribe:
    """Tests for Job.subscribe()/unsubscribe() - the SSE fan-out."""

    def test_subscribe_after_completion_replays_backlog_then_done(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start("external", "all", ["alice"])
        _wait_until_done(job)

        q = job.subscribe()
        collected = []
        while True:
            item = q.get(timeout=2)
            if item is DONE_SENTINEL:
                break
            collected.append(item)
        assert collected == job.lines

    def test_subscribe_while_running_receives_live_lines(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_TEST_SLOW", "0.3")
        manager = _manager(curatarr_web_root)
        job = manager.start("movie", "alice", ["alice"])
        q = job.subscribe()

        collected = []
        while True:
            item = q.get(timeout=5)
            if item is DONE_SENTINEL:
                break
            collected.append(item)

        assert "Movie recommendations done" in collected
        job.unsubscribe(q)  # already removed on completion; must be a no-op

    def test_subscriber_queue_is_bounded_not_unbounded(self, curatarr_web_root):
        """H2: a subscriber that never reads must not let _append_line
        grow its queue without bound - the oldest entry is dropped once
        the queue is full instead."""
        job = Job("movie", "alice", ["true"], os.path.join(curatarr_web_root, "logs", "x.log"))
        q = job.subscribe()
        for i in range(job_runner_mod.SUBSCRIBER_QUEUE_MAXSIZE + 500):
            job._append_line(f"line {i}")
        assert q.qsize() <= job_runner_mod.SUBSCRIBER_QUEUE_MAXSIZE
        # the newest line survived; the earliest ones were dropped
        last = None
        while not q.empty():
            last = q.get_nowait()
        assert last == f"line {job_runner_mod.SUBSCRIBER_QUEUE_MAXSIZE + 499}"


class TestPumpFailureHandling:
    """Tests for _pump()'s failure paths - H1 (open() failure must not
    wedge the job/lock forever) and M1 (non-UTF8 output, always reaping
    the child)."""

    def test_open_failure_marks_job_failed_and_unwedges_lock(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        # A directory, not a file, so open(log_path, 'w') always raises
        # IsADirectoryError - simulates a bad log path (permissions,
        # full disk, deleted logs dir, etc.) without OS-specific tricks.
        bad_log_path = os.path.join(curatarr_web_root, "logs", "not_a_file")
        os.makedirs(bad_log_path)
        job = Job("movie", "alice", [sys.executable, "-c", 'print("hi")'], bad_log_path)
        job.process = subprocess.Popen(
            job.cmd,
            cwd=curatarr_web_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        manager._current = job

        manager._pump(job)  # must return promptly, never hang

        assert job.state == "failed"
        assert job.returncode == -1
        assert manager.is_running() is False
        assert any("job runner error" in line for line in job.lines)

    def test_non_utf8_output_does_not_crash_and_job_still_completes(self, tmp_path):
        root = _make_root(
            tmp_path,
            (
                "import sys\n"
                "sys.stdout.buffer.write(b'before \\xff\\xfe garbage after\\n')\n"
                "sys.stdout.buffer.flush()\n"
                "print('normal line after')\n"
            ),
        )
        manager = _manager(root)
        job = manager.start("movie", "alice", ["alice"])
        _wait_until_done(job)

        assert job.returncode == 0
        assert job.state == "succeeded"
        assert any("normal line after" in line for line in job.lines)
        with open(job.log_path, encoding="utf-8") as f:
            logged = f.read()
        assert "normal line after" in logged

    def test_killed_child_marks_job_failed_and_releases_lock(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_TEST_SLOW", "10")
        manager = _manager(curatarr_web_root)
        job = manager.start("movie", "alice", ["alice", "bob"])

        deadline = time.time() + 5
        while job.process.poll() is not None and time.time() < deadline:
            time.sleep(0.02)
        assert job.process.poll() is None  # actually started

        job.process.kill()
        _wait_until_done(job)

        assert job.state == "failed"
        assert manager.is_running() is False
        assert not os.path.exists(manager._lock_path())


class TestPopenFailure:
    """Tests for M3 - a missing interpreter/shell must be a friendly
    JobError, not an unhandled 500."""

    def test_popen_failure_raises_friendly_joberror(self, curatarr_web_root, monkeypatch):
        def _boom(*args, **kwargs):
            raise FileNotFoundError("[Errno 2] No such file or directory: 'bash'")

        monkeypatch.setattr(job_runner_mod.subprocess, "Popen", _boom)
        manager = _manager(curatarr_web_root)

        with pytest.raises(JobError) as exc_info:
            manager.start("full", "all", ["alice"])

        assert "Could not start" in str(exc_info.value)
        assert manager.is_running() is False
        assert manager.current_job() is None


class TestCrossContainerLock:
    """Tests for the cross-container run lock (#233 audit remediation
    batch D / PR1(c)) - docker-compose.yml's curatarr (web UI) and
    curatarr-recommend services share the same bind-mounted ./cache
    volume; JobManager.start() must refuse to launch a subprocess while
    that lock is held by *anything* else, not just another run this
    same JobManager instance already knows about."""

    def test_start_rejected_while_lock_held_by_another_container(self, curatarr_web_root):
        """Simulates docker-entrypoint.sh's `recommend` mode already
        running in the sibling curatarr-recommend container: a raw
        flock on the identical path, held by an fd this JobManager
        instance has no in-memory knowledge of at all (unlike
        is_running()/_foreign_run_in_progress(), which only ever see
        runs *this* process triggered)."""
        import fcntl

        from utils.run_lock import run_lock_path

        lock_path = run_lock_path(curatarr_web_root)
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        raw_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(raw_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            manager = _manager(curatarr_web_root)
            with pytest.raises(JobAlreadyRunningError):
                manager.start("movie", "alice", ["alice"])
            assert manager.is_running() is False
            assert manager.current_job() is None
        finally:
            fcntl.flock(raw_fd, fcntl.LOCK_UN)
            os.close(raw_fd)

    def test_start_succeeds_once_the_other_container_releases(self, curatarr_web_root):
        import fcntl

        from utils.run_lock import run_lock_path

        lock_path = run_lock_path(curatarr_web_root)
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        raw_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(raw_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(raw_fd, fcntl.LOCK_UN)
        os.close(raw_fd)

        manager = _manager(curatarr_web_root)
        job = manager.start("movie", "alice", ["alice"])
        _wait_until_done(job)

        assert job.returncode == 0

    def test_lock_released_after_job_completes_so_a_second_run_is_allowed(self, curatarr_web_root):
        """The lock must not leak past one job's lifetime - otherwise
        every run after the first would falsely look like a
        cross-container collision forever."""
        manager = _manager(curatarr_web_root)
        job1 = manager.start("movie", "alice", ["alice", "bob"])
        _wait_until_done(job1)

        job2 = manager.start("movie", "bob", ["alice", "bob"])
        _wait_until_done(job2)

        assert job2.returncode == 0

    def test_lock_released_even_when_popen_itself_fails(self, curatarr_web_root, monkeypatch):
        """A Popen failure (M3) must not leave the cross-container lock
        stuck held forever - see start()'s except OSError branch."""

        def _boom(*args, **kwargs):
            raise FileNotFoundError("[Errno 2] No such file or directory: 'bash'")

        monkeypatch.setattr(job_runner_mod.subprocess, "Popen", _boom)
        manager = _manager(curatarr_web_root)

        with pytest.raises(JobError):
            manager.start("full", "all", ["alice"])

        # The lock must be free again - a plain flock probe proves it,
        # independent of any JobManager state.
        import fcntl

        from utils.run_lock import run_lock_path

        lock_path = run_lock_path(curatarr_web_root)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not raise
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class TestTerminateRunning:
    """Tests for H3 - terminating an in-flight run on server shutdown."""

    def test_terminate_running_kills_in_flight_process(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv("CURATARR_TEST_SLOW", "10")
        manager = _manager(curatarr_web_root)
        job = manager.start("movie", "alice", ["alice", "bob"])

        deadline = time.time() + 5
        while job.process.poll() is not None and time.time() < deadline:
            time.sleep(0.02)
        assert job.process.poll() is None

        manager.terminate_running()

        deadline = time.time() + 5
        while job.process.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert job.process.poll() is not None
        assert not os.path.exists(manager._lock_path())

    def test_terminate_running_is_a_noop_with_nothing_running(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        manager.terminate_running()  # must not raise


class TestForeignLockfile:
    """Tests for the cross-process lockfile (H3's "and" half): a fresh
    JobManager (e.g. after a server restart) must detect a still-alive
    PID left behind by a previous process, and must clean up a stale
    one from a process that's since exited."""

    def test_stale_lockfile_from_dead_pid_is_ignored_and_removed(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        os.makedirs(os.path.dirname(manager._lock_path()), exist_ok=True)
        # PID 999999 should not correspond to a live process in any test
        # environment.
        with open(manager._lock_path(), "w", encoding="utf-8") as f:
            f.write("999999")

        assert manager._foreign_run_in_progress() is False
        assert not os.path.exists(manager._lock_path())

    def test_live_foreign_pid_blocks_a_new_run(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        os.makedirs(os.path.dirname(manager._lock_path()), exist_ok=True)
        # A genuinely separate, currently-alive process - NOT this
        # test's own PID, which _foreign_run_in_progress() deliberately
        # treats as "my own lock", not a foreign one. Simulates a
        # previous curatarr server process (different PID, still
        # running) that left a lock behind after being killed without a
        # clean shutdown.
        helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            with open(manager._lock_path(), "w", encoding="utf-8") as f:
                f.write(str(helper.pid))

            with pytest.raises(JobAlreadyRunningError):
                manager.start("movie", "alice", ["alice"])
        finally:
            helper.kill()
            helper.wait()


class TestWindowsSubprocessCreationFlags:
    """Tests for suppressing a recommender subprocess's own console
    window under the windowed (console=False, see curatarr.spec) build
    - on real Windows, a console-subsystem child (powershell.exe for the
    'full' engine, or the re-invoked frozen exe for the others) would
    otherwise flash a console window even though its stdout/stderr are
    already piped back to this process.

    subprocess.CREATE_NO_WINDOW only exists as an attribute on win32
    Python builds, so _build_command reads it via getattr(...,
    default=0) - these tests force os.name to exercise both branches
    without needing an actual Windows interpreter."""

    def test_windows_popen_requests_create_no_window(self, curatarr_web_root, monkeypatch):
        monkeypatch.setattr(job_runner_mod.os, "name", "nt")
        captured = {}

        def _capture_and_boom(cmd, **kwargs):
            captured.update(kwargs)
            raise FileNotFoundError("[Errno 2] No such file or directory")

        monkeypatch.setattr(job_runner_mod.subprocess, "Popen", _capture_and_boom)
        manager = _manager(curatarr_web_root)

        with pytest.raises(JobError):
            manager.start("movie", "alice", ["alice", "bob"])

        assert captured.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert "start_new_session" not in captured

    def test_posix_popen_omits_creationflags(self, curatarr_web_root, monkeypatch):
        monkeypatch.setattr(job_runner_mod.os, "name", "posix")
        captured = {}

        def _capture_and_boom(cmd, **kwargs):
            captured.update(kwargs)
            raise FileNotFoundError("[Errno 2] No such file or directory")

        monkeypatch.setattr(job_runner_mod.subprocess, "Popen", _capture_and_boom)
        manager = _manager(curatarr_web_root)

        with pytest.raises(JobError):
            manager.start("movie", "alice", ["alice", "bob"])

        assert "creationflags" not in captured
        assert captured.get("start_new_session") is True


class TestPidAliveWindowsTasklist:
    """_pid_alive's Windows branch (a foreign-lockfile liveness probe -
    see TestForeignLockfile above) shells out to tasklist, same
    console-window concern as _build_command's own child above - see
    utils.helpers.no_window_kwargs's docstring."""

    def test_tasklist_call_suppresses_console_window(self, monkeypatch):
        monkeypatch.setattr(job_runner_mod.os, "name", "nt")
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, stdout="1234 python.exe", stderr="")

        monkeypatch.setattr(job_runner_mod.subprocess, "run", _fake_run)

        assert job_runner_mod._pid_alive(1234) is True
        assert captured["cmd"][0] == "tasklist"
        assert captured.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def test_posix_branch_never_calls_subprocess(self, monkeypatch):
        monkeypatch.setattr(job_runner_mod.os, "name", "posix")

        def _boom(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called on the POSIX branch")

        monkeypatch.setattr(job_runner_mod.subprocess, "run", _boom)

        assert job_runner_mod._pid_alive(os.getpid()) is True
