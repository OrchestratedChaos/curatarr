"""Round-trip YAML load/save for the web UI config screens.

utils.config.load_config (used for *reading* at run time) uses plain
pyyaml safe_load/dump, which is fine for the recommenders but would
silently drop every comment in config.yml/tuning.yml/sonarr.yml/
radarr.yml/trakt.yml if used to *write* them back out. This module uses
ruamel.yaml's round-trip mode instead: load a file into a CommentedMap,
mutate only the keys a given screen owns, and dump the same object back
out - untouched keys, comments, and ordering all survive.

Writes are atomic: dumped to a temp file in the same directory, then
os.replace()'d over the target, so a crash mid-write (or a validation
bug that slips through) never leaves a half-written config file behind.
"""

import os
import tempfile
from typing import Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

# Matches the plain-yaml.dump() formatting the rest of the codebase
# already produces (utils/migrate_config.py, config.example.yml, etc.):
# list items at the same column as their parent key, not indented an
# extra level under the dash.
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=2, offset=0)
_yaml.width = 4096  # don't hard-wrap long values (URLs, tokens)

# Config keys the web UI never renders in HTML and never overwrites with
# a blank submitted value (blank means "keep the existing secret").
SECRET_KEYS = frozenset({
    'token', 'api_key', 'client_secret', 'access_token', 'refresh_token', 'password',
})

# Module file name -> which top-level config key it lives under once
# loaded, mirroring utils.config._load_module_configs. 'config' is the
# root file itself (plex/tmdb/tautulli/users/general live there
# directly, not merged under a module key).
MODULE_FILES = ('config', 'tuning', 'sonarr', 'radarr', 'trakt')


def config_dir(project_root: str) -> str:
    return os.path.join(project_root, 'config')


def module_path(project_root: str, name: str) -> str:
    """*name* is one of MODULE_FILES ('config', 'tuning', 'sonarr', ...)."""
    if name not in MODULE_FILES:
        raise ValueError(f"Unknown config module: {name}")
    return os.path.join(config_dir(project_root), f'{name}.yml')


def load_module(path: str) -> CommentedMap:
    """Load one YAML module file in round-trip mode.

    Returns an empty CommentedMap (not an error) if the file doesn't
    exist yet - module files (tuning.yml, trakt.yml, radarr.yml,
    sonarr.yml) are all optional, and a fresh install may not have them.
    """
    if not os.path.isfile(path):
        return CommentedMap()
    with open(path, 'r', encoding='utf-8') as f:
        data = _yaml.load(f)
    return data if data is not None else CommentedMap()


def save_module(path: str, data: CommentedMap) -> None:
    """Atomically write *data* to *path* as round-trip YAML.

    Writes to a temp file in the same directory first, then renames it
    over the target - a crash partway through never corrupts the
    existing file, and readers never see a partially-written one.
    """
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.tmp-', suffix='.yml', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            _yaml.dump(data, f)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def is_secret_field(key: str) -> bool:
    return key in SECRET_KEYS


def merge_secret(existing: Optional[str], submitted: Optional[str]) -> str:
    """A blank submitted value keeps the existing secret; non-blank overwrites.

    This is the one rule that lets the UI show a "value configured, enter
    a new one to change it" field instead of ever round-tripping a real
    secret through the browser.
    """
    submitted = (submitted or '').strip()
    if not submitted:
        return existing or ''
    return submitted


def secret_status(value: Optional[str]) -> str:
    """'configured' / 'not set' - for display only, never the value itself."""
    return 'configured' if (value or '').strip() else 'not set'


def parse_csv_list(value: Optional[str]) -> list:
    """'a, b, c' -> ['a', 'b', 'c'], dropping blanks. Used for the small
    comma-separated list fields (plex_users, exclude_genres, etc.)."""
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]


def format_csv_list(value) -> str:
    """Inverse of parse_csv_list, for pre-filling a text input from a
    YAML list (or a legacy comma-string) on GET."""
    if not value:
        return ''
    if isinstance(value, str):
        return value
    return ', '.join(str(item) for item in value)
