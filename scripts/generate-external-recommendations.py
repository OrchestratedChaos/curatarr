#!/usr/bin/env python3
"""
Generate external recommendations - content NOT in your Plex library
Creates per-user markdown watchlists that update daily and auto-remove acquired items
"""

import os
import sys
import yaml
import json
import requests
import urllib3
from datetime import datetime
from plexapi.server import PlexServer
from plexapi.myplex import MyPlexAccount

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Add scripts directory to path for shared utilities
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))
from shared_plex_utils import (
    RED, GREEN, YELLOW, CYAN, RESET,
    get_plex_account_ids,
    fetch_watch_history_with_tmdb,
    print_user_header, print_user_footer, print_status,
    log_warning, log_error
)

# TMDB Genre ID mappings
TMDB_MOVIE_GENRES = {
    28: 'Action', 12: 'Adventure', 16: 'Animation', 35: 'Comedy', 80: 'Crime',
    99: 'Documentary', 18: 'Drama', 10751: 'Family', 14: 'Fantasy', 36: 'History',
    27: 'Horror', 10402: 'Music', 9648: 'Mystery', 10749: 'Romance', 878: 'Science Fiction',
    10770: 'TV Movie', 53: 'Thriller', 10752: 'War', 37: 'Western'
}

TMDB_TV_GENRES = {
    10759: 'Action & Adventure', 16: 'Animation', 35: 'Comedy', 80: 'Crime', 99: 'Documentary',
    18: 'Drama', 10751: 'Family', 10762: 'Kids', 9648: 'Mystery', 10763: 'News',
    10764: 'Reality', 10765: 'Sci-Fi & Fantasy', 10766: 'Soap', 10767: 'Talk',
    10768: 'War & Politics', 37: 'Western'
}

# TMDB Watch Provider ID mappings (US region)
TMDB_PROVIDERS = {
    8: 'netflix',
    15: 'hulu',
    337: 'disney_plus',
    9: 'amazon_prime',
    531: 'paramount_plus',
    350: 'apple_tv_plus',
    384: 'max',
    387: 'peacock',
    1899: 'max',  # HBO Max (legacy ID)
    203: 'crunchyroll',
    283: 'crackle',
    613: 'tubi',
    207: 'mubi',
    619: 'shudder'
}

# Reverse mapping for config service names to display names
SERVICE_DISPLAY_NAMES = {
    'netflix': 'Netflix',
    'hulu': 'Hulu',
    'disney_plus': 'Disney+',
    'amazon_prime': 'Amazon Prime Video',
    'paramount_plus': 'Paramount+',
    'apple_tv_plus': 'Apple TV+',
    'max': 'Max',
    'peacock': 'Peacock',
    'crunchyroll': 'Crunchyroll',
    'crackle': 'Crackle',
    'tubi': 'Tubi',
    'mubi': 'MUBI',
    'shudder': 'Shudder'
}

def load_config():
    """Load configuration from root config.yml"""
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yml')
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        log_error(f"Error: config.yml not found at {config_path}")
        sys.exit(1)

def get_library_items(plex, library_name, media_type='movie'):
    """Get all items currently in Plex library"""
    try:
        library = plex.library.section(library_name)
        items = library.all()

        # Extract TMDB IDs for comparison
        tmdb_ids = set()
        for item in items:
            for guid in item.guids:
                if 'tmdb://' in guid.id:
                    tmdb_id = guid.id.split('tmdb://')[1]
                    tmdb_ids.add(int(tmdb_id))

        return tmdb_ids
    except Exception as e:
        log_warning(f"Warning: Could not fetch {library_name} library: {e}")
        return set()

def get_watch_providers(tmdb_api_key, tmdb_id, media_type='movie'):
    """
    Get streaming providers for a TMDB item (US region)
    Returns list of service names (e.g., ['netflix', 'hulu'])
    """
    try:
        url = f"https://api.themoviedb.org/3/{'movie' if media_type == 'movie' else 'tv'}/{tmdb_id}/watch/providers"
        params = {'api_key': tmdb_api_key}
        response = requests.get(url, params=params, timeout=10)

        if response.status_code != 200:
            return []

        data = response.json()

        # Get US providers (flatrate = subscription streaming)
        us_providers = data.get('results', {}).get('US', {})
        flatrate_providers = us_providers.get('flatrate', [])

        # Map provider IDs to service names
        services = []
        for provider in flatrate_providers:
            provider_id = provider.get('provider_id')
            if provider_id in TMDB_PROVIDERS:
                service_name = TMDB_PROVIDERS[provider_id]
                if service_name not in services:  # Avoid duplicates
                    services.append(service_name)

        return services
    except Exception as e:
        # Silently fail for individual items - don't spam logs
        return []

def categorize_by_streaming_service(recommendations, tmdb_api_key, user_services, media_type='movie'):
    """
    Categorize recommendations by streaming availability
    Returns dict: {
        'user_services': {service_name: [items]},
        'other_services': {service_name: [items]},
        'acquire': [items]
    }
    """
    result = {
        'user_services': {},
        'other_services': {},
        'acquire': []
    }

    for item in recommendations:
        tmdb_id = item['tmdb_id']
        providers = get_watch_providers(tmdb_api_key, tmdb_id, media_type)

        if not providers:
            # Not available on any streaming service
            result['acquire'].append(item)
        else:
            # Check which services have it
            user_has_it = False
            for service in providers:
                if service in user_services:
                    # Available on user's service
                    if service not in result['user_services']:
                        result['user_services'][service] = []
                    result['user_services'][service].append(item)
                    user_has_it = True
                else:
                    # Available on other service
                    if service not in result['other_services']:
                        result['other_services'][service] = []
                    result['other_services'][service].append(item)

            # If not on any of user's services, it's in other_services only
            # (already handled above)

    return result

def get_genre_distribution(plex, config, username, media_type='movie'):
    """Calculate genre distribution from user's watch history"""
    try:
        library_name = config['plex'].get('movie_library' if media_type == 'movie' else 'tv_library')
        library = plex.library.section(library_name)

        genre_counts = {}
        total_items = 0

        # For admin user, check watched items directly
        account = MyPlexAccount(token=config['plex']['token'])
        if username.lower() == account.username.lower():
            for item in library.all():
                if item.isWatched:
                    total_items += 1
                    for genre in item.genres:
                        genre_counts[genre.tag] = genre_counts.get(genre.tag, 0) + 1

        # Calculate percentages
        genre_distribution = {}
        if total_items > 0:
            for genre, count in genre_counts.items():
                genre_distribution[genre] = count / total_items

        return genre_distribution, total_items
    except Exception as e:
        log_warning(f"  Warning: Could not calculate genre distribution: {e}")
        return {}, 0

def get_user_watch_history(plex, config, username, media_type='movie'):
    """Get user's watch history from Plex using shared utility"""
    print(f"Fetching {media_type} watch history for {username}...")

    try:
        # Get library
        library_name = config['plex'].get('movie_library' if media_type == 'movie' else 'tv_library')
        library = plex.library.section(library_name)

        # Get user's account using flexible matching from shared utils
        account_ids = get_plex_account_ids(config, [username])

        if not account_ids:
            log_warning(f"  Warning: User {username} not found")
            return []

        # Use shared utility to fetch watch history with TMDB IDs
        return fetch_watch_history_with_tmdb(plex, config, account_ids, library, media_type)

    except Exception as e:
        log_warning(f"  Warning: Could not fetch watch history: {e}")
        return []

def balance_genres_proportionally(recommendations, genre_distribution, limit, media_type='movie'):
    """
    Balance recommendations to match user's genre distribution from watch history
    Prevents any single genre from dominating the list
    """
    if not genre_distribution or not recommendations:
        return recommendations[:limit]

    genre_map = TMDB_MOVIE_GENRES if media_type == 'movie' else TMDB_TV_GENRES

    # Calculate target counts for each genre
    genre_targets = {}
    for genre_name, percentage in genre_distribution.items():
        target_count = int(limit * percentage)
        # Ensure at least 1 slot for genres that exist in history
        if target_count == 0 and percentage > 0:
            target_count = 1
        genre_targets[genre_name] = target_count

    # Track how many of each genre we've added
    genre_counts = {genre: 0 for genre in genre_targets}

    balanced_recs = []
    remaining_recs = []

    # First pass: add items up to their genre targets
    for rec in recommendations:
        if len(balanced_recs) >= limit:
            break

        # Get primary genre (first genre_id)
        primary_genre_id = rec['genre_ids'][0] if rec['genre_ids'] else None
        primary_genre = genre_map.get(primary_genre_id, 'Unknown')

        # Check if this genre is under its target
        if primary_genre in genre_targets and genre_counts[primary_genre] < genre_targets[primary_genre]:
            balanced_recs.append(rec)
            genre_counts[primary_genre] += 1
        else:
            remaining_recs.append(rec)

    # Second pass: fill remaining slots with best-scored items regardless of genre
    remaining_needed = limit - len(balanced_recs)
    if remaining_needed > 0:
        balanced_recs.extend(remaining_recs[:remaining_needed])

    print(f"Genre balancing: {len(balanced_recs)} items selected")
    for genre, count in sorted(genre_counts.items(), key=lambda x: x[1], reverse=True):
        if count > 0:
            target = genre_targets.get(genre, 0)
            actual_pct = (count / len(balanced_recs) * 100) if balanced_recs else 0
            target_pct = genre_distribution.get(genre, 0) * 100
            print(f"  {genre}: {count} items ({actual_pct:.1f}% actual vs {target_pct:.1f}% target)")

    return balanced_recs

def find_similar_content(tmdb_api_key, watched_items, existing_library_ids, media_type='movie', limit=50, genre_distribution=None):
    """Find similar content NOT in library using TMDB API"""
    print(f"Finding similar {media_type}s not in library...")

    if not watched_items:
        print_status("No watch history found", "warning")
        return []

    # Use TMDB's recommendations and similar endpoints
    # Score each recommendation by how many watched items recommend it
    recommendation_scores = {}

    media_type_param = 'movie' if media_type == 'movie' else 'tv'

    # Sample from watched items (use most recent 20 to avoid API spam)
    sample_size = min(20, len(watched_items))
    sampled_items = watched_items[:sample_size]

    for watched_item in sampled_items:
        tmdb_id = watched_item['tmdb_id']

        try:
            # Get recommendations from TMDB
            url = f"https://api.themoviedb.org/3/{media_type_param}/{tmdb_id}/recommendations"
            params = {'api_key': tmdb_api_key, 'page': 1}
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                for result in data.get('results', [])[:10]:  # Top 10 from each
                    result_id = result['id']

                    # Skip if already in library
                    if result_id in existing_library_ids:
                        continue

                    # Add or increment score
                    if result_id not in recommendation_scores:
                        recommendation_scores[result_id] = {
                            'tmdb_id': result_id,
                            'title': result.get('title') or result.get('name'),
                            'year': (result.get('release_date') or result.get('first_air_date', ''))[:4],
                            'rating': result.get('vote_average', 0),
                            'score': 0,
                            'overview': result.get('overview', ''),
                            'genre_ids': result.get('genre_ids', [])
                        }
                    recommendation_scores[result_id]['score'] += 1

            # Also get "similar" content
            url = f"https://api.themoviedb.org/3/{media_type_param}/{tmdb_id}/similar"
            params = {'api_key': tmdb_api_key, 'page': 1}
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                for result in data.get('results', [])[:5]:  # Top 5 similar
                    result_id = result['id']

                    # Skip if already in library
                    if result_id in existing_library_ids:
                        continue

                    # Add or increment score
                    if result_id not in recommendation_scores:
                        recommendation_scores[result_id] = {
                            'tmdb_id': result_id,
                            'title': result.get('title') or result.get('name'),
                            'year': (result.get('release_date') or result.get('first_air_date', ''))[:4],
                            'rating': result.get('vote_average', 0),
                            'score': 0,
                            'overview': result.get('overview', ''),
                            'genre_ids': result.get('genre_ids', [])
                        }
                    recommendation_scores[result_id]['score'] += 0.5  # Similar gets half weight

        except Exception as e:
            # Silently skip errors (rate limiting, timeouts, etc.)
            pass

    # Convert scores to list and normalize
    recommendations = list(recommendation_scores.values())

    # Normalize scores to 0-1 range
    if recommendations:
        max_score = max(r['score'] for r in recommendations)
        for rec in recommendations:
            rec['score'] = rec['score'] / max_score if max_score > 0 else 0

    # Sort by score descending
    recommendations.sort(key=lambda x: (x['score'], x['rating']), reverse=True)

    print(f"Found {len(recommendations)} recommendations")

    # Apply genre balancing if distribution provided
    if genre_distribution:
        balanced = balance_genres_proportionally(recommendations, genre_distribution, limit, media_type)
        return balanced
    else:
        return recommendations[:limit]

def load_cache(username, media_type):
    """Load existing recommendations cache"""
    cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f'external_recs_{username}_{media_type}.json')

    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            cache = json.load(f)
            # Add tmdb_id to items that don't have it (backwards compatibility)
            for tmdb_id_str, item in cache.items():
                if 'tmdb_id' not in item:
                    item['tmdb_id'] = int(tmdb_id_str)
            return cache
    return {}

def save_cache(username, media_type, cache_data):
    """Save recommendations cache"""
    cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f'external_recs_{username}_{media_type}.json')

    with open(cache_file, 'w') as f:
        json.dump(cache_data, f, indent=2)

def load_ignore_list(username):
    """Load user's manual ignore list"""
    ignore_file = os.path.join(os.path.dirname(__file__), 'recommendations', 'external', f'{username}_ignore.txt')
    if os.path.exists(ignore_file):
        with open(ignore_file, 'r') as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def generate_markdown(username, display_name, movies_categorized, shows_categorized, output_dir):
    """
    Generate markdown watchlist file with streaming service grouping

    Args:
        movies_categorized: dict with 'user_services', 'other_services', 'acquire' keys
        shows_categorized: dict with 'user_services', 'other_services', 'acquire' keys
    """
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f'{username}_watchlist.md')

    now = datetime.now()

    def write_service_section(f, items, media_icon):
        """Helper to write a table of items"""
        f.write(f"| Title | Year | Rating | Score | Days on List |\n")
        f.write(f"|-------|------|--------|-------|-------------|\n")
        for item in items:
            days_listed = (now - datetime.fromisoformat(item['added_date'])).days
            f.write(f"| {item['title']} | {item['year']} | ⭐ {item['rating']:.1f} | {item['score']:.1%} | {days_listed} |\n")
        f.write("\n")

    with open(output_file, 'w') as f:
        f.write(f"# 🎬 Watchlist for {display_name}\n\n")
        f.write(f"*Last updated: {now.strftime('%Y-%m-%d %H:%M')}*\n\n")
        f.write("---\n\n")

        # Movies section
        if any([movies_categorized['user_services'], movies_categorized['other_services'], movies_categorized['acquire']]):
            f.write("## 🎥 Movies to Watch\n\n")

            # User's services
            if movies_categorized['user_services']:
                f.write("### Available on Your Services\n\n")
                for service, items in sorted(movies_categorized['user_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} movies)\n\n")
                    write_service_section(f, items, "🎥")
                f.write("---\n\n")

            # Other services
            if movies_categorized['other_services']:
                f.write("### Available on Other Services\n\n")
                f.write("*Consider subscribing if many recommendations are on a single service*\n\n")
                for service, items in sorted(movies_categorized['other_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} movies)\n\n")
                    write_service_section(f, items, "🎥")
                f.write("---\n\n")

            # Acquire
            if movies_categorized['acquire']:
                f.write(f"### Acquire ({len(movies_categorized['acquire'])} movies)\n\n")
                f.write("*Not available on any streaming service - need physical/digital copy*\n\n")
                write_service_section(f, movies_categorized['acquire'], "🎥")

        # TV Shows section
        if any([shows_categorized['user_services'], shows_categorized['other_services'], shows_categorized['acquire']]):
            f.write("## 📺 TV Shows to Watch\n\n")

            # User's services
            if shows_categorized['user_services']:
                f.write("### Available on Your Services\n\n")
                for service, items in sorted(shows_categorized['user_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} shows)\n\n")
                    write_service_section(f, items, "📺")
                f.write("---\n\n")

            # Other services
            if shows_categorized['other_services']:
                f.write("### Available on Other Services\n\n")
                f.write("*Consider subscribing if many recommendations are on a single service*\n\n")
                for service, items in sorted(shows_categorized['other_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} shows)\n\n")
                    write_service_section(f, items, "📺")
                f.write("---\n\n")

            # Acquire
            if shows_categorized['acquire']:
                f.write(f"### Acquire ({len(shows_categorized['acquire'])} shows)\n\n")
                f.write("*Not available on any streaming service - need physical/digital copy*\n\n")
                write_service_section(f, shows_categorized['acquire'], "📺")

        # Instructions
        f.write("---\n\n")
        f.write("## 📝 How to Use This List\n\n")
        f.write("- Items are automatically removed when added to your Plex library\n")
        f.write(f"- To manually ignore an item, add its title to `{username}_ignore.txt`\n")
        f.write("- List updates daily with new recommendations\n")
        f.write("- Grouped by streaming availability to help you decide what to watch or acquire\n\n")

    return output_file

def process_user(config, plex, username):
    """Process external recommendations for a single user"""
    user_prefs = config['users']['preferences'].get(username, {})
    display_name = user_prefs.get('display_name', username)

    print_user_header(f"{display_name} (external recommendations)")

    # Get current library contents
    movie_library = config['plex'].get('movie_library', 'Movies')
    tv_library = config['plex'].get('tv_library', 'TV Shows')

    library_movie_ids = get_library_items(plex, movie_library, 'movie')
    library_show_ids = get_library_items(plex, tv_library, 'show')

    print(f"Library has {len(library_movie_ids)} movies, {len(library_show_ids)} TV shows")

    # Load existing cache and ignore list
    movie_cache = load_cache(username, 'movies')
    show_cache = load_cache(username, 'shows')
    ignore_list = load_ignore_list(username)

    # Remove acquired items from cache (now in library)
    removed_movies = [tmdb_id for tmdb_id in movie_cache.keys() if int(tmdb_id) in library_movie_ids]
    removed_shows = [tmdb_id for tmdb_id in show_cache.keys() if int(tmdb_id) in library_show_ids]

    for tmdb_id in removed_movies:
        del movie_cache[tmdb_id]
    for tmdb_id in removed_shows:
        del show_cache[tmdb_id]

    if removed_movies or removed_shows:
        print_status(f"Removed {len(removed_movies)} movies and {len(removed_shows)} shows (now in library)", "success")

    # Remove ignored items
    removed_ignored = 0
    for tmdb_id, item in list(movie_cache.items()):
        if item['title'] in ignore_list:
            del movie_cache[tmdb_id]
            removed_ignored += 1
    for tmdb_id, item in list(show_cache.items()):
        if item['title'] in ignore_list:
            del show_cache[tmdb_id]
            removed_ignored += 1

    if removed_ignored:
        print_status(f"Removed {removed_ignored} ignored items", "warning")

    # Get user's watch history
    movie_watch_history = get_user_watch_history(plex, config, username, 'movie')
    show_watch_history = get_user_watch_history(plex, config, username, 'show')

    print(f"Watch history: {len(movie_watch_history)} movies, {len(show_watch_history)} shows")

    # Get genre distribution from watch history
    movie_genre_dist, movie_count = get_genre_distribution(plex, config, username, 'movie')
    show_genre_dist, show_count = get_genre_distribution(plex, config, username, 'show')

    if movie_genre_dist:
        print(f"Movie genre distribution: {dict(sorted(movie_genre_dist.items(), key=lambda x: x[1], reverse=True)[:5])}")
    if show_genre_dist:
        print(f"TV genre distribution: {dict(sorted(show_genre_dist.items(), key=lambda x: x[1], reverse=True)[:5])}")

    # Find new recommendations
    external_config = config.get('external_recommendations', {})
    movie_limit = external_config.get('movie_limit', 30)
    show_limit = external_config.get('show_limit', 20)

    new_movies = find_similar_content(
        config['tmdb']['api_key'],
        movie_watch_history,
        library_movie_ids,
        'movie',
        limit=movie_limit,
        genre_distribution=movie_genre_dist
    )

    new_shows = find_similar_content(
        config['tmdb']['api_key'],
        show_watch_history,
        library_show_ids,
        'tv',
        limit=show_limit,
        genre_distribution=show_genre_dist
    )

    # Merge with existing cache (add new ones)
    for movie in new_movies:
        tmdb_id = str(movie['tmdb_id'])
        if tmdb_id not in movie_cache:
            movie_cache[tmdb_id] = {
                'tmdb_id': movie['tmdb_id'],  # Add tmdb_id for categorization
                'title': movie['title'],
                'year': movie['year'],
                'rating': movie['rating'],
                'score': movie['score'],
                'added_date': datetime.now().isoformat()
            }

    for show in new_shows:
        tmdb_id = str(show['tmdb_id'])
        if tmdb_id not in show_cache:
            show_cache[tmdb_id] = {
                'tmdb_id': show['tmdb_id'],  # Add tmdb_id for categorization
                'title': show['title'],
                'year': show['year'],
                'rating': show['rating'],
                'score': show['score'],
                'added_date': datetime.now().isoformat()
            }

    # Save updated caches
    save_cache(username, 'movies', movie_cache)
    save_cache(username, 'shows', show_cache)

    # Prepare lists for categorization
    movies_list = sorted(movie_cache.values(), key=lambda x: x['score'], reverse=True)
    shows_list = sorted(show_cache.values(), key=lambda x: x['score'], reverse=True)

    # Get household streaming services from top-level config
    user_services = config.get('streaming_services', [])

    # Categorize by streaming service availability
    print("Categorizing by streaming service availability...")
    movies_categorized = categorize_by_streaming_service(
        movies_list,
        config['tmdb']['api_key'],
        user_services,
        'movie'
    )
    shows_categorized = categorize_by_streaming_service(
        shows_list,
        config['tmdb']['api_key'],
        user_services,
        'tv'
    )

    # Generate markdown
    output_dir = os.path.join(os.path.dirname(__file__), 'recommendations', 'external')
    output_file = generate_markdown(username, display_name, movies_categorized, shows_categorized, output_dir)

    # Count totals
    total_movies = sum(len(items) for items in movies_categorized['user_services'].values()) + \
                   sum(len(items) for items in movies_categorized['other_services'].values()) + \
                   len(movies_categorized['acquire'])
    total_shows = sum(len(items) for items in shows_categorized['user_services'].values()) + \
                  sum(len(items) for items in shows_categorized['other_services'].values()) + \
                  len(shows_categorized['acquire'])

    print_status(f"Generated watchlist: {output_file}", "success")
    print(f"{total_movies} movies, {total_shows} TV shows")
    print_user_footer(f"{display_name} (external recommendations)")

def main():
    print(f"\n{CYAN}External Recommendations Generator{RESET}")
    print("-" * 50)

    # Load config
    config = load_config()

    # Connect to Plex
    try:
        plex = PlexServer(config['plex']['url'], config['plex']['token'])
        print_status("Connected to Plex", "success")
    except Exception as e:
        print_status(f"Error connecting to Plex: {e}", "error")
        sys.exit(1)

    # Get users
    users = [u.strip() for u in config['users']['list'].split(',')]

    # Process each user
    for username in users:
        try:
            process_user(config, plex, username)
        except Exception as e:
            print_status(f"Error processing {username}: {e}", "error")
            import traceback
            traceback.print_exc()

    print_status("All watchlists generated!", "success")
    print(f"Watchlists saved to: {os.path.join(os.path.dirname(__file__), 'recommendations', 'external')}")

if __name__ == "__main__":
    main()
