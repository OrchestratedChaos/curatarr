"""Tests for web/job_runner.py - the subprocess job runner, single-run
lock, and SSE subscriber fan-out.

These use the curatarr_web_root fixture (see tests/conftest.py), which
provides fake recommenders/*.py + run.sh/run.ps1 scripts so tests are
fast and hermetic - they never touch Plex/TMDB or the real repo.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from web.job_runner import DONE_SENTINEL, JobAlreadyRunningError, JobError, JobManager


def _wait_until_done(job, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.state != 'running':
            return
        time.sleep(0.05)
    raise AssertionError('job did not finish in time')


def _manager(root):
    return JobManager(root, os.path.join(root, 'logs'))


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
