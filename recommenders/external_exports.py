"""
Export recommendations to external services.
Handles Trakt, Sonarr, Radarr, MDBList, and Simkl exports.
"""

import json
import logging
import os
import sys
import requests
from typing import Dict, List, Optional, Any

from utils import (
    CYAN, GREEN, RESET,
    print_status, log_warning, log_error, clickable_link,
    get_authenticated_trakt_client, TraktAPIError, TraktAuthError,
    create_sonarr_client, SonarrAPIError,
    create_radarr_client, RadarrAPIError,
    create_mdblist_client, MDBListAPIError,
    create_simkl_client, SimklAPIError, SimklAuthError,
)

logger = logging.getLogger('curatarr')

# Batch size for Trakt sync operations
TRAKT_BATCH_SIZE = 100


def get_imdb_id(tmdb_api_key: str, tmdb_id: int, media_type: str = 'movie') -> Optional[str]:
    """Fetch IMDB ID from TMDB external IDs endpoint."""
    try:
        media = 'movie' if media_type == 'movie' else 'tv'
        url = f"https://api.themoviedb.org/3/{media}/{tmdb_id}/external_ids"
        response = requests.get(url, params={'api_key': tmdb_api_key}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get('imdb_id')
    except (requests.RequestException, KeyError) as e:
        logger.debug(f"Error fetching IMDB ID for TMDB {tmdb_id}: {e}")
    return None


def _sync_items_in_batches(
    items: List[Dict],
    trakt_client: Any,
    media_type: str,
    result_key: str
) -> int:
    """
    Sync items to Trakt in batches with progress display.

    Args:
        items: List of items to sync (IMDB ID dicts)
        trakt_client: Authenticated Trakt client
        media_type: 'movies' or 'shows' for display
        result_key: Key to extract from result ('movies' or 'episodes')

    Returns:
        Total count of items added
    """
    if not items:
        return 0

    total_added = 0
    for i in range(0, len(items), TRAKT_BATCH_SIZE):
        batch = items[i:i + TRAKT_BATCH_SIZE]
        batch_num = (i // TRAKT_BATCH_SIZE) + 1
        total_batches = (len(items) + TRAKT_BATCH_SIZE - 1) // TRAKT_BATCH_SIZE
        sys.stdout.write(f"\r  Syncing {media_type}: batch {batch_num}/{total_batches}")
        sys.stdout.flush()

        if media_type == 'movies':
            result = trakt_client.add_to_history(movies=batch)
        else:
            result = trakt_client.add_to_history(shows=batch)

        total_added += result.get('added', {}).get(result_key, 0)

    print()  # newline after progress
    return total_added


def collect_imdb_ids(
    categorized: Dict,
    tmdb_api_key: str,
    media_type: str = 'movie',
    flatten_func: callable = None
) -> List[str]:
    """
    Collect IMDB IDs from categorized items.

    Args:
        categorized: Dict with categorized items
        tmdb_api_key: TMDB API key for ID lookups
        media_type: 'movie' or 'tv'
        flatten_func: Function to flatten categorized items

    Returns:
        List of IMDB IDs
    """
    if flatten_func is None:
        # Inline fallback if no flatten function provided
        items = []
        for service_items in categorized.get('user_services', {}).values():
            items.extend(service_items)
        for service_items in categorized.get('other_services', {}).values():
            items.extend(service_items)
        items.extend(categorized.get('acquire', []))
    else:
        items = flatten_func(categorized)

    imdb_ids = []
    for item in items:
        tmdb_id = item.get('tmdb_id')
        if tmdb_id:
            imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, media_type)
            if imdb_id:
                imdb_ids.append(imdb_id)
    return imdb_ids


def export_to_trakt(config: Dict, all_users_data: List[Dict], tmdb_api_key: str) -> None:
    """
    Export recommendations to Trakt lists.

    Creates/updates lists named: "{prefix} - {username} - Movies/TV"

    Config options:
        trakt.enabled: Master switch for Trakt integration
        trakt.export.enabled: Enable export feature (default: true)
        trakt.export.auto_sync: Auto-sync on each run (default: true)
        trakt.export.user_mode: How to handle multiple Plex users:
            - mapping: Only export users in plex_users list (recommended)
            - per_user: Separate list for each Plex user
            - combined: All users combined into one list
        trakt.export.plex_users: List of Plex usernames to export (for mapping mode)
    """
    trakt_config = config.get('trakt', {})
    export_config = trakt_config.get('export', {})

    # Check if export is enabled
    if not trakt_config.get('enabled', False):
        return
    if not export_config.get('enabled', True):
        return
    # Check if auto_sync is enabled (can still manually export via HTML)
    if not export_config.get('auto_sync', True):
        return

    # Get authenticated Trakt client
    trakt_client = get_authenticated_trakt_client(config)
    if not trakt_client:
        log_warning("Trakt not authenticated - run setup wizard to authenticate")
        return

    list_prefix = export_config.get('list_prefix', 'Curatarr')
    trakt_username = trakt_client.get_username()
    user_mode = export_config.get('user_mode', 'mapping')
    plex_users = export_config.get('plex_users', [])

    # Safety check: mapping mode requires explicit plex_users configuration
    if user_mode == 'mapping':
        # Reject empty list, placeholder, or unconfigured
        invalid_configs = [[], ['YourPlexUsername'], None]
        if plex_users in invalid_configs or not plex_users:
            log_warning(
                "Trakt export: No plex_users configured.\n"
                "  Edit config.yml -> trakt.export.plex_users and add YOUR Plex username.\n"
                "  Example: plex_users: [\"jason\"]\n"
                "  This prevents accidentally syncing other users' data to YOUR Trakt account."
            )
            return

    print(f"\n{CYAN}Exporting to Trakt...{RESET}")

    # Filter users based on mode
    if user_mode == 'mapping':
        # Only export users in the plex_users list (case-insensitive)
        plex_users_lower = [u.lower() for u in plex_users]
        users_to_export = [
            u for u in all_users_data
            if u['username'].lower() in plex_users_lower
        ]
        if not users_to_export:
            log_warning(
                f"Trakt export: No matching users found. Configured plex_users: {plex_users}\n"
                "  Check that your Plex username matches exactly."
            )
            return
    else:
        users_to_export = all_users_data

    # Handle combined mode - merge all users into one list
    if user_mode == 'combined':
        all_movie_imdb_ids = []
        all_show_imdb_ids = []
        for user_data in users_to_export:
            all_movie_imdb_ids.extend(
                collect_imdb_ids(user_data['movies_categorized'], tmdb_api_key, 'movie')
            )
            all_show_imdb_ids.extend(
                collect_imdb_ids(user_data['shows_categorized'], tmdb_api_key, 'tv')
            )
        # Deduplicate
        all_movie_imdb_ids = list(dict.fromkeys(all_movie_imdb_ids))
        all_show_imdb_ids = list(dict.fromkeys(all_show_imdb_ids))

        try:
            if all_movie_imdb_ids:
                movie_list_name = f"{list_prefix} - Movies"
                trakt_client.sync_list(
                    movie_list_name,
                    movies=all_movie_imdb_ids,
                    description="Combined movie recommendations from Curatarr"
                )
                movie_slug = movie_list_name.lower().replace(" ", "-").replace("_", "-")
                movie_url = f"https://trakt.tv/users/{trakt_username}/lists/{movie_slug}"
                print_status(f"  Combined: {len(all_movie_imdb_ids)} movies -> Trakt", "success")
                print(f"    {clickable_link(movie_url)}")

            if all_show_imdb_ids:
                show_list_name = f"{list_prefix} - TV"
                trakt_client.sync_list(
                    show_list_name,
                    shows=all_show_imdb_ids,
                    description="Combined TV recommendations from Curatarr"
                )
                show_slug = show_list_name.lower().replace(" ", "-").replace("_", "-")
                show_url = f"https://trakt.tv/users/{trakt_username}/lists/{show_slug}"
                print_status(f"  Combined: {len(all_show_imdb_ids)} shows -> Trakt", "success")
                print(f"    {clickable_link(show_url)}")

        except (TraktAPIError, TraktAuthError) as e:
            log_error(f"Failed to export combined list to Trakt: {e}")
        return

    # Per-user or mapping mode - separate list per user
    for user_data in users_to_export:
        display_name = user_data['display_name']
        movies_categorized = user_data['movies_categorized']
        shows_categorized = user_data['shows_categorized']

        # Collect IMDB IDs using helper
        movie_imdb_ids = collect_imdb_ids(movies_categorized, tmdb_api_key, 'movie')
        show_imdb_ids = collect_imdb_ids(shows_categorized, tmdb_api_key, 'tv')

        # Sync to Trakt lists
        try:
            if movie_imdb_ids:
                movie_list_name = f"{list_prefix} - {display_name} - Movies"
                trakt_client.sync_list(
                    movie_list_name,
                    movies=movie_imdb_ids,
                    description=f"Movie recommendations for {display_name} from Curatarr"
                )
                movie_slug = movie_list_name.lower().replace(" ", "-").replace("_", "-")
                movie_url = f"https://trakt.tv/users/{trakt_username}/lists/{movie_slug}"
                print_status(f"  {display_name}: {len(movie_imdb_ids)} movies -> Trakt", "success")
                print(f"    {clickable_link(movie_url)}")

            if show_imdb_ids:
                show_list_name = f"{list_prefix} - {display_name} - TV"
                trakt_client.sync_list(
                    show_list_name,
                    shows=show_imdb_ids,
                    description=f"TV recommendations for {display_name} from Curatarr"
                )
                show_slug = show_list_name.lower().replace(" ", "-").replace("_", "-")
                show_url = f"https://trakt.tv/users/{trakt_username}/lists/{show_slug}"
                print_status(f"  {display_name}: {len(show_imdb_ids)} shows -> Trakt", "success")
                print(f"    {clickable_link(show_url)}")

        except (TraktAPIError, TraktAuthError) as e:
            log_error(f"Failed to export {display_name} to Trakt: {e}")


def export_to_sonarr(config: Dict, all_users_data: List[Dict], tmdb_api_key: str) -> None:
    """
    Export TV recommendations to Sonarr.

    Adds recommended shows to Sonarr for tracking/downloading.

    Config options:
        sonarr.enabled: Master switch for Sonarr integration
        sonarr.auto_sync: Auto-add on each run (default: false)
        sonarr.user_mode: How to handle multiple Plex users:
            - mapping: Only export users in plex_users list (recommended)
            - per_user: All Plex users' recommendations
            - combined: Merge all users' recommendations
        sonarr.plex_users: List of Plex usernames to export (for mapping mode)
    """
    logger.debug("export_to_sonarr called")
    sonarr_config = config.get('sonarr', {})

    # Check if Sonarr is enabled and auto_sync is on
    if not sonarr_config.get('enabled', False):
        return
    if not sonarr_config.get('auto_sync', False):
        return

    # Create Sonarr client
    sonarr_client = create_sonarr_client(config)
    if not sonarr_client:
        log_warning("Sonarr not configured - check config/sonarr.yml")
        return

    # Test connection
    try:
        sonarr_client.test_connection()
        print(f"\n{CYAN}=== Exporting to Sonarr ==={RESET}")
        existing_count = len(sonarr_client.get_series())
        print(f"  Connected to Sonarr ({existing_count} existing shows)")
    except SonarrAPIError as e:
        log_error(f"Could not connect to Sonarr: {e}")
        return

    user_mode = sonarr_config.get('user_mode', 'mapping')
    plex_users = sonarr_config.get('plex_users', [])

    # Safety check: mapping mode requires explicit plex_users configuration
    if user_mode == 'mapping':
        invalid_configs = [[], ['YourPlexUsername'], None]
        if plex_users in invalid_configs or not plex_users:
            log_warning(
                "Sonarr export: No plex_users configured.\n"
                "  Edit sonarr.yml -> plex_users and add YOUR Plex username.\n"
                "  Example: plex_users: [\"jason\"]\n"
                "  This prevents accidentally adding other users' recommendations."
            )
            return

    # Filter users based on mode
    if user_mode == 'mapping':
        plex_users_lower = [u.lower() for u in plex_users]
        users_to_export = [
            u for u in all_users_data
            if u['username'].lower() in plex_users_lower
        ]
        if not users_to_export:
            log_warning(
                f"Sonarr export: No matching users found. Configured plex_users: {plex_users}\n"
                "  Check that your Plex username matches exactly."
            )
            return
    else:
        users_to_export = all_users_data

    # Get Sonarr settings from config
    root_folder = sonarr_config.get('root_folder', '/tv')
    quality_profile_name = sonarr_config.get('quality_profile', 'HD-1080p')
    tag_name = sonarr_config.get('tag', 'Curatarr')
    append_usernames = sonarr_config.get('append_usernames', False)
    monitored = sonarr_config.get('monitor', False)
    search_for_series = sonarr_config.get('search_for_series', False)
    series_type = sonarr_config.get('series_type', 'standard')
    season_folder = sonarr_config.get('season_folder', True)

    # Get quality profile ID
    quality_profile_id = sonarr_client.get_quality_profile_id(quality_profile_name)
    if not quality_profile_id:
        available = [p['name'] for p in sonarr_client.get_quality_profiles()]
        log_error(f"Quality profile '{quality_profile_name}' not found. Available: {available}")
        return

    # Validate root folder
    valid_root = sonarr_client.get_root_folder_path(root_folder)
    if not valid_root:
        available = [f['path'] for f in sonarr_client.get_root_folders()]
        log_error(f"Root folder '{root_folder}' not found. Available: {available}")
        return

    # Collect all shows to add (handle combined mode)
    if user_mode == 'combined':
        all_show_tvdb_ids = []
        for user_data in users_to_export:
            # Collect TVDB IDs directly from shows_categorized
            for category_shows in user_data['shows_categorized'].values():
                for show in category_shows:
                    tvdb_id = show.get('tvdb_id')
                    if tvdb_id:
                        all_show_tvdb_ids.append(tvdb_id)
        # Deduplicate
        all_show_tvdb_ids = list(dict.fromkeys(all_show_tvdb_ids))

        if not all_show_tvdb_ids:
            print_status("  No show recommendations to add", "info")
            return

        print(f"  Combined mode: Processing {len(all_show_tvdb_ids)} show recommendations...")

        # Get or create tag
        tag_id = sonarr_client.get_or_create_tag(tag_name)

        added = 0
        skipped = 0
        failed = 0

        for tvdb_id in all_show_tvdb_ids:
            if sonarr_client.series_exists(tvdb_id):
                skipped += 1
                continue

            # Look up series
            series_data = sonarr_client.lookup_series(tvdb_id)
            if not series_data:
                logger.debug(f"Could not find series for TVDB ID: {tvdb_id}")
                failed += 1
                continue

            try:
                sonarr_client.add_series(
                    tvdb_id=tvdb_id,
                    title=series_data['title'],
                    root_folder_path=valid_root,
                    quality_profile_id=quality_profile_id,
                    monitored=monitored,
                    season_folder=season_folder,
                    series_type=series_type,
                    tag_ids=[tag_id],
                    search_for_missing_episodes=search_for_series
                )
                added += 1
                print(f"  {GREEN}Added: {series_data['title']}{RESET}")
            except SonarrAPIError as e:
                logger.debug(f"Failed to add {series_data['title']}: {e}")
                failed += 1

        print_status(f"  Combined: {added} added, {skipped} already exist, {failed} failed", "success")
        return

    # Per-user or mapping mode
    for user_data in users_to_export:
        display_name = user_data['display_name']
        shows_categorized = user_data['shows_categorized']

        # Collect TVDB IDs for shows
        show_tvdb_ids = []
        for category_shows in shows_categorized.values():
            for show in category_shows:
                tvdb_id = show.get('tvdb_id')
                if tvdb_id:
                    show_tvdb_ids.append(tvdb_id)
        # Deduplicate
        show_tvdb_ids = list(dict.fromkeys(show_tvdb_ids))

        if not show_tvdb_ids:
            print_status(f"  {display_name}: No show recommendations to add", "info")
            continue

        print(f"  {display_name}: Processing {len(show_tvdb_ids)} show recommendations...")

        # Get or create tag (optionally with username)
        user_tag = f"{tag_name}-{display_name}" if append_usernames else tag_name
        tag_id = sonarr_client.get_or_create_tag(user_tag)

        added = 0
        skipped = 0
        failed = 0

        for tvdb_id in show_tvdb_ids:
            if sonarr_client.series_exists(tvdb_id):
                skipped += 1
                continue

            # Look up series
            series_data = sonarr_client.lookup_series(tvdb_id)
            if not series_data:
                logger.debug(f"Could not find series for TVDB ID: {tvdb_id}")
                failed += 1
                continue

            try:
                sonarr_client.add_series(
                    tvdb_id=tvdb_id,
                    title=series_data['title'],
                    root_folder_path=valid_root,
                    quality_profile_id=quality_profile_id,
                    monitored=monitored,
                    season_folder=season_folder,
                    series_type=series_type,
                    tag_ids=[tag_id],
                    search_for_missing_episodes=search_for_series
                )
                added += 1
                print(f"    {GREEN}Added: {series_data['title']}{RESET}")
            except SonarrAPIError as e:
                logger.debug(f"Failed to add {series_data['title']}: {e}")
                failed += 1

        print_status(f"  {display_name}: {added} added, {skipped} already exist, {failed} failed", "success")


def export_to_radarr(config: Dict, all_users_data: List[Dict], tmdb_api_key: str) -> None:
    """
    Export movie recommendations to Radarr.

    Adds recommended movies to Radarr for tracking/downloading.

    Config options:
        radarr.enabled: Master switch for Radarr integration
        radarr.auto_sync: Auto-add on each run (default: false)
        radarr.user_mode: How to handle multiple Plex users:
            - mapping: Only export users in plex_users list (recommended)
            - per_user: All Plex users' recommendations
            - combined: Merge all users' recommendations
        radarr.plex_users: List of Plex usernames to export (for mapping mode)
    """
    logger.debug("export_to_radarr called")
    radarr_config = config.get('radarr', {})

    # Check if Radarr is enabled and auto_sync is on
    if not radarr_config.get('enabled', False):
        return
    if not radarr_config.get('auto_sync', False):
        return

    # Create Radarr client
    radarr_client = create_radarr_client(config)
    if not radarr_client:
        log_warning("Radarr not configured - check config/radarr.yml")
        return

    # Test connection
    try:
        radarr_client.test_connection()
        print(f"\n{CYAN}=== Exporting to Radarr ==={RESET}")
        existing_count = len(radarr_client.get_movies())
        print(f"  Connected to Radarr ({existing_count} existing movies)")
    except RadarrAPIError as e:
        log_error(f"Could not connect to Radarr: {e}")
        return

    user_mode = radarr_config.get('user_mode', 'mapping')
    plex_users = radarr_config.get('plex_users', [])

    # Safety check: mapping mode requires explicit plex_users configuration
    if user_mode == 'mapping':
        invalid_configs = [[], ['YourPlexUsername'], None]
        if plex_users in invalid_configs or not plex_users:
            log_warning(
                "Radarr export: No plex_users configured.\n"
                "  Edit radarr.yml -> plex_users and add YOUR Plex username.\n"
                "  Example: plex_users: [\"jason\"]\n"
                "  This prevents accidentally adding other users' recommendations."
            )
            return

    # Filter users based on mode
    if user_mode == 'mapping':
        plex_users_lower = [u.lower() for u in plex_users]
        users_to_export = [
            u for u in all_users_data
            if u['username'].lower() in plex_users_lower
        ]
        if not users_to_export:
            log_warning(
                f"Radarr export: No matching users found. Configured plex_users: {plex_users}\n"
                "  Check that your Plex username matches exactly."
            )
            return
    else:
        users_to_export = all_users_data

    # Get Radarr settings from config
    root_folder = radarr_config.get('root_folder', '/movies')
    quality_profile_name = radarr_config.get('quality_profile', 'HD-1080p')
    minimum_availability = radarr_config.get('minimum_availability', 'released')
    tag_name = radarr_config.get('tag', 'Curatarr')
    append_usernames = radarr_config.get('append_usernames', False)
    monitored = radarr_config.get('monitor', False)
    search_for_movie = radarr_config.get('search_for_movie', False)

    # Get quality profile ID
    quality_profile_id = radarr_client.get_quality_profile_id(quality_profile_name)
    if not quality_profile_id:
        available = [p['name'] for p in radarr_client.get_quality_profiles()]
        log_error(f"Quality profile '{quality_profile_name}' not found. Available: {available}")
        return

    # Validate root folder
    valid_root = radarr_client.get_root_folder_path(root_folder)
    if not valid_root:
        available = [f['path'] for f in radarr_client.get_root_folders()]
        log_error(f"Root folder '{root_folder}' not found. Available: {available}")
        return

    # Collect all movies to add (handle combined mode)
    if user_mode == 'combined':
        all_movie_tmdb_ids = []
        for user_data in users_to_export:
            # Collect TMDB IDs directly from movies_categorized
            for category_movies in user_data['movies_categorized'].values():
                for movie in category_movies:
                    tmdb_id = movie.get('tmdb_id')
                    if tmdb_id:
                        all_movie_tmdb_ids.append(tmdb_id)
        # Deduplicate
        all_movie_tmdb_ids = list(dict.fromkeys(all_movie_tmdb_ids))

        if not all_movie_tmdb_ids:
            print_status("  No movie recommendations to add", "info")
            return

        print(f"  Combined mode: Processing {len(all_movie_tmdb_ids)} movie recommendations...")

        # Get or create tag
        tag_id = radarr_client.get_or_create_tag(tag_name)

        added = 0
        skipped = 0
        failed = 0

        for tmdb_id in all_movie_tmdb_ids:
            if radarr_client.movie_exists(tmdb_id):
                skipped += 1
                continue

            # Look up movie
            movie_data = radarr_client.lookup_movie(tmdb_id)
            if not movie_data:
                logger.debug(f"Could not find movie for TMDB ID: {tmdb_id}")
                failed += 1
                continue

            try:
                radarr_client.add_movie(
                    tmdb_id=tmdb_id,
                    title=movie_data['title'],
                    root_folder_path=valid_root,
                    quality_profile_id=quality_profile_id,
                    monitored=monitored,
                    minimum_availability=minimum_availability,
                    tag_ids=[tag_id],
                    search_for_movie=search_for_movie
                )
                added += 1
                print(f"  {GREEN}Added: {movie_data['title']}{RESET}")
            except RadarrAPIError as e:
                logger.debug(f"Failed to add {movie_data['title']}: {e}")
                failed += 1

        print_status(f"  Combined: {added} added, {skipped} already exist, {failed} failed", "success")
        return

    # Per-user or mapping mode
    for user_data in users_to_export:
        display_name = user_data['display_name']
        movies_categorized = user_data['movies_categorized']

        # Collect TMDB IDs for movies
        movie_tmdb_ids = []
        for category_movies in movies_categorized.values():
            for movie in category_movies:
                tmdb_id = movie.get('tmdb_id')
                if tmdb_id:
                    movie_tmdb_ids.append(tmdb_id)
        # Deduplicate
        movie_tmdb_ids = list(dict.fromkeys(movie_tmdb_ids))

        if not movie_tmdb_ids:
            print_status(f"  {display_name}: No movie recommendations to add", "info")
            continue

        print(f"  {display_name}: Processing {len(movie_tmdb_ids)} movie recommendations...")

        # Get or create tag (optionally with username)
        user_tag = f"{tag_name}-{display_name}" if append_usernames else tag_name
        tag_id = radarr_client.get_or_create_tag(user_tag)

        added = 0
        skipped = 0
        failed = 0

        for tmdb_id in movie_tmdb_ids:
            if radarr_client.movie_exists(tmdb_id):
                skipped += 1
                continue

            # Look up movie
            movie_data = radarr_client.lookup_movie(tmdb_id)
            if not movie_data:
                logger.debug(f"Could not find movie for TMDB ID: {tmdb_id}")
                failed += 1
                continue

            try:
                radarr_client.add_movie(
                    tmdb_id=tmdb_id,
                    title=movie_data['title'],
                    root_folder_path=valid_root,
                    quality_profile_id=quality_profile_id,
                    monitored=monitored,
                    minimum_availability=minimum_availability,
                    tag_ids=[tag_id],
                    search_for_movie=search_for_movie
                )
                added += 1
                print(f"    {GREEN}Added: {movie_data['title']}{RESET}")
            except RadarrAPIError as e:
                logger.debug(f"Failed to add {movie_data['title']}: {e}")
                failed += 1

        print_status(f"  {display_name}: {added} added, {skipped} already exist, {failed} failed", "success")


def export_to_mdblist(config: Dict, all_users_data: List[Dict], tmdb_api_key: str) -> None:
    """
    Export recommendations to MDBList.

    Creates/updates lists with recommendations for importing into other apps.

    Config options:
        mdblist.enabled: Master switch for MDBList integration
        mdblist.auto_sync: Auto-export on each run (default: false)
        mdblist.user_mode: How to handle multiple Plex users:
            - mapping: Only export users in plex_users list (recommended)
            - per_user: Separate list for each Plex user
            - combined: All users combined into one list
        mdblist.plex_users: List of Plex usernames to export (for mapping mode)
        mdblist.list_prefix: Prefix for list names (default: "Curatarr")
        mdblist.replace_existing: Clear list before adding (default: true)
    """
    logger.debug("export_to_mdblist called")
    mdblist_config = config.get('mdblist', {})

    # Check if MDBList is enabled and auto_sync is on
    if not mdblist_config.get('enabled', False):
        return
    if not mdblist_config.get('auto_sync', False):
        return

    # Create MDBList client
    mdblist_client = create_mdblist_client(config)
    if not mdblist_client:
        log_warning("MDBList not configured - check config/mdblist.yml")
        return

    # Test connection
    try:
        user_info = mdblist_client.get_user_info()
        print(f"\n{CYAN}=== Exporting to MDBList ==={RESET}")
        print(f"  Connected as: {user_info.get('name', 'Unknown')}")
    except MDBListAPIError as e:
        log_error(f"Could not connect to MDBList: {e}")
        return

    user_mode = mdblist_config.get('user_mode', 'mapping')
    plex_users = mdblist_config.get('plex_users', [])
    list_prefix = mdblist_config.get('list_prefix', 'Curatarr')
    replace_existing = mdblist_config.get('replace_existing', True)

    # Safety check: mapping mode requires explicit plex_users configuration
    if user_mode == 'mapping':
        invalid_configs = [[], ['YourPlexUsername'], None]
        if plex_users in invalid_configs or not plex_users:
            log_warning(
                "MDBList export: No plex_users configured.\n"
                "  Edit mdblist.yml -> plex_users and add YOUR Plex username.\n"
                "  Example: plex_users: [\"jason\"]\n"
                "  This prevents accidentally exporting other users' recommendations."
            )
            return

    # Filter users based on mode
    if user_mode == 'mapping':
        plex_users_lower = [u.lower() for u in plex_users]
        users_to_export = [
            u for u in all_users_data
            if u['username'].lower() in plex_users_lower
        ]
        if not users_to_export:
            log_warning(
                f"MDBList export: No matching users found. Configured plex_users: {plex_users}\n"
                "  Check that your Plex username matches exactly."
            )
            return
    else:
        users_to_export = all_users_data

    # Helper to collect TMDB IDs from categorized data
    def collect_tmdb_ids(categorized):
        tmdb_ids = []
        for category_items in categorized.values():
            if isinstance(category_items, dict):
                for items in category_items.values():
                    for item in items:
                        if item.get('tmdb_id'):
                            tmdb_ids.append(item['tmdb_id'])
            elif isinstance(category_items, list):
                for item in category_items:
                    if item.get('tmdb_id'):
                        tmdb_ids.append(item['tmdb_id'])
        return list(dict.fromkeys(tmdb_ids))

    # Handle combined mode
    if user_mode == 'combined':
        all_movie_tmdb_ids = []
        all_show_tmdb_ids = []
        for user_data in users_to_export:
            all_movie_tmdb_ids.extend(collect_tmdb_ids(user_data['movies_categorized']))
            all_show_tmdb_ids.extend(collect_tmdb_ids(user_data['shows_categorized']))
        # Deduplicate
        all_movie_tmdb_ids = list(dict.fromkeys(all_movie_tmdb_ids))
        all_show_tmdb_ids = list(dict.fromkeys(all_show_tmdb_ids))

        try:
            if all_movie_tmdb_ids:
                movie_list_name = f"{list_prefix} - Movies"
                movie_list = mdblist_client.get_or_create_list(movie_list_name)
                if replace_existing:
                    mdblist_client.clear_list(movie_list['id'])
                result = mdblist_client.add_items(movie_list['id'], movies=all_movie_tmdb_ids)
                print_status(f"  Combined: {result.get('added', 0)} movies -> MDBList", "success")

            if all_show_tmdb_ids:
                show_list_name = f"{list_prefix} - TV"
                show_list = mdblist_client.get_or_create_list(show_list_name)
                if replace_existing:
                    mdblist_client.clear_list(show_list['id'])
                result = mdblist_client.add_items(show_list['id'], shows=all_show_tmdb_ids)
                print_status(f"  Combined: {result.get('added', 0)} shows -> MDBList", "success")

        except MDBListAPIError as e:
            log_error(f"Failed to export combined list to MDBList: {e}")
        return

    # Per-user or mapping mode
    for user_data in users_to_export:
        display_name = user_data['display_name']
        movies_categorized = user_data['movies_categorized']
        shows_categorized = user_data['shows_categorized']

        movie_tmdb_ids = collect_tmdb_ids(movies_categorized)
        show_tmdb_ids = collect_tmdb_ids(shows_categorized)

        try:
            if movie_tmdb_ids:
                movie_list_name = f"{list_prefix} - {display_name} - Movies"
                movie_list = mdblist_client.get_or_create_list(movie_list_name)
                if replace_existing:
                    mdblist_client.clear_list(movie_list['id'])
                result = mdblist_client.add_items(movie_list['id'], movies=movie_tmdb_ids)
                print_status(f"  {display_name}: {result.get('added', 0)} movies -> MDBList", "success")

            if show_tmdb_ids:
                show_list_name = f"{list_prefix} - {display_name} - TV"
                show_list = mdblist_client.get_or_create_list(show_list_name)
                if replace_existing:
                    mdblist_client.clear_list(show_list['id'])
                result = mdblist_client.add_items(show_list['id'], shows=show_tmdb_ids)
                print_status(f"  {display_name}: {result.get('added', 0)} shows -> MDBList", "success")

        except MDBListAPIError as e:
            log_error(f"Failed to export {display_name} to MDBList: {e}")


def export_to_simkl(config: Dict, all_users_data: List[Dict], tmdb_api_key: str) -> None:
    """
    Export recommendations to Simkl watchlist.

    Adds recommendations to user's Simkl "Plan to Watch" list.

    Config options:
        simkl.enabled: Master switch for Simkl integration
        simkl.export.enabled: Enable export feature
        simkl.export.auto_sync: Auto-export on each run (default: false)
        simkl.export.user_mode: How to handle multiple Plex users
        simkl.export.plex_users: List of Plex usernames to export (for mapping mode)
    """
    logger.debug("export_to_simkl called")
    simkl_config = config.get('simkl', {})

    # Check if Simkl is enabled
    if not simkl_config.get('enabled', False):
        return

    export_config = simkl_config.get('export', {})
    if not export_config.get('enabled', True):
        return
    if not export_config.get('auto_sync', False):
        return

    # Create Simkl client
    simkl_client = create_simkl_client(config)
    if not simkl_client:
        log_warning("Simkl not configured - check config/simkl.yml")
        return

    # Test connection
    try:
        if not simkl_client.test_connection():
            log_error("Could not connect to Simkl - check your access token")
            return
        print(f"\n{CYAN}=== Exporting to Simkl ==={RESET}")
    except (SimklAPIError, SimklAuthError) as e:
        log_error(f"Could not connect to Simkl: {e}")
        return

    user_mode = export_config.get('user_mode', 'mapping')
    plex_users = export_config.get('plex_users', [])

    # Safety check: mapping mode requires explicit plex_users configuration
    if user_mode == 'mapping':
        invalid_configs = [[], ['YourPlexUsername'], None]
        if plex_users in invalid_configs or not plex_users:
            log_warning(
                "Simkl export: No plex_users configured.\n"
                "  Edit simkl.yml -> export -> plex_users and add YOUR Plex username.\n"
                "  Example: plex_users: [\"jason\"]\n"
                "  This prevents accidentally exporting other users' recommendations."
            )
            return

    # Filter users based on mode
    if user_mode == 'mapping':
        plex_users_lower = [u.lower() for u in plex_users]
        users_to_export = [
            u for u in all_users_data
            if u['username'].lower() in plex_users_lower
        ]
        if not users_to_export:
            log_warning(
                f"Simkl export: No matching users found. Configured plex_users: {plex_users}\n"
                "  Check that your Plex username matches exactly."
            )
            return
    else:
        users_to_export = all_users_data

    # Collect TMDB IDs from categorized data
    def collect_tmdb_ids(categorized):
        """Extract TMDB IDs from categorized items."""
        tmdb_ids = []
        for category_items in categorized.values():
            if isinstance(category_items, dict):
                for items in category_items.values():
                    for item in items:
                        if item.get('tmdb_id'):
                            tmdb_ids.append(item['tmdb_id'])
            elif isinstance(category_items, list):
                for item in category_items:
                    if item.get('tmdb_id'):
                        tmdb_ids.append(item['tmdb_id'])
        return list(dict.fromkeys(tmdb_ids))

    # Collect all recommendations
    all_movie_tmdb_ids = []
    all_show_tmdb_ids = []
    for user_data in users_to_export:
        all_movie_tmdb_ids.extend(collect_tmdb_ids(user_data['movies_categorized']))
        all_show_tmdb_ids.extend(collect_tmdb_ids(user_data['shows_categorized']))

    # Deduplicate
    all_movie_tmdb_ids = list(dict.fromkeys(all_movie_tmdb_ids))
    all_show_tmdb_ids = list(dict.fromkeys(all_show_tmdb_ids))

    try:
        added_movies = 0
        added_shows = 0

        if all_movie_tmdb_ids:
            movies_data = [{"ids": {"tmdb": tmdb_id}} for tmdb_id in all_movie_tmdb_ids]
            result = simkl_client.add_to_watchlist(movies=movies_data)
            added_movies = result.get('added', {}).get('movies', 0)

        if all_show_tmdb_ids:
            shows_data = [{"ids": {"tmdb": tmdb_id}} for tmdb_id in all_show_tmdb_ids]
            result = simkl_client.add_to_watchlist(shows=shows_data)
            added_shows = result.get('added', {}).get('shows', 0)

        print_status(f"  Added {added_movies} movies, {added_shows} shows to Simkl watchlist", "success")

    except (SimklAPIError, SimklAuthError) as e:
        log_error(f"Failed to export to Simkl: {e}")


def sync_watch_history_to_trakt(
    config: Dict,
    tmdb_api_key: str,
    users: List[str] = None,
    load_profile_func: callable = None
) -> None:
    """
    Sync Plex watch history to Trakt.

    Loads watched TMDB IDs from cache files, converts to IMDB IDs,
    and marks them as watched on Trakt.

    This should run BEFORE processing users so Trakt data is available
    for profile enhancement.

    Args:
        config: Full config dict
        tmdb_api_key: TMDB API key for ID conversion
        users: Optional list of usernames (defaults to config users list)
        load_profile_func: Function to load user profile from cache
    """
    trakt_config = config.get('trakt', {})
    export_config = trakt_config.get('export', {})

    # Check if auto_sync is enabled
    if not trakt_config.get('enabled', False):
        return
    if not export_config.get('auto_sync', False):
        return

    # Get authenticated Trakt client
    trakt_client = get_authenticated_trakt_client(config)
    if not trakt_client:
        log_warning("Trakt not authenticated - run setup wizard to authenticate")
        return

    user_mode = export_config.get('user_mode', 'mapping')
    plex_users = export_config.get('plex_users', [])

    # Safety check for mapping mode
    if user_mode == 'mapping':
        if not plex_users or plex_users in [[], ['YourPlexUsername']]:
            log_warning(
                "Trakt sync: No plex_users configured.\n"
                "  Edit config.yml -> trakt.export.plex_users and add YOUR Plex username."
            )
            return

    print(f"\n{CYAN}Syncing Plex watch history to Trakt...{RESET}")

    # Get existing Trakt watch history to avoid duplicates
    existing_movie_imdb = trakt_client.get_watch_history_imdb_ids('movies')
    existing_show_imdb = trakt_client.get_watch_history_imdb_ids('shows')
    print(f"  Already on Trakt: {len(existing_movie_imdb)} movies, {len(existing_show_imdb)} shows")

    # Get users to sync
    if users is None:
        users = [u.strip() for u in config['users']['list'].split(',')]

    # Filter users based on mode
    if user_mode == 'mapping':
        plex_users_lower = [u.lower() for u in plex_users]
        users_to_sync = [u for u in users if u.lower() in plex_users_lower]
    else:
        users_to_sync = users

    if not users_to_sync:
        log_warning("No matching users to sync")
        return

    # Load TMDB IDs from cache files (fast - no API calls)
    all_movie_tmdb_ids = set()
    all_show_tmdb_ids = set()

    if load_profile_func is None:
        print("  No profile loader provided - cannot load cached watch history")
        return

    for username in users_to_sync:
        movie_profile = load_profile_func(config, username, 'movie')
        if movie_profile:
            all_movie_tmdb_ids.update(movie_profile.get('tmdb_ids', set()))

        tv_profile = load_profile_func(config, username, 'tv')
        if tv_profile:
            all_show_tmdb_ids.update(tv_profile.get('tmdb_ids', set()))

    if not all_movie_tmdb_ids and not all_show_tmdb_ids:
        print("  No Plex watch history in cache - run internal recommenders first")
        return

    # Load cache of already-synced TMDB IDs (avoid re-converting every run)
    TRAKT_SYNC_CACHE_VERSION = 1
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(project_root, config.get('cache_dir', 'cache'))
    sync_cache_file = os.path.join(cache_dir, 'trakt_synced_ids.json')
    synced_movie_tmdb = set()
    synced_show_tmdb = set()

    if os.path.exists(sync_cache_file):
        try:
            with open(sync_cache_file, 'r') as f:
                sync_cache = json.load(f)
                # Check cache version
                if sync_cache.get('version', 0) < TRAKT_SYNC_CACHE_VERSION:
                    print("  Trakt sync cache outdated, rebuilding...")
                else:
                    synced_movie_tmdb = set(sync_cache.get('movies', []))
                    synced_show_tmdb = set(sync_cache.get('shows', []))
        except Exception as e:
            logger.debug(f"Error loading Trakt sync cache: {e}")

    # Only process items we haven't synced before
    new_movie_tmdb = all_movie_tmdb_ids - synced_movie_tmdb
    new_show_tmdb = all_show_tmdb_ids - synced_show_tmdb

    print(f"  Plex watched: {len(all_movie_tmdb_ids)} movies, {len(all_show_tmdb_ids)} shows")
    print(f"  Already synced: {len(synced_movie_tmdb)} movies, {len(synced_show_tmdb)} shows")

    if not new_movie_tmdb and not new_show_tmdb:
        print_status("  Watch history already synced to Trakt", "success")
        return

    print(f"  New to sync: {len(new_movie_tmdb)} movies, {len(new_show_tmdb)} shows")

    # Convert only NEW TMDB IDs to IMDB IDs
    new_movie_imdb = []
    new_show_imdb = []
    converted_movies = set()  # Track ALL converted (for cache)
    converted_shows = set()

    # Movies with progress
    movie_list = list(new_movie_tmdb)
    total_movies = len(movie_list)
    if total_movies > 0:
        if len(synced_movie_tmdb) == 0:
            print("  (First-time sync - this is a one-time operation)")
        for i, tmdb_id in enumerate(movie_list, 1):
            if i % 10 == 0 or i == total_movies:
                pct = int(i / total_movies * 100)
                sys.stdout.write(f"\r  Converting movie IDs: {i}/{total_movies} ({pct}%)")
                sys.stdout.flush()
            imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'movie')
            if imdb_id:
                converted_movies.add(tmdb_id)  # Cache ALL converted
                if imdb_id not in existing_movie_imdb:
                    new_movie_imdb.append(imdb_id)
        print()  # newline after progress

    # Shows with progress
    show_list = list(new_show_tmdb)
    total_shows = len(show_list)
    if total_shows > 0:
        for i, tmdb_id in enumerate(show_list, 1):
            if i % 10 == 0 or i == total_shows:
                pct = int(i / total_shows * 100)
                sys.stdout.write(f"\r  Converting show IDs: {i}/{total_shows} ({pct}%)")
                sys.stdout.flush()
            imdb_id = get_imdb_id(tmdb_api_key, tmdb_id, 'tv')
            if imdb_id:
                converted_shows.add(tmdb_id)  # Cache ALL converted
                if imdb_id not in existing_show_imdb:
                    new_show_imdb.append(imdb_id)
        print()  # newline after progress

    # Update cache with all converted IDs (including ones already on Trakt)
    synced_movie_tmdb.update(converted_movies)
    synced_show_tmdb.update(converted_shows)
    try:
        with open(sync_cache_file, 'w') as f:
            json.dump({
                'version': TRAKT_SYNC_CACHE_VERSION,
                'movies': list(synced_movie_tmdb),
                'shows': list(synced_show_tmdb)
            }, f)
    except Exception as e:
        logger.debug(f"Error saving Trakt sync cache: {e}")

    if not new_movie_imdb and not new_show_imdb:
        print_status("  Watch history already synced to Trakt", "success")
        return

    print(f"  New items to sync: {len(new_movie_imdb)} movies, {len(new_show_imdb)} shows")

    # Sync to Trakt in batches (avoid timeout with large lists)
    try:
        total_movies_added = _sync_items_in_batches(
            new_movie_imdb, trakt_client, 'movies', 'movies'
        )
        total_shows_added = _sync_items_in_batches(
            new_show_imdb, trakt_client, 'shows', 'episodes'
        )

        print_status(
            f"  Synced to Trakt: {total_movies_added} movies, {total_shows_added} shows",
            "success"
        )
    except (TraktAPIError, TraktAuthError) as e:
        log_error(f"Failed to sync watch history to Trakt: {e}")
