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
import shutil
from datetime import datetime
from typing import Optional

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
]

# Feature modules (each gets its own file)
FEATURE_MODULES = ['trakt', 'radarr', 'sonarr']


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

    if dry_run:
        print("\n=== Dry Run - Would create these files ===\n")
        print(f"config.yml (slimmed to {len(core_config)} sections)")
        if tuning_config:
            print(f"tuning.yml ({len(tuning_config)} sections)")
        for feature, cfg in feature_configs.items():
            print(f"{feature}.yml")
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
