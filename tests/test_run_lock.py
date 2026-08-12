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

"""Tests for utils/run_lock.py - the cross-container recommender run
lock (#233 audit remediation batch D / PR1(c)).
"""

import fcntl
import os

import pytest

from utils.run_lock import PosixRunLock, run_lock_path


class TestRunLockPath:
    def test_path_is_under_cache_dir(self, tmp_path):
        path = run_lock_path(str(tmp_path))

        assert path == os.path.join(str(tmp_path), "cache", ".recommender_run.lock")


class TestPosixRunLock:
    def test_acquire_creates_lock_file_and_cache_dir(self, tmp_path):
        lock_path = run_lock_path(str(tmp_path))
        lock = PosixRunLock(lock_path)

        lock.acquire()
        try:
            assert os.path.exists(lock_path)
        finally:
            lock.release()

    def test_second_acquire_from_same_process_fails(self, tmp_path):
        """A second, independent file descriptor on the same path must
        fail to acquire while the first is held - this is the actual
        mechanism that makes two containers bind-mounting the same
        ./cache volume correctly contend with each other."""
        lock_path = run_lock_path(str(tmp_path))
        first = PosixRunLock(lock_path)
        second = PosixRunLock(lock_path)

        first.acquire()
        try:
            with pytest.raises(OSError):
                second.acquire()
        finally:
            first.release()

    def test_lock_available_again_after_release(self, tmp_path):
        lock_path = run_lock_path(str(tmp_path))
        first = PosixRunLock(lock_path)
        first.acquire()
        first.release()

        second = PosixRunLock(lock_path)
        second.acquire()
        second.release()  # must not raise

    def test_release_without_acquire_is_a_safe_noop(self, tmp_path):
        lock = PosixRunLock(run_lock_path(str(tmp_path)))
        lock.release()  # must not raise

    def test_release_is_idempotent(self, tmp_path):
        lock = PosixRunLock(run_lock_path(str(tmp_path)))
        lock.acquire()
        lock.release()
        lock.release()  # must not raise a second time

    def test_contends_with_a_raw_flock_held_by_another_fd(self, tmp_path):
        """Simulates docker-entrypoint.sh's `recommend` mode, which
        holds this same lock via the flock(1) *command* (a plain
        fcntl.flock on an independently-opened fd, not a PosixRunLock
        instance) - proves the two mechanisms genuinely contend on the
        same underlying kernel lock, not just within this Python class."""
        lock_path = run_lock_path(str(tmp_path))
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        raw_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(raw_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            lock = PosixRunLock(lock_path)
            with pytest.raises(OSError):
                lock.acquire()
        finally:
            fcntl.flock(raw_fd, fcntl.LOCK_UN)
            os.close(raw_fd)
