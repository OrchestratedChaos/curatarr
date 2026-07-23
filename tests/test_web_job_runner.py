"""Tests for web/job_runner.py - the subprocess job runner, single-run
lock, and SSE subscriber fan-out.

These use the curatarr_web_root fixture (see tests/conftest.py), which
provides fake recommenders/*.py + run.sh/run.ps1 scripts so tests are
fast and hermetic - they never touch Plex/TMDB or the real repo.
"""

import os
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
        if job.state != 'running':
            return
        time.sleep(0.05)
    raise AssertionError('job did not finish in time')


def _manager(root):
    return JobManager(root, os.path.join(root, 'logs'))


def _make_root(tmp_path, movie_py):
    """Like the curatarr_web_root fixture (tests/conftest.py) but with a
    caller-supplied recommenders/movie.py body, for tests that need
    control over exactly what the child process prints/does."""
    root = tmp_path
    (root / 'config').mkdir()
    (root / 'config' / 'config.yml').write_text(
        'plex:\n  url: "http://localhost:32400"\n  token: "not-a-real-token"\n'
        'users:\n  list: "alice, bob"\n',
        encoding='utf-8',
    )
    (root / 'logs').mkdir()
    (root / 'recommendations' / 'external').mkdir(parents=True)
    (root / 'recommenders').mkdir()
    (root / 'recommenders' / 'movie.py').write_text(movie_py, encoding='utf-8')
    (root / 'recommenders' / 'tv.py').write_text('print("tv done")\n', encoding='utf-8')
    (root / 'recommenders' / 'external.py').write_text('print("external done")\n', encoding='utf-8')
    (root / 'run.sh').write_text('#!/bin/bash\necho full done\n', encoding='utf-8')
    (root / 'run.ps1').write_text('Write-Host "full done"\n', encoding='utf-8')
    return str(root)


class TestJobManagerStart:
    """Tests for JobManager.start()"""

    def test_runs_movie_engine_for_single_user(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start('movie', 'alice', ['alice', 'bob'])
        _wait_until_done(job)
        assert job.returncode == 0
        assert any('user=alice' in line for line in job.lines)
        assert os.path.isfile(job.log_path)
        with open(job.log_path) as f:
            assert 'user=alice' in f.read()

    def test_runs_movie_engine_for_all_users(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start('movie', 'all', ['alice', 'bob'])
        _wait_until_done(job)
        assert job.returncode == 0
        assert any('user=all' in line for line in job.lines)

    def test_runs_full_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start('full', 'all', ['alice', 'bob'])
        _wait_until_done(job)
        assert job.returncode == 0
        assert any('full run' in line for line in job.lines)

    def test_runs_external_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start('external', 'all', ['alice', 'bob'])
        _wait_until_done(job)
        assert job.returncode == 0

    def test_rejects_unknown_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        with pytest.raises(JobError):
            manager.start('bogus', 'all', ['alice'])

    def test_rejects_unknown_user(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        with pytest.raises(JobError):
            manager.start('movie', 'mallory', ['alice', 'bob'])

    def test_rejects_single_user_for_full_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        with pytest.raises(JobError):
            manager.start('full', 'alice', ['alice'])

    def test_rejects_single_user_for_external_engine(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        with pytest.raises(JobError):
            manager.start('external', 'alice', ['alice'])

    def test_rejects_concurrent_run(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv('CURATARR_TEST_SLOW', '1')
        manager = _manager(curatarr_web_root)
        job = manager.start('movie', 'alice', ['alice', 'bob'])
        with pytest.raises(JobAlreadyRunningError):
            manager.start('movie', 'bob', ['alice', 'bob'])
        _wait_until_done(job)

    def test_second_run_allowed_after_first_completes(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job1 = manager.start('movie', 'alice', ['alice', 'bob'])
        _wait_until_done(job1)
        job2 = manager.start('movie', 'bob', ['alice', 'bob'])
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
        monkeypatch.setenv('CURATARR_TEST_SLOW', '1')
        manager = _manager(curatarr_web_root)
        job = manager.start('movie', 'alice', ['alice', 'bob'])
        assert manager.is_running() is True
        status = manager.status()
        assert status['state'] == 'running'
        assert status['engine'] == 'movie'
        assert status['user'] == 'alice'
        _wait_until_done(job)
        assert manager.is_running() is False
        assert manager.status()['state'] == 'succeeded'


class TestJobSubscribe:
    """Tests for Job.subscribe()/unsubscribe() - the SSE fan-out."""

    def test_subscribe_after_completion_replays_backlog_then_done(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        job = manager.start('external', 'all', ['alice'])
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
        monkeypatch.setenv('CURATARR_TEST_SLOW', '0.3')
        manager = _manager(curatarr_web_root)
        job = manager.start('movie', 'alice', ['alice'])
        q = job.subscribe()

        collected = []
        while True:
            item = q.get(timeout=5)
            if item is DONE_SENTINEL:
                break
            collected.append(item)

        assert 'Movie recommendations done' in collected
        job.unsubscribe(q)  # already removed on completion; must be a no-op

    def test_subscriber_queue_is_bounded_not_unbounded(self, curatarr_web_root):
        """H2: a subscriber that never reads must not let _append_line
        grow its queue without bound - the oldest entry is dropped once
        the queue is full instead."""
        job = Job('movie', 'alice', ['true'], os.path.join(curatarr_web_root, 'logs', 'x.log'))
        q = job.subscribe()
        for i in range(job_runner_mod.SUBSCRIBER_QUEUE_MAXSIZE + 500):
            job._append_line(f'line {i}')
        assert q.qsize() <= job_runner_mod.SUBSCRIBER_QUEUE_MAXSIZE
        # the newest line survived; the earliest ones were dropped
        last = None
        while not q.empty():
            last = q.get_nowait()
        assert last == f'line {job_runner_mod.SUBSCRIBER_QUEUE_MAXSIZE + 499}'


class TestPumpFailureHandling:
    """Tests for _pump()'s failure paths - H1 (open() failure must not
    wedge the job/lock forever) and M1 (non-UTF8 output, always reaping
    the child)."""

    def test_open_failure_marks_job_failed_and_unwedges_lock(self, curatarr_web_root):
        manager = _manager(curatarr_web_root)
        # A directory, not a file, so open(log_path, 'w') always raises
        # IsADirectoryError - simulates a bad log path (permissions,
        # full disk, deleted logs dir, etc.) without OS-specific tricks.
        bad_log_path = os.path.join(curatarr_web_root, 'logs', 'not_a_file')
        os.makedirs(bad_log_path)
        job = Job('movie', 'alice', [sys.executable, '-c', 'print("hi")'], bad_log_path)
        job.process = subprocess.Popen(
            job.cmd, cwd=curatarr_web_root, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, encoding='utf-8', errors='replace', bufsize=1,
        )
        manager._current = job

        manager._pump(job)  # must return promptly, never hang

        assert job.state == 'failed'
        assert job.returncode == -1
        assert manager.is_running() is False
        assert any('job runner error' in line for line in job.lines)

    def test_non_utf8_output_does_not_crash_and_job_still_completes(self, tmp_path):
        root = _make_root(tmp_path, (
            'import sys\n'
            "sys.stdout.buffer.write(b'before \\xff\\xfe garbage after\\n')\n"
            'sys.stdout.buffer.flush()\n'
            "print('normal line after')\n"
        ))
        manager = _manager(root)
        job = manager.start('movie', 'alice', ['alice'])
        _wait_until_done(job)

        assert job.returncode == 0
        assert job.state == 'succeeded'
        assert any('normal line after' in line for line in job.lines)
        with open(job.log_path, encoding='utf-8') as f:
            logged = f.read()
        assert 'normal line after' in logged

    def test_killed_child_marks_job_failed_and_releases_lock(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv('CURATARR_TEST_SLOW', '10')
        manager = _manager(curatarr_web_root)
        job = manager.start('movie', 'alice', ['alice', 'bob'])

        deadline = time.time() + 5
        while job.process.poll() is not None and time.time() < deadline:
            time.sleep(0.02)
        assert job.process.poll() is None  # actually started

        job.process.kill()
        _wait_until_done(job)

        assert job.state == 'failed'
        assert manager.is_running() is False
        assert not os.path.exists(manager._lock_path())


class TestPopenFailure:
    """Tests for M3 - a missing interpreter/shell must be a friendly
    JobError, not an unhandled 500."""

    def test_popen_failure_raises_friendly_joberror(self, curatarr_web_root, monkeypatch):
        def _boom(*args, **kwargs):
            raise FileNotFoundError("[Errno 2] No such file or directory: 'bash'")

        monkeypatch.setattr(job_runner_mod.subprocess, 'Popen', _boom)
        manager = _manager(curatarr_web_root)

        with pytest.raises(JobError) as exc_info:
            manager.start('full', 'all', ['alice'])

        assert 'Could not start' in str(exc_info.value)
        assert manager.is_running() is False
        assert manager.current_job() is None


class TestTerminateRunning:
    """Tests for H3 - terminating an in-flight run on server shutdown."""

    def test_terminate_running_kills_in_flight_process(self, curatarr_web_root, monkeypatch):
        monkeypatch.setenv('CURATARR_TEST_SLOW', '10')
        manager = _manager(curatarr_web_root)
        job = manager.start('movie', 'alice', ['alice', 'bob'])

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
        with open(manager._lock_path(), 'w', encoding='utf-8') as f:
            f.write('999999')

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
        helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
        try:
            with open(manager._lock_path(), 'w', encoding='utf-8') as f:
                f.write(str(helper.pid))

            with pytest.raises(JobAlreadyRunningError):
                manager.start('movie', 'alice', ['alice'])
        finally:
            helper.kill()
            helper.wait()
