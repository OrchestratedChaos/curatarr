#!/usr/bin/env python3
"""
Create smart collections for all user recommendations
Smart collections auto-update based on labels
"""

import os
import yaml
import requests
import urllib3
from urllib.parse import quote
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

def get_library_ids():
    """Auto-detect library IDs from Plex using configured library names"""
    movie_lib = config['plex'].get('movie_library', 'Movies')
    tv_lib = config['plex'].get('tv_library', 'TV Shows')

    try:
        url = f"{PLEX_URL}/library/sections"
        headers = {'X-Plex-Token': PLEX_TOKEN}
        response = requests.get(url, headers=headers, verify=False)

        if response.status_code != 200:
            print(f"Error fetching libraries from Plex (Status: {response.status_code})")
            print("Falling back to default IDs (Movies=1, TV Shows=2)")
            return {'Movies': 1, 'TV Shows': 2}

        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.content)

        libraries = {}
        for directory in root.findall('.//Directory'):
            title = directory.get('title')
            lib_id = int(directory.get('key'))
            lib_type = directory.get('type')

            if title == movie_lib and lib_type == 'movie':
                libraries['Movies'] = lib_id
            elif title == tv_lib and lib_type == 'show':
                libraries['TV Shows'] = lib_id

        if 'Movies' not in libraries or 'TV Shows' not in libraries:
            print(f"Warning: Could not find all libraries")
            print(f"  Looking for: '{movie_lib}' and '{tv_lib}'")
            print(f"  Found: {libraries}")
            print("Falling back to default IDs")
            return {'Movies': 1, 'TV Shows': 2}

        return libraries

    except Exception as e:
        print(f"Error detecting library IDs: {e}")
        print("Falling back to default IDs (Movies=1, TV Shows=2)")
        return {'Movies': 1, 'TV Shows': 2}

def collection_exists(lib_id, collection_name):
    """Check if a collection already exists in the library"""
    try:
        url = f"{PLEX_URL}/library/sections/{lib_id}/collections"
        headers = {'X-Plex-Token': PLEX_TOKEN}
        response = requests.get(url, headers=headers, verify=False)

        if response.status_code != 200:
            return False

        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.content)

        for collection in root.findall('.//Directory'):
            if collection.get('title') == collection_name:
                return True

        return False
    except Exception:
        return False

def create_smart_collection(lib_id, lib_name, user, label):
    """Create a smart collection that filters by label (if it doesn't exist)"""
    # Use display name from preferences, fallback to username
    display_name = USER_PREFS.get(user, {}).get('display_name', user)
    collection_name = f"🎬 Recommended - {display_name}" if lib_name == "Movies" else f"📺 Recommended - {display_name}"

    # Check if collection already exists
    if collection_exists(lib_id, collection_name):
        print(f"  ⚬ Already exists: {collection_name}")
        return True

    url = f"{PLEX_URL}/library/collections"
    headers = {'X-Plex-Token': PLEX_TOKEN}

    # Build the smart filter URI
    # Format: /library/sections/{sectionId}/all?label={label_value}
    filter_uri = f"/library/sections/{lib_id}/all?label={quote(label)}"

    # Smart collection parameters
    params = {
        'type': '1' if lib_name == 'Movies' else '2',  # 1=movie, 2=show
        'title': collection_name,
        'smart': '1',  # Make it smart
        'sectionId': lib_id,
        'uri': filter_uri,  # Use URI filter instead of label parameter
        'sort': 'titleSort:asc'  # Sort by title ascending
    }

    response = requests.post(url, headers=headers, params=params, verify=False)

    if response.status_code in [200, 201]:
        print(f"  ✓ Created: {collection_name}")
        return True
    else:
        print(f"  ✗ Failed to create: {collection_name} (Status: {response.status_code})")
        print(f"     Filter URI: {filter_uri}")
        return False

def main():
    print("=== Creating Smart Collections for Recommendations ===\n")

    # Auto-detect library IDs from Plex
    print("Detecting library IDs from Plex...")
    libraries = get_library_ids()
    print(f"  Movies: Library ID {libraries['Movies']}")
    print(f"  TV Shows: Library ID {libraries['TV Shows']}")
    print()

    print("Creating smart collections...\n")

    # Create smart collections for each user
    for user in USERS:
        print(f"User: {user}")

        # Movies
        label = f"Recommended_{user}"
        create_smart_collection(libraries['Movies'], 'Movies', user, label)

        # TV Shows
        create_smart_collection(libraries['TV Shows'], 'TV Shows', user, label)

        print()

    print("✓ All smart collections created!")
    print("\nThese collections will auto-update when labels change.")
    print("They should now appear on your Plex landing pages.")

if __name__ == "__main__":
    main()
