#!/usr/bin/env python3
"""
Create smart collections for all user recommendations
Smart collections auto-update based on labels
"""

import os
import yaml
from plexapi.server import PlexServer

# Load config from root
config_path = os.path.join(os.path.dirname(__file__), 'config.yml')
try:
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    print(f"Error: config.yml not found at {config_path}")
    print("Please ensure config.yml exists in the project root.")
    exit(1)

PLEX_URL = config['plex']['url']
PLEX_TOKEN = config['plex']['token']
USERS = [u.strip() for u in config['users']['list'].split(',')]
USER_PREFS = config['users']['preferences']

def create_smart_collection(section, user, label):
    """Create a smart collection that filters by label using PlexAPI"""
    # Use display name from preferences, fallback to username
    display_name = USER_PREFS.get(user, {}).get('display_name', user)

    if section.type == 'movie':
        collection_name = f"🎬 Recommended - {display_name}"
    else:
        collection_name = f"📺 Recommended - {display_name}"

    # Check if collection already exists
    try:
        existing = section.collection(collection_name)
        print(f"  ⚬ Already exists: {collection_name}")
        return True
    except:
        pass  # Collection doesn't exist, create it

    try:
        # Create smart collection using PlexAPI's proper method
        # Use search filters with label parameter
        section.createCollection(
            title=collection_name,
            smart=True,
            limit=None,
            libtype=section.TYPE,
            sort='titleSort:asc',
            **{'label': label}  # Filter by label
        )
        print(f"  ✓ Created: {collection_name}")
        return True
    except Exception as e:
        print(f"  ✗ Failed to create: {collection_name}")
        print(f"     Error: {e}")
        return False

def main():
    print("=== Creating Smart Collections for Recommendations ===\n")

    # Connect to Plex
    try:
        plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    except Exception as e:
        print(f"Error connecting to Plex: {e}")
        exit(1)

    # Get libraries
    movie_lib_name = config['plex'].get('movie_library', 'Movies')
    tv_lib_name = config['plex'].get('tv_library', 'TV Shows')

    try:
        movies = plex.library.section(movie_lib_name)
        tv = plex.library.section(tv_lib_name)
    except Exception as e:
        print(f"Error loading libraries: {e}")
        exit(1)

    print(f"Loaded libraries:")
    print(f"  {movie_lib_name}: {movies.key}")
    print(f"  {tv_lib_name}: {tv.key}")
    print()

    print("Creating smart collections...\n")

    # Create smart collections for each user
    for user in USERS:
        print(f"User: {user}")

        label = f"Recommended_{user}"

        # Movies
        create_smart_collection(movies, user, label)

        # TV Shows
        create_smart_collection(tv, user, label)

        print()

    print("✓ All smart collections created!")
    print("\nThese collections will auto-update when labels change.")
    print("They should now appear on your Plex landing pages.")

if __name__ == "__main__":
    main()
