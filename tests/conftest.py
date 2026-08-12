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

"""Shared fixtures for the whole tests/ suite (web/ Flask UI fixtures,
plus a couple of suite-wide safety nets - see _no_real_update_check_network,
_isolated_update_dismissal_dir, and _block_non_loopback_sockets below)."""

import os
import socket as _socket_module
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Plex Media Server's well-known default port. plex.direct hostnames -
# the ones Plex itself issues for every real server, e.g. this project's
# own config/config.yml has "https://127-0-0-1.<hash>.plex.direct:32400"
# - are standard, expected Plex behavior (not exotic), and they resolve
# straight to a loopback IP (the dash-encoded address in the hostname
# itself). By the time a real HTTP client (requests/urllib3/plexapi)
# calls socket.connect(), that hostname has already been resolved via
# getaddrinfo() to a plain IP, so a plex.direct name that resolves to
# 127.0.0.1 arrives here indistinguishable from this suite's own local
# test-server binds - a loopback-only check alone would silently ALLOW a
# test with a real config accidentally connecting to a real, live PMS
# instance running on this same machine, defeating the whole point of
# this guard. Blocking this port specifically (regardless of how the
# host got resolved) is what actually closes that gap: nothing in this
# suite's own legitimate loopback socket use (test_web_routes.py's
# TestWaitForListening/TestBindRetry, which always bind to an
# OS-assigned ephemeral port via ("127.0.0.1", 0)) ever targets it.
_PLEX_DEFAULT_PORT = 32400


def _is_allowed_test_socket_address(address) -> bool:
    """True if a socket.connect() address is safe to allow during a test run.

    address is whatever was passed to socket.socket.connect() - for
    AF_INET it's (host, port), for AF_INET6 it's (host, port, flowinfo,
    scopeid), for AF_UNIX it's a path string (not a network egress at
    all, so always allowed). By the time a real network stack call
    reaches .connect(), the host is normally already a resolved IP
    (requests/urllib3 resolve via getaddrinfo first), but 'localhost' is
    allowed too for anything that connects before resolving.

    Loopback alone is NOT sufficient - see _PLEX_DEFAULT_PORT above for
    why a loopback address on Plex's default port is also blocked.
    """
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host)
    is_loopback_host = host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")
    if not is_loopback_host:
        return False
    port = address[1] if len(address) > 1 else None
    if port == _PLEX_DEFAULT_PORT:
        return False
    return True


@pytest.fixture(autouse=True)
def _block_non_loopback_sockets(monkeypatch):
    """Suite-wide regression guard for tests/test_movie.py's and
    tests/test_tv.py's network leak (PlexMovieRecommender/PlexTVRecommender
    construction reaching real plex.tv / TMDB / etc. via unmocked
    utils/plex.py calls - see _no_real_plex_watched_lookups in those two
    files for the specific fix). That leak was traced by instrumenting
    socket.socket.connect and observing real outbound TCP attempts from
    otherwise fully-mocked tests; a runaway connect attempt like that can
    silently leak the test runner's network position (and a real Plex
    token, if one happens to be set in the environment) at best, and
    hang CI for many minutes at worst (observed once: a 24-minute hang
    that an identical re-run of the same commit did not reproduce,
    because whether the outbound connect fails fast or hangs depends on
    the runner's network egress policy, not on the test itself).

    Patches socket.socket.connect (the single choke point every TCP
    connection - direct socket use, socket.create_connection(), and
    therefore urllib3/requests - eventually goes through) to allow
    127.0.0.1/::1/localhost (loopback - e.g. tests/test_web_routes.py's
    TestWaitForListening/TestBindRetry classes, which bind a real local
    server socket and poll it) EXCEPT on Plex's own default port (see
    _PLEX_DEFAULT_PORT/_is_allowed_test_socket_address above - a
    loopback-only check would have been a false guarantee: plex.direct
    hostnames are standard Plex behavior that resolve straight to
    loopback, so a test running against a real config would sail
    straight through a check that only looked at the host), and raise
    immediately, with the offending address in the message, for
    anything else. A loud, instant, self-diagnosing failure instead of a
    silent leak or a multi-minute hang - any test that legitimately
    needs to reach a real non-loopback host (or the real Plex port) must
    mock the call instead (mirroring the rest of this suite's existing
    convention of mocking requests.get/MyPlexAccount/etc. rather than
    hitting the network for real).
    """
    real_connect = _socket_module.socket.connect

    def _guarded_connect(self, address, *args, **kwargs):
        if not _is_allowed_test_socket_address(address):
            raise AssertionError(
                f"Blocked outbound network connect attempt to {address!r} during "
                "test run - tests must mock all real network calls (Plex, TMDB, "
                "GitHub, etc.) rather than reach the network. If this is a "
                "legitimate local-server test, it must connect to "
                "127.0.0.1/::1/localhost on a non-Plex port only."
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(_socket_module.socket, "connect", _guarded_connect)


@pytest.fixture(autouse=True)
def _no_real_update_check_network(tmp_path_factory, monkeypatch):
    """Suite-wide safety net: utils.update_check.get_project_root() is
    NOT the same thing as a Flask app's project_root fixture override -
    it always resolves to the real repo checkout (or the real per-user
    data dir when frozen), regardless of what project_root a given test
    passes to create_app(). Without this, any test whose config.yml
    doesn't explicitly set general.update_mode: off (the new default is
    'notify') makes a REAL network call to the GitHub Releases API and
    writes a REAL update_check_cache.json into the repo root on every
    such test run - discovered the hard way via tests/
    test_web_config_libraries.py's bare-tmp_path config fixture.

    Patches utils.update_check._fetch_latest_version specifically
    (rather than the shared requests.get, which utils/plex.py, utils/
    tmdb.py, utils/radarr.py etc. ALL also import from the same
    `requests` module singleton - patching requests.get here broke ~20
    unrelated tests in test_movie.py/test_tv.py/test_external.py that
    mock or exercise their OWN requests.get calls) so this can never
    collide with any other module's HTTP mocking.

    utils.update_check is fail-open by design, so simulating "offline"
    here just means every test sees "no newer version known" by
    default unless it explicitly mocks around this - which
    tests/test_update_check.py and the update-notice/banner tests do,
    via their own more specific mocks that layer on top of (override)
    this one for the duration of those tests.
    """
    monkeypatch.setattr(
        "utils.update_check.get_project_root",
        lambda: str(tmp_path_factory.mktemp("update_check_cache")),
    )
    monkeypatch.setattr("utils.update_check._fetch_latest_version", lambda: None)


@pytest.fixture(autouse=True)
def _isolated_update_dismissal_dir(tmp_path_factory, monkeypatch):
    """Same reasoning as _no_real_update_check_network above, for the
    update-dismissal state (utils/update_dismissal.py) added in v2.8.31:
    its get_project_root() also always resolves to the real repo/data
    dir regardless of a test's Flask app project_root override. Without
    this, any test that exercises a dismissed/snoozed banner would write
    a REAL dismissed_update.json into the repo root. Tests that actually
    need dismissal state to persist across calls within a single test
    (tests/test_update_dismissal.py, the snooze tests in tests/
    test_web_update_banner.py) override this per-test with a stable
    tmp_path, same layering _no_real_update_check_network documents for
    the update-check cache above.
    """
    monkeypatch.setattr(
        "utils.update_dismissal.get_project_root",
        lambda: str(tmp_path_factory.mktemp("update_dismissal")),
    )


@pytest.fixture(autouse=True)
def _isolated_metrics_dir(tmp_path_factory, monkeypatch):
    """Same reasoning as _isolated_update_dismissal_dir above, for
    utils/metrics.py's on-disk metrics_state.json: its get_project_root()
    also always resolves to the real repo/data dir regardless of a
    test's Flask app project_root override, and metrics recording is
    exercised incidentally by a lot of otherwise-unrelated tests (any
    recommender run, any *arr/TMDB/Trakt/Simkl/Tautulli/MDBList client
    call, any cache read - see each module's own utils.metrics call
    sites). Without this, those tests would write a REAL
    cache/metrics_state.json into the repo root. Tests that specifically
    exercise metrics persistence/rendering (tests/test_metrics.py,
    tests/test_web_metrics.py) override this per-test with their own
    stable tmp_path, same layering the two fixtures above document.
    """
    monkeypatch.setattr(
        "utils.metrics.get_project_root",
        lambda: str(tmp_path_factory.mktemp("metrics_state")),
    )


@pytest.fixture(autouse=True)
def _isolated_recommender_cache_dir(tmp_path_factory, monkeypatch):
    """Same reasoning as _isolated_metrics_dir above, for
    recommenders/base.py's BaseRecommender.__init__ (self.cache_dir =
    get_project_root() + config['cache_dir']) and recommenders/
    external.py's several standalone functions that resolve cache_dir
    the same way - both hold their OWN `from utils import
    get_project_root` name binding (same reason the three fixtures
    above each patch their own consuming module rather than
    utils.helpers.get_project_root itself - patching the origin
    wouldn't touch a binding another module already copied at import
    time).

    Without this, any test that constructs a real (unmocked)
    PlexMovieRecommender/PlexTVRecommender, or calls one of
    recommenders/external.py's real cache-dir-resolving functions,
    writes REAL watched_cache_plex_<user>.json /
    tv_watched_cache_plex_<user>.json / external_recs_*.json files into
    the real repo's cache/ directory - this is exactly how fake test
    usernames (plex_bob, plex_user1, anime_plex_user1) ended up in the
    live cache/ dir: tests/test_tv.py in particular never overrode
    get_project_root at all before this fixture existed.

    Still honors CURATARR_CONFIG_DIR (falls through to the real
    override-branch behavior) rather than unconditionally replacing
    get_project_root with a fixed path, so this can't break the tests
    that deliberately exercise the REAL cache_dir resolution logic
    against an explicit CURATARR_CONFIG_DIR (tests/test_base.py's
    TestBaseRecommenderCacheDirResolution, tests/test_movie.py's
    TestLibraryFetchedOnceNotSixTimes) - those still get the directory
    THEY set, not this fixture's fallback tmp_path. Tests that
    specifically exercise recommenders/external.py's own cache-dir
    handling already patch recommenders.external.get_project_root
    themselves per-test (see tests/test_external.py); this is a
    safety net for the many that don't.

    ALSO no-ops recommenders.base.migrate_legacy_cache_dir - discovered
    the hard way while building this exact fixture: with get_project_root
    faked out but migrate_legacy_cache_dir left real,
    BaseRecommender.__init__'s own legacy_cache_dir (computed straight
    off recommenders/base.py's __file__, bypassing get_project_root
    entirely by design - see that function's docstring) still resolves
    to the REAL repo cache/ directory, which the real
    migrate_legacy_cache_dir would then treat as "legacy" relative to
    this fixture's fake new_dir and shutil.move() every real file in it
    into a throwaway tmp_path - silently DELETING real cache/ contents
    on every test run that constructs a recommender, not just
    preventing new writes. tests/test_helpers.py's own
    migrate_legacy_cache_dir tests call the function directly (a
    different name binding - see above), so they're unaffected;
    tests/test_base.py already mocks this itself in every test that
    needs to (its own @patch layers on top of this, same convention as
    every other fixture here).

    ALSO patches recommenders.movie.get_project_root and
    recommenders.tv.get_project_root - a second, separate name binding
    from recommenders.base.get_project_root above (movie.py/tv.py each
    hold their own `from utils import get_project_root`), used by
    process_recommendations()'s own log_dir = os.path.join(
    get_project_root(), "logs") (feeding both setup_log_file()/
    teardown_log_file() and record_run_status()). Left unpatched, any
    test that calls process_recommendations() without ALSO separately
    mocking those two - or the setup_log_file/teardown_log_file/
    record_run_status calls it makes - resolves log_dir to the REAL
    repo's logs/ directory and writes a real run_status_movie_<user>.json
    / run_status_tv_<user>.json into it. Confirmed: tests/test_movie.py's
    and tests/test_tv.py's TestProcessRecommendationsLibraryParam classes
    did exactly this (mocked setup_log_file/teardown_log_file/the
    recommender class, but not record_run_status) before this fixture
    covered these two modules - the same class of leak
    _isolated_metrics_dir/_isolated_update_dismissal_dir/
    _no_real_update_check_network above each exist to close for their
    own module.
    """
    fallback_root = str(tmp_path_factory.mktemp("recommender_cache"))

    def _fake_get_project_root():
        override = os.environ.get("CURATARR_CONFIG_DIR")
        if override:
            os.makedirs(override, exist_ok=True)
            return override
        return fallback_root

    monkeypatch.setattr("recommenders.base.get_project_root", _fake_get_project_root)
    monkeypatch.setattr("recommenders.external.get_project_root", _fake_get_project_root)
    monkeypatch.setattr("recommenders.movie.get_project_root", _fake_get_project_root)
    monkeypatch.setattr("recommenders.tv.get_project_root", _fake_get_project_root)
    # utils/plex.py resolves the per-user Plex token cache the same way
    # (get_project_root + config['cache_dir']) and holds its own binding,
    # so it needs the same treatment or it writes into the real cache/.
    monkeypatch.setattr("utils.plex.get_project_root", _fake_get_project_root)
    monkeypatch.setattr("recommenders.base.migrate_legacy_cache_dir", lambda legacy_dir, new_dir: None)


_FAKE_MOVIE_PY = """\
import os
import sys
import time

print("Movie recommendations starting")
user = sys.argv[1] if len(sys.argv) > 1 else "all"
print(f"user={user}")
delay = os.environ.get("CURATARR_TEST_SLOW")
if delay:
    time.sleep(float(delay))
print("Movie recommendations done")
"""

_FAKE_TV_PY = _FAKE_MOVIE_PY.replace("Movie", "TV")

_FAKE_EXTERNAL_PY = """\
print("External watchlists starting")
print("External watchlists done")
"""

_FAKE_RUN_SH = """#!/bin/bash
echo "full run starting"
echo "full run done"
"""

_FAKE_RUN_PS1 = """
Write-Host "full run starting"
Write-Host "full run done"
"""

_CONFIG_YML = """\
plex:
  url: "http://localhost:32400"
  token: "not-a-real-token"
users:
  list: "alice, bob"
  preferences:
    alice:
      display_name: "Alice A"
general:
  # Off by default in this shared fixture, purely as a reasonable
  # default value - unlike before v2.8.31, this no longer skips the
  # update-banner context processor's update_available() call (every
  # mode, including 'off', calls it now); the real reason no test here
  # accidentally renders a banner is _no_real_update_check_network above
  # patching _fetch_latest_version to always return None regardless of
  # mode. Tests that specifically exercise the update banner (tests/
  # test_web_update_banner.py) write their own config.yml with whatever
  # update_mode they need.
  update_mode: off
libraries:
  - id: movies
    name: Movies
    section: Movies
    media_type: movie
    arr:
      root_folder: /data/movies
      quality_profile: HD-1080p
      minimum_availability: released
      instance:
        url: "http://localhost:7878"
        api_key: "not-a-real-radarr-key"
  - id: tv-shows
    name: TV Shows
    section: TV Shows
    media_type: tv
"""


@pytest.fixture
def curatarr_web_root(tmp_path):
    """A throwaway fake curatarr project root for web/ tests.

    Mirrors the real repo layout that web/app.py and web/job_runner.py
    expect (config/config.yml, logs/, recommendations/external/,
    recommenders/*.py, run.sh/run.ps1) without touching the real repo
    or running the real (slow, Plex/TMDB-dependent) recommenders.
    """
    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "config.yml").write_text(_CONFIG_YML, encoding="utf-8")
    (root / "logs").mkdir()
    (root / "recommendations" / "external").mkdir(parents=True)
    (root / "recommenders").mkdir()
    (root / "recommenders" / "movie.py").write_text(_FAKE_MOVIE_PY, encoding="utf-8")
    (root / "recommenders" / "tv.py").write_text(_FAKE_TV_PY, encoding="utf-8")
    (root / "recommenders" / "external.py").write_text(_FAKE_EXTERNAL_PY, encoding="utf-8")
    (root / "run.sh").write_text(_FAKE_RUN_SH, encoding="utf-8")
    (root / "run.ps1").write_text(_FAKE_RUN_PS1, encoding="utf-8")
    return str(root)


def _snapshot_dir_mtimes(directory: str) -> dict:
    """path -> mtime for every file under *directory* (empty dict if it
    doesn't exist). Helper for _fail_if_real_logs_or_cache_written below."""
    if not os.path.isdir(directory):
        return {}
    snapshot = {}
    for root, _dirs, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            try:
                snapshot[path] = os.path.getmtime(path)
            except OSError:
                # Deleted between os.walk() yielding it and getmtime() -
                # nothing to snapshot, and not a leak this guard cares about.
                continue
    return snapshot


@pytest.fixture(scope="session", autouse=True)
def _fail_if_real_logs_or_cache_written():
    """Hard gate: this project has a repeat history of test-isolation
    leaks writing real files into the checked-out repo's own logs/ and
    cache/ directories (see _isolated_recommender_cache_dir's docstring
    above for the fullest account, and utils/metrics.py's/utils/
    update_dismissal.py's own equivalents) - every prior instance was
    only ever noticed by someone spotting an unexpected file in `git
    status`/`ls -la` afterward, not by the suite itself. This snapshots
    the real repo's logs/ and cache/ directories (by path -> mtime) once
    at session start and once at session end; if anything was created,
    deleted, or modified in either, the session fails loudly naming
    every changed path, rather than staying green while quietly
    corrupting the owner's real generated data.

    Deliberately session-scoped (one check for the whole run, not
    per-test) - walking both directories after each of ~2900 tests would
    be wasteful, and the fixtures this guards against are themselves
    autouse-for-the-whole-suite, so there's no per-test granularity to
    preserve here anyway.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logs_dir = os.path.join(repo_root, "logs")
    cache_dir = os.path.join(repo_root, "cache")

    before_logs = _snapshot_dir_mtimes(logs_dir)
    before_cache = _snapshot_dir_mtimes(cache_dir)

    yield

    after_logs = _snapshot_dir_mtimes(logs_dir)
    after_cache = _snapshot_dir_mtimes(cache_dir)

    changed = []
    for label, before, after in (("logs", before_logs, after_logs), ("cache", before_cache, after_cache)):
        for path in sorted(set(after) - set(before)):
            changed.append(f"created ({label}): {path}")
        for path in sorted(set(before) - set(after)):
            changed.append(f"deleted ({label}): {path}")
        for path in sorted(set(after) & set(before)):
            if after[path] != before[path]:
                changed.append(f"modified ({label}): {path}")

    if changed:
        raise AssertionError(
            "Test suite wrote to the real repo's logs/ and/or cache/ directory "
            "- every test must isolate these via tmp_path/tmp_path_factory (see "
            "_isolated_recommender_cache_dir, _isolated_metrics_dir, and "
            "_isolated_update_dismissal_dir above) rather than letting a "
            "module's real get_project_root() resolve during a test run. "
            "Changed paths:\n  " + "\n  ".join(changed)
        )
