"""Shared fixtures for the whole tests/ suite (web/ Flask UI fixtures,
plus a couple of suite-wide safety nets - see _no_real_update_check_network
below)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
        'utils.update_check.get_project_root',
        lambda: str(tmp_path_factory.mktemp('update_check_cache')),
    )
    monkeypatch.setattr('utils.update_check._fetch_latest_version', lambda: None)

_FAKE_MOVIE_PY = '''\
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
'''

_FAKE_TV_PY = _FAKE_MOVIE_PY.replace("Movie", "TV")

_FAKE_EXTERNAL_PY = '''\
print("External watchlists starting")
print("External watchlists done")
'''

_FAKE_RUN_SH = '''#!/bin/bash
echo "full run starting"
echo "full run done"
'''

_FAKE_RUN_PS1 = '''
Write-Host "full run starting"
Write-Host "full run done"
'''

_CONFIG_YML = '''\
plex:
  url: "http://localhost:32400"
  token: "not-a-real-token"
users:
  list: "alice, bob"
  preferences:
    alice:
      display_name: "Alice A"
general:
  # Off by default in this shared fixture so the update-banner context
  # processor (web/app.py) doesn't make a real network call on every
  # single web test's template render - tests that specifically exercise
  # the update banner (tests/test_web_update_banner.py) write their own
  # config.yml with a non-off update_mode.
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
'''


@pytest.fixture
def curatarr_web_root(tmp_path):
    """A throwaway fake curatarr project root for web/ tests.

    Mirrors the real repo layout that web/app.py and web/job_runner.py
    expect (config/config.yml, logs/, recommendations/external/,
    recommenders/*.py, run.sh/run.ps1) without touching the real repo
    or running the real (slow, Plex/TMDB-dependent) recommenders.
    """
    root = tmp_path
    (root / 'config').mkdir()
    (root / 'config' / 'config.yml').write_text(_CONFIG_YML, encoding='utf-8')
    (root / 'logs').mkdir()
    (root / 'recommendations' / 'external').mkdir(parents=True)
    (root / 'recommenders').mkdir()
    (root / 'recommenders' / 'movie.py').write_text(_FAKE_MOVIE_PY, encoding='utf-8')
    (root / 'recommenders' / 'tv.py').write_text(_FAKE_TV_PY, encoding='utf-8')
    (root / 'recommenders' / 'external.py').write_text(_FAKE_EXTERNAL_PY, encoding='utf-8')
    (root / 'run.sh').write_text(_FAKE_RUN_SH, encoding='utf-8')
    (root / 'run.ps1').write_text(_FAKE_RUN_PS1, encoding='utf-8')
    return str(root)
