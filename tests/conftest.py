"""Shared fixtures for the web/ (Flask UI) test suite."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
