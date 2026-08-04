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

Also the home for commit_modules (the validate-then-save-every-module
orchestration every config screen's *_save route calls through) and any
other constant/helper shared by more than one of those screens
(USER_MODE_CHOICES below) - see web/config_app.py's docstring for the
full per-screen module layout this is a sibling of.
"""

import logging
import os
import shutil
import tempfile
from typing import Dict, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from utils.helpers import harden_file_permissions

logger = logging.getLogger("curatarr")

# 'mapping' (default) / 'per_user' / 'combined' - the three
# plex_users-to-*arr-account sync-safety modes shared by the
# Connections and Settings screens (web/config_connections.py,
# web/config_settings.py) - one place to change the choice set both
# screens' user_mode <select> options and server-side validate_choice
# calls read from.
USER_MODE_CHOICES = ("mapping", "per_user", "combined")

# Global *arr defaults (#339). These live in sonarr.yml/radarr.yml and are
# what a library's own `arr:` block overrides field by field - so the
# global value is what applies to every library that does not override it,
# which until now could only be edited by hand. Choice sets mirror what
# Sonarr/Radarr themselves accept; anything outside them is rejected at
# validation rather than written and later failing mid-sync.
SERIES_TYPE_CHOICES = ("standard", "daily", "anime")
MONITOR_OPTION_CHOICES = (
    "none",
    "all",
    "future",
    "missing",
    "existing",
    "pilot",
    "firstSeason",
    "lastSeason",
    "latestSeason",
)
MINIMUM_AVAILABILITY_CHOICES = ("announced", "inCinemas", "released", "preDB")

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
SECRET_KEYS = frozenset(
    {
        "token",
        "api_key",
        "client_secret",
        "access_token",
        "refresh_token",
        "password",
    }
)

# Module file name -> which top-level config key it lives under once
# loaded, mirroring utils.config._load_module_configs. 'config' is the
# root file itself (plex/tmdb/tautulli/users/general live there
# directly, not merged under a module key).
MODULE_FILES = ("config", "tuning", "sonarr", "radarr", "trakt")


def config_dir(project_root: str) -> str:
    return os.path.join(project_root, "config")


def module_path(project_root: str, name: str) -> str:
    """*name* is one of MODULE_FILES ('config', 'tuning', 'sonarr', ...)."""
    if name not in MODULE_FILES:
        raise ValueError(f"Unknown config module: {name}")
    return os.path.join(config_dir(project_root), f"{name}.yml")


def load_module(path: str) -> CommentedMap:
    """Load one YAML module file in round-trip mode.

    Returns an empty CommentedMap (not an error) if the file doesn't
    exist yet - module files (tuning.yml, trakt.yml, radarr.yml,
    sonarr.yml) are all optional, and a fresh install may not have them.
    """
    if not os.path.isfile(path):
        return CommentedMap()
    with open(path, "r", encoding="utf-8") as f:
        data = _yaml.load(f)
    return data if data is not None else CommentedMap()


def save_module(path: str, data: CommentedMap) -> None:
    """Atomically write *data* to *path* as round-trip YAML.

    Writes to a temp file in the same directory first, then renames it
    over the target - a crash partway through never corrupts the
    existing file, and readers never see a partially-written one.

    *path* ends up owner-only readable/writable (0o600) - config.yml/
    trakt.yml/sonarr.yml/radarr.yml hold the Plex token and integration
    API keys/tokens. tempfile.mkstemp() already creates its temp file
    0o600 on POSIX, and os.replace() carries that over, so this was
    already true today - but only as an implementation detail of
    mkstemp, not something to keep relying on implicitly. Explicit here
    so it stays true even if the temp-file mechanism ever changes.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".yml", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _yaml.dump(data, f)
        os.replace(tmp_path, path)
        harden_file_permissions(path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def ensure_section(parent: CommentedMap, key: str) -> CommentedMap:
    """Return parent[key] as a CommentedMap, creating one if the key is
    missing OR present-but-null.

    A bare ``plex:`` / ``general:`` / ``movies:`` line (no nested keys)
    parses through ruamel/pyyaml as key -> None, not key -> {}. Plain
    ``parent.setdefault(key, CommentedMap())`` doesn't help in that case
    since the key already exists (with a None value) - the very next
    ``parent[key]['field'] = ...`` would then raise TypeError on the
    None. Any hand-edited or partially-migrated config file can produce
    this shape, so every _apply_* writer across web/config_connections.py,
    config_users.py, config_libraries.py, and config_settings.py goes
    through this helper instead of setdefault() before mutating a
    sub-section, so a null section becomes an empty mapping instead of
    a 500 (and a half-written save, since sibling sections already
    written to their own module file before the crash would otherwise
    stay saved while this one - and everything after it - never runs).
    """
    section = parent.get(key)
    if not isinstance(section, CommentedMap):
        section = CommentedMap()
        parent[key] = section
    return section


def validate_merge(project_root: str, modules: Dict[str, CommentedMap]) -> Optional[str]:
    """Dry-run utils.load_config's full merge against a throwaway temp
    copy of config/ with *modules* substituted in, so a value that
    passes this screen's own field validation (config_validate.py) but
    still breaks the merge some other way (e.g. utils.config's own
    parsing) is caught BEFORE any real file on disk is touched.

    This closes the gap in the old post-write-only reload check, which
    could only report a broken merge *after* save_module had already
    replaced the real files - too late for the operator whose next
    scheduled run would already be reading the broken config.

    *modules* maps MODULE_FILES names ('config', 'tuning', 'sonarr',
    'radarr', 'trakt') to the CommentedMap that would be written for
    that module; every module file not being changed by this save is
    copied through unchanged from the real config dir, so the merge is
    evaluated against the actual resulting config, not just the files
    one screen owns.

    Returns None if the merge is clean, or an error message otherwise.
    """
    from utils import load_config as _load_config

    src_dir = config_dir(project_root)
    with tempfile.TemporaryDirectory(prefix="curatarr-config-validate-") as tmp_dir:
        if os.path.isdir(src_dir):
            for name in os.listdir(src_dir):
                src_path = os.path.join(src_dir, name)
                if os.path.isfile(src_path):
                    shutil.copy2(src_path, os.path.join(tmp_dir, name))
        for name, data in modules.items():
            save_module(os.path.join(tmp_dir, f"{name}.yml"), data)
        try:
            _load_config(os.path.join(tmp_dir, "config.yml"))
        except Exception as exc:
            return str(exc)
    return None


def commit_modules(project_root: str, modules: Dict[str, CommentedMap]) -> Optional[str]:
    """Validate *modules* (module-file-name -> CommentedMap to write) via
    a dry-run merge (validate_merge above) BEFORE writing anything for
    real, then save every module. Field-level validation (weights sum,
    URLs, choices - see config_validate.py) happens even earlier, before
    this is ever called, which is what actually prevents most bad input
    from being considered at all. This is the second, defense-in-depth
    check: it catches a value that's individually well-typed but still
    breaks utils.load_config's merge some other way - and catches it on
    a throwaway temp copy, so a bad save can never reach the real config
    files (nor leave some of a multi-file save applied and others not).

    Shared by every config screen's *_save route (web/config_connections.py,
    web/config_users.py, web/config_libraries.py, web/config_settings.py)
    - lives here rather than in any one of them since none of those
    screens owns it more than another.

    Returns an error message (and writes nothing) if the dry-run merge
    fails, else None once every module in *modules* has been saved.
    """
    error = validate_merge(project_root, modules)
    if error:
        logger.error(f"Config merge validation failed - nothing saved: {error}")
        return error
    for name, data in modules.items():
        save_module(module_path(project_root, name), data)
    return None


def is_secret_field(key: str) -> bool:
    return key in SECRET_KEYS


def merge_secret(existing: Optional[str], submitted: Optional[str]) -> str:
    """A blank submitted value keeps the existing secret; non-blank overwrites.

    This is the one rule that lets the UI show a "value configured, enter
    a new one to change it" field instead of ever round-tripping a real
    secret through the browser.
    """
    submitted = (submitted or "").strip()
    if not submitted:
        return existing or ""
    return submitted


def secret_status(value: Optional[str]) -> str:
    """'configured' / 'not set' - for display only, never the value itself."""
    return "configured" if (value or "").strip() else "not set"


def secret_status_with_env(section: str, key: str, disk_value: Optional[str]) -> str:
    """secret_status(), but environment-variable-aware (#289): if an
    operator has set this field's env var override (see
    utils.config.ENV_VAR_OVERRIDES - the same list load_config() itself
    uses, so this can never silently drift out of sync with what's
    actually active at runtime), that value wins for status purposes
    too, exactly like it wins for the real value at run time. Without
    this, an install configured entirely via the environment (Docker
    secrets, an orchestrator's own secrets management - never written
    to config.yml/sonarr.yml/etc at all) would show a misleading "not
    set" here despite working correctly.

    Args:
        section: Config section (e.g. "plex", "trakt")
        key: Config key within that section (e.g. "token", "client_secret")
        disk_value: The value as saved on disk (or merged with a
            just-submitted form value) - used only when no env override
            is set.
    """
    from utils.config import get_env_override

    return secret_status(get_env_override(section, key) or disk_value)


def parse_csv_list(value: Optional[str]) -> list:
    """'a, b, c' -> ['a', 'b', 'c'], dropping blanks. Used for the small
    comma-separated list fields (plex_users, exclude_genres, etc.)."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def existing_library_secret(core: CommentedMap, library_id: Optional[str]) -> str:
    """Current arr.instance.api_key for the on-disk library whose id is
    *library_id*, or '' if that id doesn't match any existing entry (a
    brand new library row being added this submission) or has no
    instance api_key configured.

    Used by web.config_libraries (_apply_libraries / _libraries_view) so
    a blank instance_api_key submission keeps
    whatever secret was already saved for that specific library row -
    matched by its immutable id, not by list position, since rows can
    be reordered/added/removed within the same submission.
    """
    if not library_id:
        return ""
    for entry in core.get("libraries") or []:
        entry = entry or {}
        if entry.get("id") == library_id:
            arr = entry.get("arr") or {}
            instance = arr.get("instance") or {}
            return instance.get("api_key", "") or ""
    return ""


def format_csv_list(value) -> str:
    """Inverse of parse_csv_list, for pre-filling a text input from a
    YAML list (or a legacy comma-string) on GET."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return ", ".join(str(item) for item in value)
