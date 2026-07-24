"""
Config migration script for Curatarr 2.0.

Splits monolithic config.yml into modular config files:
- config.yml (essentials only)
- tuning.yml (display/scoring options)
- trakt.yml (if trakt section exists)
- radarr.yml (if radarr section exists)
- sonarr.yml (if sonarr section exists)

Usage:
    python3 -m utils.migrate_config [config_path]
"""

import os
import re
import shutil
from datetime import datetime
from typing import List, Optional

import yaml


# Sections that belong in each module file
TUNING_SECTIONS = [
    'movies',
    'tv',
    'collections',
    'external_recommendations',
    'recency_decay',
    'rating_multipliers',
    'negative_signals',
]

# Sections that stay in main config.yml
CORE_SECTIONS = [
    'plex',
    'tmdb',
    'users',
    'general',
    'streaming_services',
    'logging',
    'platform',
    'libraries',
]

# Feature modules (each gets its own file)
FEATURE_MODULES = ['trakt', 'radarr', 'sonarr']

# Legacy global radarr.yml/sonarr.yml routing field -> unified library
# arr.* field name, for fields whose name differs by media type. Mirrors
# utils.config._ARR_FIELD_ALIASES (kept local here to avoid a module-level
# dependency between the two standalone utilities).
_LEGACY_ARR_FIELD_ALIASES = {
    'movie': {'search': 'search_for_movie'},
    'tv': {'search': 'search_for_series'},
}

# Legacy routing fields to fold into a library's arr block, by media type.
_LEGACY_ARR_ROUTING_FIELDS = {
    'movie': ['root_folder', 'quality_profile', 'tag', 'monitor', 'search', 'minimum_availability'],
    'tv': ['root_folder', 'quality_profile', 'tag', 'monitor', 'search', 'series_type'],
}


def needs_migration(config: dict) -> bool:
    """Check if config.yml contains sections that should be in module files."""
    # Check for tuning sections in root
    for section in TUNING_SECTIONS:
        if section in config:
            return True

    # Check for feature modules in root
    for module in FEATURE_MODULES:
        if module in config:
            return True

    # Check for nested radarr/sonarr (old format)
    if 'movies' in config and 'radarr' in config.get('movies', {}):
        return True
    if 'tv' in config and 'sonarr' in config.get('tv', {}):
        return True

    # Additive (#157 Phase 1): legacy single-library plex config not yet
    # folded into a 'libraries' list also needs migration.
    if not config.get('libraries'):
        plex_config = config.get('plex', config.get('PLEX', {})) or {}
        if plex_config.get('movie_library') or plex_config.get('tv_library'):
            return True

    return False


def extract_tuning_config(config: dict) -> dict:
    """Extract tuning sections from config."""
    tuning = {}

    for section in TUNING_SECTIONS:
        if section in config:
            tuning[section] = config[section]

    return tuning


def extract_feature_config(config: dict, feature: str) -> Optional[dict]:
    """Extract a feature module config (trakt, radarr, sonarr)."""
    # Check root level first
    if feature in config:
        feature_config = config[feature]
        # Only extract if it has content and is enabled or has settings
        if feature_config and (feature_config.get('enabled', False) or len(feature_config) > 1):
            return feature_config

    # Check nested in movies/tv (old format for radarr/sonarr)
    if feature == 'radarr' and 'movies' in config:
        if 'radarr' in config['movies']:
            return config['movies']['radarr']
    if feature == 'sonarr' and 'tv' in config:
        if 'sonarr' in config['tv']:
            return config['tv']['sonarr']

    return None


def _slugify_library_id(name: str) -> str:
    """Derive a stable slug id from a library name (e.g. "TV Shows" -> "tv-shows")."""
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').strip().lower()).strip('-')
    return slug or 'library'


def _fold_legacy_arr_routing(legacy_config: dict, media_type: str) -> dict:
    """
    Fold a legacy radarr.yml/sonarr.yml block's routing fields into the
    unified per-library arr.* schema. Only fields actually present in
    legacy_config are copied, to keep the migration diff minimal.
    """
    field_map = _LEGACY_ARR_FIELD_ALIASES.get(media_type, {})
    arr = {}
    for unified_field in _LEGACY_ARR_ROUTING_FIELDS.get(media_type, []):
        legacy_field = field_map.get(unified_field, unified_field)
        if legacy_field in legacy_config:
            arr[unified_field] = legacy_config[legacy_field]
    return arr


def _load_legacy_module(config: dict, config_dir: str, module: str) -> dict:
    """
    Get a feature module's config (radarr/sonarr) from the in-memory config
    dict being migrated, falling back to reading its standalone module file
    (e.g. radarr.yml) if the config has already been split into modular
    files (module sections then live outside the root config.yml dict).
    """
    if module in config and config[module]:
        return config[module]

    module_path = os.path.join(config_dir, f'{module}.yml')
    if os.path.exists(module_path):
        try:
            with open(module_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                return data or {}
        except Exception:
            return {}

    return {}


def migrate_to_libraries(config: dict, config_dir: str) -> Optional[List[dict]]:
    """
    Build an additive 'libraries' list from legacy single-library plex
    config, folding in routing fields from radarr.yml/sonarr.yml.

    Purely additive: does NOT remove plex.movie_library/plex.tv_library
    from config - both the legacy keys and the new 'libraries' list
    coexist. Idempotent: returns None if config already has a truthy
    'libraries' section, or if there's no legacy movie_library/tv_library
    to migrate from.

    Args:
        config: The config dict being migrated (may still be monolithic,
            or already split into modular files)
        config_dir: Directory containing config.yml and module files
            (radarr.yml, sonarr.yml)

    Returns:
        Two-entry list [movie library, tv library], or None if not
        applicable.
    """
    if config.get('libraries'):
        return None

    plex_config = config.get('plex', config.get('PLEX', {})) or {}
    movie_library = plex_config.get('movie_library')
    tv_library = plex_config.get('tv_library')

    if not movie_library and not tv_library:
        return None

    movie_library = movie_library or 'Movies'
    tv_library = tv_library or 'TV Shows'

    radarr_config = _load_legacy_module(config, config_dir, 'radarr')
    sonarr_config = _load_legacy_module(config, config_dir, 'sonarr')

    return [
        {
            'id': _slugify_library_id(movie_library),
            'name': movie_library,
            'section': movie_library,
            'media_type': 'movie',
            'arr': _fold_legacy_arr_routing(radarr_config, 'movie'),
        },
        {
            'id': _slugify_library_id(tv_library),
            'name': tv_library,
            'section': tv_library,
            'media_type': 'tv',
            'arr': _fold_legacy_arr_routing(sonarr_config, 'tv'),
        },
    ]


def migrate_update_mode(config: dict) -> Optional[str]:
    """
    Derive an explicit general.update_mode from the legacy
    general.auto_update flag, mirroring migrate_to_libraries()'s
    additive, idempotent pattern: does NOT remove auto_update - both
    keys coexist afterward, so utils.config.get_update_mode() (the
    actual runtime source of truth, consulted whether or not this
    physical migration ever runs) keeps working either way.

    Idempotent: returns None if general.update_mode is already set, or
    if there's no legacy auto_update to derive from (a fresh 'notify'
    default needs no persisted key at all).

    Note: an unquoted `update_mode: off` in hand-written YAML parses as
    the Python boolean False, not the string 'off' (YAML 1.1 boolean
    literals include on/off/yes/no) - checked explicitly below so that
    case still counts as "already set" rather than being treated as
    absent. Mirrors utils.config.get_update_mode()'s same handling.

    Args:
        config: The config dict being migrated

    Returns:
        'force' or 'off' derived from legacy auto_update, or None if no
        migration is applicable.
    """
    general = config.get('general') or {}
    if 'update_mode' in general and general.get('update_mode') is not None:
        return None
    if 'auto_update' not in general:
        return None
    return 'force' if general.get('auto_update') else 'off'


def build_core_config(config: dict) -> dict:
    """Build the slim core config.yml with essentials only."""
    core = {}

    for section in CORE_SECTIONS:
        if section in config:
            core[section] = config[section]

    return core


def migrate_config(config_path: str, dry_run: bool = False) -> dict:
    """
    Migrate a monolithic config.yml to modular config files.

    Args:
        config_path: Path to config.yml
        dry_run: If True, don't write files, just return what would be created

    Returns:
        Dict with keys: 'migrated' (bool), 'files_created' (list), 'backup_path' (str or None)
    """
    result = {
        'migrated': False,
        'files_created': [],
        'backup_path': None,
    }

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        return result

    # Load existing config
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    if not config:
        print("Config file is empty")
        return result

    # Check if migration is needed
    if not needs_migration(config):
        print("Config is already in modular format, no migration needed")
        return result

    config_dir = os.path.dirname(config_path) or '.'

    # Extract sections
    tuning_config = extract_tuning_config(config)
    core_config = build_core_config(config)

    # Extract feature modules
    feature_configs = {}
    for feature in FEATURE_MODULES:
        feature_config = extract_feature_config(config, feature)
        if feature_config:
            feature_configs[feature] = feature_config

    # Additive (#157 Phase 1): fold legacy plex.movie_library/tv_library +
    # radarr.yml/sonarr.yml routing into a 'libraries' list. Does not
    # remove the legacy plex keys.
    libraries = migrate_to_libraries(config, config_dir)
    if libraries:
        core_config['libraries'] = libraries

    # Additive: persist an explicit general.update_mode derived from the
    # legacy general.auto_update flag - same non-destructive pattern as
    # migrate_to_libraries() above, auto_update is kept, not removed.
    derived_update_mode = migrate_update_mode(core_config)
    if derived_update_mode:
        core_config.setdefault('general', {})['update_mode'] = derived_update_mode

    if dry_run:
        print("\n=== Dry Run - Would create these files ===\n")
        print(f"config.yml (slimmed to {len(core_config)} sections)")
        if tuning_config:
            print(f"tuning.yml ({len(tuning_config)} sections)")
        for feature, cfg in feature_configs.items():
            print(f"{feature}.yml")
        if libraries:
            print(f"config.yml would gain a 'libraries' section ({len(libraries)} entries)")
        if derived_update_mode:
            print(f"config.yml would gain general.update_mode: {derived_update_mode}")
        result['migrated'] = True
        return result

    # Backup original
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(config_dir, f'config.yml.backup.{timestamp}')
    shutil.copy2(config_path, backup_path)
    result['backup_path'] = backup_path
    print(f"Backed up original config to: {backup_path}")

    # Write tuning.yml if there are tuning sections
    if tuning_config:
        tuning_path = os.path.join(config_dir, 'tuning.yml')
        with open(tuning_path, 'w', encoding='utf-8') as f:
            f.write("# Curatarr Tuning Configuration\n")
            f.write("# Display options, weights, and scoring parameters\n\n")
            yaml.dump(tuning_config, f, default_flow_style=False, sort_keys=False)
        result['files_created'].append('tuning.yml')
        print(f"Created: tuning.yml")

    # Write feature module files
    for feature, feature_config in feature_configs.items():
        feature_path = os.path.join(config_dir, f'{feature}.yml')
        with open(feature_path, 'w', encoding='utf-8') as f:
            f.write(f"# Curatarr {feature.title()} Configuration\n\n")
            yaml.dump(feature_config, f, default_flow_style=False, sort_keys=False)
        result['files_created'].append(f'{feature}.yml')
        print(f"Created: {feature}.yml")

    # Write slimmed config.yml
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write("# Curatarr Configuration\n")
        f.write("# Core settings - see tuning.yml for display/scoring options\n\n")
        yaml.dump(core_config, f, default_flow_style=False, sort_keys=False)
    print(f"Updated: config.yml (slimmed to essentials)")

    result['migrated'] = True
    return result


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description='Migrate monolithic config.yml to modular format')
    parser.add_argument('config_path', nargs='?', default='config/config.yml', help='Path to config.yml')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    args = parser.parse_args()

    print(f"Migrating config: {args.config_path}")
    if args.dry_run:
        print("(Dry run mode - no files will be modified)\n")

    result = migrate_config(args.config_path, dry_run=args.dry_run)

    if result['migrated']:
        print("\nMigration complete!")
        if result['files_created']:
            print(f"Created {len(result['files_created'])} module file(s)")
    else:
        print("\nNo migration performed")


if __name__ == '__main__':
    main()
