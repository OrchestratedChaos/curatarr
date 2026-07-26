"""Shared fixtures for the whole tests/ suite (web/ Flask UI fixtures,
plus a couple of suite-wide safety nets - see _no_real_update_check_network,
_isolated_update_dismissal_dir, and _block_non_loopback_sockets below)."""

import os
import socket as _socket_module
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_loopback_address(address) -> bool:
    """True if a socket.connect() address targets only this machine.

    address is whatever was passed to socket.socket.connect() - for
    AF_INET it's (host, port), for AF_INET6 it's (host, port, flowinfo,
    scopeid), for AF_UNIX it's a path string (not a network egress at
    all, so always allowed). By the time a real network stack call
    reaches .connect(), the host is normally already a resolved IP
    (requests/urllib3 resolve via getaddrinfo first), but 'localhost' is
    allowed too for anything that connects before resolving.
    """
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host)
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


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
    server socket and poll it) and raise immediately, with the offending
    host in the message, for anything else. A loud, instant,
    self-diagnosing failure instead of a silent leak or a multi-minute
    hang - any test that legitimately needs to reach a real non-loopback
    host must mock the call instead (mirroring the rest of this suite's
    existing convention of mocking requests.get/MyPlexAccount/etc. rather
    than hitting the network for real).
    """
    real_connect = _socket_module.socket.connect

    def _guarded_connect(self, address, *args, **kwargs):
        if not _is_loopback_address(address):
            raise AssertionError(
                f"Blocked outbound network connect attempt to {address!r} during "
                "test run - tests must mock all real network calls (Plex, TMDB, "
                "GitHub, etc.) rather than reach the network. If this is a "
                "legitimate local-server test, it must connect to "
                "127.0.0.1/::1/localhost only."
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
