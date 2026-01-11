"""
Output generation for external recommendations.
Generates markdown watchlists and combined HTML views.
"""

import json
import os
from datetime import datetime
from typing import Dict, List


def _load_imdb_cache(cache_path: str) -> Dict[str, str]:
    """Load IMDB ID cache from disk. IDs are permanent so no staleness check."""
    try:
        if os.path.exists(cache_path):
            with open(cache_path, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def _save_imdb_cache(cache_path: str, cache: Dict[str, str]) -> None:
    """Save IMDB ID cache to disk."""
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w') as f:
            json.dump(cache, f)
    except IOError:
        pass

# ANSI color codes
CYAN = '\033[96m'
GREEN = '\033[92m'
RESET = '\033[0m'

# Service display name mappings
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

# Short display names for icons (space-efficient)
SERVICE_SHORT_NAMES = {
    'netflix': 'Netflix',
    'hulu': 'Hulu',
    'disney_plus': 'Disney+',
    'amazon_prime': 'Prime',
    'paramount_plus': 'P+',
    'apple_tv_plus': 'Apple',
    'max': 'Max',
    'peacock': 'Peacock',
    'crunchyroll': 'Crunchy',
    'crackle': 'Crackle',
    'tubi': 'Tubi',
    'mubi': 'MUBI',
    'shudder': 'Shudder'
}


def render_streaming_icons(
    services: List[str],
    user_services: List[str],
    rent_services: List[str] = None,
    buy_services: List[str] = None
) -> str:
    """
    Render HTML streaming service icons/badges.
    User's services get a gold border highlight.
    If no streaming, shows rent/buy options or Acquire.
    """
    # If streaming services available, show them
    if services:
        icons = []
        for service in services:
            short_name = SERVICE_SHORT_NAMES.get(service, service.title())
            css_class = f"streaming-icon {service}"
            if service in user_services:
                css_class += " user-service"
            icons.append(f'<span class="{css_class}">{short_name}</span>')
        return ' '.join(icons)

    # No streaming - check rent/buy availability
    if rent_services:
        display = ', '.join(rent_services[:2])  # Show first 2
        all_providers = ', '.join(rent_services)  # Tooltip shows all
        more = f" +{len(rent_services) - 2}" if len(rent_services) > 2 else ""
        return f'<span class="streaming-icon rent" title="Available: {all_providers}">Rent: {display}{more}</span>'

    if buy_services:
        display = ', '.join(buy_services[:2])
        all_providers = ', '.join(buy_services)
        more = f" +{len(buy_services) - 2}" if len(buy_services) > 2 else ""
        return f'<span class="streaming-icon buy" title="Available: {all_providers}">Buy: {display}{more}</span>'

    return '<span class="streaming-icon acquire">Acquire</span>'


def generate_markdown(
    username: str,
    display_name: str,
    movies_categorized: Dict,
    shows_categorized: Dict,
    output_dir: str
) -> str:
    """
    Generate markdown watchlist file with streaming service grouping

    Args:
        movies_categorized: dict with 'user_services', 'other_services', 'acquire' keys
        shows_categorized: dict with 'user_services', 'other_services', 'acquire' keys

    Returns:
        Path to the generated markdown file
    """
    os.makedirs(output_dir, exist_ok=True)
    # Use display_name for filename, sanitized for filesystem
    safe_name = display_name.lower().replace(' ', '_')
    output_file = os.path.join(output_dir, f'{safe_name}_watchlist.md')

    now = datetime.now()

    def write_service_section(f, items):
        """Helper to write a table of items"""
        f.write("| Title | Year | Rating | Score | Days on List |\n")
        f.write("|-------|------|--------|-------|-------------|\n")
        for item in items:
            days_listed = (now - datetime.fromisoformat(item['added_date'])).days
            f.write(f"| {item['title']} | {item['year']} | {item['rating']:.1f} | {item['score']:.1%} | {days_listed} |\n")
        f.write("\n")

    with open(output_file, 'w') as f:
        f.write(f"# Watchlist for {display_name}\n\n")
        f.write(f"*Last updated: {now.strftime('%Y-%m-%d %H:%M')}*\n\n")
        f.write("---\n\n")

        # Movies section
        if any([movies_categorized['user_services'], movies_categorized['other_services'], movies_categorized['acquire']]):
            f.write("## Movies to Watch\n\n")

            # User's services
            if movies_categorized['user_services']:
                f.write("### Available on Your Services\n\n")
                for service, items in sorted(movies_categorized['user_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} movies)\n\n")
                    write_service_section(f, items)
                f.write("---\n\n")

            # Other services
            if movies_categorized['other_services']:
                f.write("### Available on Other Services\n\n")
                f.write("*Consider subscribing if many recommendations are on a single service*\n\n")
                for service, items in sorted(movies_categorized['other_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} movies)\n\n")
                    write_service_section(f, items)
                f.write("---\n\n")

            # Acquire
            if movies_categorized['acquire']:
                f.write(f"### Acquire ({len(movies_categorized['acquire'])} movies)\n\n")
                f.write("*Not available on any streaming service - need physical/digital copy*\n\n")
                write_service_section(f, movies_categorized['acquire'])

        # TV Shows section
        if any([shows_categorized['user_services'], shows_categorized['other_services'], shows_categorized['acquire']]):
            f.write("## TV Shows to Watch\n\n")

            # User's services
            if shows_categorized['user_services']:
                f.write("### Available on Your Services\n\n")
                for service, items in sorted(shows_categorized['user_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} shows)\n\n")
                    write_service_section(f, items)
                f.write("---\n\n")

            # Other services
            if shows_categorized['other_services']:
                f.write("### Available on Other Services\n\n")
                f.write("*Consider subscribing if many recommendations are on a single service*\n\n")
                for service, items in sorted(shows_categorized['other_services'].items(), key=lambda x: -len(x[1])):
                    service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
                    f.write(f"#### {service_display} ({len(items)} shows)\n\n")
                    write_service_section(f, items)
                f.write("---\n\n")

            # Acquire
            if shows_categorized['acquire']:
                f.write(f"### Acquire ({len(shows_categorized['acquire'])} shows)\n\n")
                f.write("*Not available on any streaming service - need physical/digital copy*\n\n")
                write_service_section(f, shows_categorized['acquire'])

        # Instructions
        f.write("---\n\n")
        f.write("## How to Use This List\n\n")
        f.write("- Items are automatically removed when added to your Plex library\n")
        f.write(f"- To manually ignore an item, add its title to `{safe_name}_ignore.txt`\n")
        f.write("- List updates daily with new recommendations\n")
        f.write("- Grouped by streaming availability to help you decide what to watch or acquire\n\n")

    return output_file


def generate_combined_html(
    all_users_data: List[Dict],
    output_dir: str,
    tmdb_api_key: str,
    get_imdb_id_func,
    movie_counts: Dict[str, int] = None,
    show_counts: Dict[str, int] = None,
    total_users: int = 1,
    missing_sequels: List[Dict] = None,
    horizon_movies: List[Dict] = None
) -> str:
    """
    Generate single HTML watchlist with tabs for all users.
    Users can switch between tabs, select items, and export to Radarr/Sonarr/Trakt.

    Args:
        all_users_data: List of user data dicts with movies_categorized and shows_categorized
        output_dir: Directory to write HTML file
        tmdb_api_key: TMDB API key for fetching IMDB IDs
        get_imdb_id_func: Function to fetch IMDB ID from TMDB ID
        movie_counts: Dict mapping TMDB ID to count of users wanting the movie
        show_counts: Dict mapping TMDB ID to count of users wanting the show
        total_users: Total number of users for displaying "X/N users"
        missing_sequels: List of missing sequel items from Sequel Huntarr
        horizon_movies: List of upcoming movie items from Horizon Huntarr

    Returns:
        Path to the generated HTML file
    """
    movie_counts = movie_counts or {}
    show_counts = show_counts or {}
    missing_sequels = missing_sequels or []
    horizon_movies = horizon_movies or []
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'watchlist.html')

    now = datetime.now()

    # Load IMDB ID cache (IDs are permanent, no staleness needed)
    cache_dir = os.path.dirname(output_dir)
    imdb_cache_path = os.path.join(cache_dir, 'cache', 'imdb_ids_cache.json')
    imdb_cache = _load_imdb_cache(imdb_cache_path)

    # Collect all unique TMDB IDs that need IMDB lookup
    all_imdb_ids = {}  # tmdb_id -> imdb_id
    pending_lookups = []  # [(tmdb_id, media_type), ...]

    def collect_tmdb_ids_from_categorized(categorized, media_type):
        """Helper to collect TMDB IDs from categorized items."""
        items = categorized.get('all_items', [])
        if not items:
            for service_items in categorized.get('user_services', {}).values():
                items.extend(service_items)
            for service_items in categorized.get('other_services', {}).values():
                items.extend(service_items)
            items.extend(categorized.get('acquire', []))

        for item in items:
            tmdb_id = item.get('tmdb_id')
            if not tmdb_id:
                continue
            # Check cache first
            cache_key = f"{tmdb_id}_{media_type}"
            if cache_key in imdb_cache:
                all_imdb_ids[tmdb_id] = imdb_cache[cache_key]
            elif tmdb_id not in all_imdb_ids and (tmdb_id, media_type) not in [(p[0], p[1]) for p in pending_lookups]:
                pending_lookups.append((tmdb_id, media_type))

    for user_data in all_users_data:
        collect_tmdb_ids_from_categorized(user_data['movies_categorized'], 'movie')
        collect_tmdb_ids_from_categorized(user_data['shows_categorized'], 'tv')

    # Also collect from missing sequels (Sequel Huntarr)
    for item in missing_sequels:
        tmdb_id = item.get('tmdb_id')
        if not tmdb_id:
            continue
        cache_key = f"{tmdb_id}_movie"
        if cache_key in imdb_cache:
            all_imdb_ids[tmdb_id] = imdb_cache[cache_key]
        elif tmdb_id not in all_imdb_ids and (tmdb_id, 'movie') not in [(p[0], p[1]) for p in pending_lookups]:
            pending_lookups.append((tmdb_id, 'movie'))

    # Also collect from horizon movies (Horizon Huntarr)
    for item in horizon_movies:
        tmdb_id = item.get('tmdb_id')
        if not tmdb_id:
            continue
        cache_key = f"{tmdb_id}_movie"
        if cache_key in imdb_cache:
            all_imdb_ids[tmdb_id] = imdb_cache[cache_key]
        elif tmdb_id not in all_imdb_ids and (tmdb_id, 'movie') not in [(p[0], p[1]) for p in pending_lookups]:
            pending_lookups.append((tmdb_id, 'movie'))

    # Fetch IMDB IDs for items not in cache
    total_lookups = len(pending_lookups)
    new_lookups = 0
    if total_lookups > 0:
        print(f"  {CYAN}Fetching IMDB IDs for export ({total_lookups} new, {len(all_imdb_ids)} cached)...{RESET}")
        for i, (tmdb_id, media_type) in enumerate(pending_lookups, 1):
            if i % 10 == 0 or i == total_lookups:
                print(f"\r    {CYAN}Progress: {i}/{total_lookups}{RESET}", end="", flush=True)
            imdb_id = get_imdb_id_func(tmdb_api_key, tmdb_id, media_type)
            if imdb_id:
                all_imdb_ids[tmdb_id] = imdb_id
                imdb_cache[f"{tmdb_id}_{media_type}"] = imdb_id
                new_lookups += 1
        print(f"\r    {GREEN}Fetched {new_lookups} new IMDB IDs ({len(all_imdb_ids)} total){RESET}          ")
        # Save updated cache
        _save_imdb_cache(imdb_cache_path, imdb_cache)
    else:
        print(f"  {GREEN}All {len(all_imdb_ids)} IMDB IDs from cache{RESET}")

    def render_table_flat(items, media_type, user_id, user_services):
        """Render HTML table with streaming icons column (score-sorted)"""
        rows = []
        counts = movie_counts if media_type == 'movie' else show_counts
        for item in items:
            tmdb_id = item.get('tmdb_id', '')
            imdb_id = all_imdb_ids.get(tmdb_id, '')
            days_listed = (now - datetime.fromisoformat(item['added_date'])).days
            # Show shared count if more than one user
            user_count = counts.get(str(tmdb_id), 1)
            shared_badge = f'<span class="shared-badge" title="{user_count} of {total_users} users want this">{user_count}/{total_users}</span>' if total_users > 1 else ''
            # Render streaming icons (with rent/buy fallback)
            streaming_services = item.get('streaming_services', [])
            rent_services = item.get('rent_services', [])
            buy_services = item.get('buy_services', [])
            streaming_html = render_streaming_icons(streaming_services, user_services, rent_services, buy_services)
            rows.append(f'''
                <tr data-tmdb="{tmdb_id}" data-imdb="{imdb_id}" data-type="{media_type}" data-user="{user_id}">
                    <td><input type="checkbox" class="select-item"></td>
                    <td>{item['title']} {shared_badge}</td>
                    <td>{item['year']}</td>
                    <td>{item['rating']:.1f}</td>
                    <td>{item['score']:.0%}</td>
                    <td><div class="streaming-icons">{streaming_html}</div></td>
                    <td>{days_listed}</td>
                </tr>''')
        return '\n'.join(rows)

    def render_sequels_table(items, user_services):
        """Render HTML table for missing sequels (Sequel Huntarr)"""
        rows = []
        for item in items:
            tmdb_id = item.get('tmdb_id', '')
            imdb_id = all_imdb_ids.get(tmdb_id, '')
            collection_name = item.get('collection_name', 'Unknown')
            owned = item.get('owned_count', 0)
            total = item.get('total_count', 0)
            streaming_services = item.get('streaming_services', [])
            rent_services = item.get('rent_services', [])
            buy_services = item.get('buy_services', [])
            streaming_html = render_streaming_icons(streaming_services, user_services, rent_services, buy_services)
            # Add badges for TV Special and Animated
            badges = ''
            if item.get('is_animated'):
                badges += '<span class="animated-badge">Animated</span>'
            if item.get('is_tv_movie'):
                badges += '<span class="tv-special-badge">TV Special</span>'
            rows.append(f'''
                <tr data-tmdb="{tmdb_id}" data-imdb="{imdb_id}" data-type="movie" data-user="sequel-huntarr">
                    <td><input type="checkbox" class="select-item"></td>
                    <td>{item['title']} {badges}</td>
                    <td>{item.get('year', '')}</td>
                    <td>{collection_name}</td>
                    <td>{owned}/{total}</td>
                    <td><div class="streaming-icons">{streaming_html}</div></td>
                </tr>''')
        return '\n'.join(rows)

    def render_horizon_table(items):
        """Render HTML table for upcoming movies (Horizon Huntarr)"""
        rows = []
        for item in items:
            tmdb_id = item.get('tmdb_id', '')
            imdb_id = all_imdb_ids.get(tmdb_id, '')
            collection_name = item.get('collection_name', 'Unknown')
            release_date = item.get('release_date', 'TBA')
            status = item.get('status', 'Unknown')
            # Status badge styling
            status_class = status.lower().replace(' ', '-')
            rows.append(f'''
                <tr data-tmdb="{tmdb_id}" data-imdb="{imdb_id}" data-type="movie" data-user="horizon-huntarr">
                    <td><input type="checkbox" class="select-item"></td>
                    <td>{item['title']}</td>
                    <td>{collection_name}</td>
                    <td>{release_date}</td>
                    <td><span class="status-badge {status_class}">{status}</span></td>
                </tr>''')
        return '\n'.join(rows)

    # Build tabs HTML
    tabs_html = ""
    panels_html = ""

    for i, user_data in enumerate(all_users_data):
        display_name = user_data['display_name']
        user_id = user_data['username'].lower().replace(' ', '_')
        movies_cat = user_data['movies_categorized']
        shows_cat = user_data['shows_categorized']
        user_services = user_data.get('user_services', [])
        is_active = "active" if i == 0 else ""

        # Tab button
        tabs_html += f'<button class="tab-btn {is_active}" data-user="{user_id}">{display_name}</button>\n'

        # Panel content - use flat all_items sorted by score
        panel_content = ""

        # Movies section - flat table sorted by score
        all_movies = movies_cat.get('all_items', [])
        if all_movies:
            panel_content += f"<h2>Movies to Watch ({len(all_movies)})</h2>"
            panel_content += f'''
                <table>
                    <thead>
                        <tr><th><input type="checkbox" class="select-all-table"></th><th class="sortable">Title</th><th class="sortable">Year</th><th class="sortable">Rating</th><th class="sortable desc">Score</th><th class="sortable">Streaming</th><th class="sortable">Days</th></tr>
                    </thead>
                    <tbody>
                        {render_table_flat(all_movies, 'movie', user_id, user_services)}
                    </tbody>
                </table>'''

        # Shows section - flat table sorted by score
        all_shows = shows_cat.get('all_items', [])
        if all_shows:
            panel_content += f"<h2>TV Shows to Watch ({len(all_shows)})</h2>"
            panel_content += f'''
                <table>
                    <thead>
                        <tr><th><input type="checkbox" class="select-all-table"></th><th class="sortable">Title</th><th class="sortable">Year</th><th class="sortable">Rating</th><th class="sortable desc">Score</th><th class="sortable">Streaming</th><th class="sortable">Days</th></tr>
                    </thead>
                    <tbody>
                        {render_table_flat(all_shows, 'show', user_id, user_services)}
                    </tbody>
                </table>'''

        if not panel_content:
            panel_content = "<p>No recommendations available for this user.</p>"

        panels_html += f'<div class="tab-panel {is_active}" data-user="{user_id}">{panel_content}</div>\n'

    # Build Huntarr tabs (separate row below user tabs)
    huntarr_tabs_html = ""
    first_user_services = all_users_data[0].get('user_services', []) if all_users_data else []
    has_user_tabs = bool(all_users_data)
    first_huntarr_tab = True  # Track if this is the first huntarr tab (for active state)

    # Sequel Huntarr tab
    if missing_sequels:
        # Make first huntarr tab active if no user tabs
        is_active = "active" if not has_user_tabs and first_huntarr_tab else ""
        first_huntarr_tab = False
        huntarr_tabs_html += f'<button class="tab-btn {is_active}" data-user="sequel-huntarr">Sequel Huntarr</button>\n'
        sequels_content = f"<h2>Sequel Huntarr ({len(missing_sequels)})</h2>"
        sequels_content += "<p class=\"subtitle\">Missing movies from collections you've started.</p>"
        sequels_content += f'''
            <table>
                <thead>
                    <tr><th><input type="checkbox" class="select-all-table"></th><th class="sortable">Title</th><th class="sortable">Year</th><th class="sortable">Collection</th><th class="sortable">Owned</th><th class="sortable">Streaming</th></tr>
                </thead>
                <tbody>
                    {render_sequels_table(missing_sequels, first_user_services)}
                </tbody>
            </table>'''
        panels_html += f'<div class="tab-panel {is_active}" data-user="sequel-huntarr">{sequels_content}</div>\n'

    # Horizon Huntarr tab
    if horizon_movies:
        # Make first huntarr tab active if no user tabs (and sequel wasn't first)
        is_active = "active" if not has_user_tabs and first_huntarr_tab else ""
        first_huntarr_tab = False
        huntarr_tabs_html += f'<button class="tab-btn {is_active}" data-user="horizon-huntarr">Horizon Huntarr</button>\n'
        horizon_content = f"<h2>Horizon Huntarr ({len(horizon_movies)})</h2>"
        horizon_content += "<p class=\"subtitle\">Upcoming unreleased movies from collections you own.</p>"
        horizon_content += f'''
            <table>
                <thead>
                    <tr><th><input type="checkbox" class="select-all-table"></th><th class="sortable">Title</th><th class="sortable">Collection</th><th class="sortable">Release Date</th><th class="sortable">Status</th></tr>
                </thead>
                <tbody>
                    {render_horizon_table(horizon_movies)}
                </tbody>
            </table>'''
        panels_html += f'<div class="tab-panel {is_active}" data-user="horizon-huntarr">{horizon_content}</div>\n'

    html_content = _generate_html_template(tabs_html, panels_html, now, huntarr_tabs_html)

    with open(output_file, 'w') as f:
        f.write(html_content)

    return output_file


def _generate_html_template(tabs_html: str, panels_html: str, now: datetime, huntarr_tabs_html: str = "") -> str:
    """Generate the full HTML template with CSS and JavaScript."""
    # If no user tabs but huntarr tabs exist, put huntarr tabs in the main tabs row
    # Otherwise, huntarr tabs go in their own row below user tabs
    if not tabs_html.strip() and huntarr_tabs_html:
        tabs_html = huntarr_tabs_html
        huntarr_tabs_row = ""
    elif huntarr_tabs_html:
        huntarr_tabs_row = f'''
            <div class="huntarr-tabs">
                {huntarr_tabs_html}
            </div>'''
    else:
        huntarr_tabs_row = ""

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Curatarr Watchlist</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Playfair+Display:wght@700&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; }}

        html {{
            background: #080808;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 50px 70px 40px;
            background: linear-gradient(180deg, #0c0c0c 0%, #111 50%, #0c0c0c 100%);
            color: #e0e0e0;
            min-height: 100vh;
            position: relative;
            line-height: 1.6;
        }}

        /* Draped curtain left */
        body::before {{
            content: '';
            position: fixed;
            left: 0;
            top: 0;
            width: 60px;
            height: 100%;
            background:
                radial-gradient(ellipse 80% 50% at 100% 0%, transparent 40%, rgba(0,0,0,0.4) 100%),
                linear-gradient(90deg,
                    #2a0000 0%,
                    #4a0000 15%,
                    #6b0000 30%,
                    #8b0000 45%,
                    #7a0000 55%,
                    #5a0000 70%,
                    #3a0000 85%,
                    #1a0000 100%);
            box-shadow:
                inset -15px 0 40px rgba(0,0,0,0.6),
                8px 0 30px rgba(0,0,0,0.5);
            z-index: 100;
            border-radius: 0 0 40% 0;
        }}

        /* Draped curtain right */
        body::after {{
            content: '';
            position: fixed;
            right: 0;
            top: 0;
            width: 60px;
            height: 100%;
            background:
                radial-gradient(ellipse 80% 50% at 0% 0%, transparent 40%, rgba(0,0,0,0.4) 100%),
                linear-gradient(270deg,
                    #2a0000 0%,
                    #4a0000 15%,
                    #6b0000 30%,
                    #8b0000 45%,
                    #7a0000 55%,
                    #5a0000 70%,
                    #3a0000 85%,
                    #1a0000 100%);
            box-shadow:
                inset 15px 0 40px rgba(0,0,0,0.6),
                -8px 0 30px rgba(0,0,0,0.5);
            z-index: 100;
            border-radius: 0 0 0 40%;
        }}

        /* Draped valance top */
        .curtain-top {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 30px;
            background: linear-gradient(180deg,
                #5a0000 0%,
                #7b0000 40%,
                #6a0000 70%,
                #4a0000 100%);
            box-shadow: 0 8px 30px rgba(0,0,0,0.6);
            z-index: 101;
        }}
        .curtain-top::after {{
            content: '';
            position: absolute;
            bottom: -20px;
            left: 0;
            right: 0;
            height: 20px;
            background:
                radial-gradient(ellipse 60px 20px at 30px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 90px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 150px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 210px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 270px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 330px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 390px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 450px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 510px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 570px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 630px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 690px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 750px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 810px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 870px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 930px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 990px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1050px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1110px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1170px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1230px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1290px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1350px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1410px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1470px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1530px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1590px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1650px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1710px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1770px 0px, #4a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1830px 0px, #5a0000 70%, transparent 70%),
                radial-gradient(ellipse 60px 20px at 1890px 0px, #4a0000 70%, transparent 70%);
            filter: drop-shadow(0 3px 4px rgba(0,0,0,0.4));
        }}

        .page-wrapper {{
            position: relative;
            z-index: 1;
        }}

        /* Branding */
        .brand {{
            text-align: center;
            margin-bottom: 40px;
            padding-top: 20px;
        }}
        .brand h1 {{
            font-family: 'Playfair Display', Georgia, serif;
            font-size: 3.2em;
            margin: 0;
            background: linear-gradient(180deg, #ffd700 0%, #d4af37 30%, #b8960c 60%, #d4af37 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: 8px;
            font-weight: 700;
            filter: drop-shadow(0 2px 4px rgba(0,0,0,0.3));
        }}
        .brand .subtitle {{
            color: #999;
            font-size: 0.9em;
            letter-spacing: 6px;
            text-transform: uppercase;
            margin-top: 8px;
            font-weight: 500;
        }}
        .brand .timestamp {{
            color: #666;
            font-size: 0.8em;
            margin-top: 12px;
        }}

        h2 {{
            background: linear-gradient(135deg, #8b0000 0%, #6a0000 50%, #580000 100%);
            color: #f0d060;
            padding: 16px 22px;
            border-radius: 12px;
            margin-top: 40px;
            border: none;
            text-transform: uppercase;
            letter-spacing: 2px;
            font-size: 0.95em;
            font-weight: 600;
            box-shadow:
                0 8px 25px rgba(0,0,0,0.4),
                inset 0 1px 0 rgba(255,255,255,0.1),
                inset 0 -2px 5px rgba(0,0,0,0.2);
        }}
        h3 {{
            background: linear-gradient(135deg, #1e1e1e 0%, #282828 100%);
            color: #ccc;
            padding: 14px 18px;
            border-radius: 10px;
            border: 1px solid #333;
            font-size: 0.9em;
            font-weight: 600;
            box-shadow:
                0 4px 15px rgba(0,0,0,0.3),
                inset 0 1px 0 rgba(255,255,255,0.05);
        }}
        h4 {{
            color: #d4af37;
            margin: 20px 0 12px 0;
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            font-weight: 600;
        }}

        .header-actions {{
            display: flex;
            justify-content: center;
            gap: 16px;
            margin-bottom: 35px;
            flex-wrap: wrap;
        }}
        .export-btn {{
            background: linear-gradient(180deg, #a01010 0%, #8b0000 40%, #6a0000 100%);
            color: #ffd700;
            border: none;
            padding: 14px 32px;
            border-radius: 50px;
            cursor: pointer;
            font-family: 'Inter', sans-serif;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow:
                0 6px 20px rgba(139, 0, 0, 0.4),
                0 2px 5px rgba(0,0,0,0.3),
                inset 0 1px 0 rgba(255,255,255,0.15),
                inset 0 -2px 10px rgba(0,0,0,0.2);
            position: relative;
            overflow: hidden;
        }}
        .export-btn::before {{
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.1), transparent);
            transition: left 0.5s ease;
        }}
        .export-btn:hover {{
            transform: translateY(-3px);
            box-shadow:
                0 10px 30px rgba(139, 0, 0, 0.5),
                0 4px 10px rgba(0,0,0,0.3),
                inset 0 1px 0 rgba(255,255,255,0.2);
        }}
        .export-btn:hover::before {{
            left: 100%;
        }}
        .export-btn:active {{
            transform: translateY(-1px);
            box-shadow:
                0 4px 15px rgba(139, 0, 0, 0.4),
                inset 0 2px 5px rgba(0,0,0,0.2);
        }}
        .export-btn.sonarr {{
            background: linear-gradient(180deg, #404040 0%, #2d2d2d 40%, #1a1a1a 100%);
            color: #e0e0e0;
            box-shadow:
                0 6px 20px rgba(0, 0, 0, 0.4),
                0 2px 5px rgba(0,0,0,0.3),
                inset 0 1px 0 rgba(255,255,255,0.1),
                inset 0 -2px 10px rgba(0,0,0,0.2);
        }}
        .export-btn.sonarr:hover {{
            box-shadow:
                0 10px 30px rgba(0, 0, 0, 0.5),
                0 4px 10px rgba(0,0,0,0.3),
                inset 0 1px 0 rgba(255,255,255,0.15);
        }}
        .export-btn.trakt {{
            background: linear-gradient(180deg, #ff3333 0%, #ed1c24 40%, #c41920 100%);
            color: #fff;
            box-shadow:
                0 6px 20px rgba(237, 28, 36, 0.4),
                0 2px 5px rgba(0,0,0,0.3),
                inset 0 1px 0 rgba(255,255,255,0.2),
                inset 0 -2px 10px rgba(0,0,0,0.2);
        }}
        .export-btn.trakt:hover {{
            box-shadow:
                0 10px 30px rgba(237, 28, 36, 0.5),
                0 4px 10px rgba(0,0,0,0.3),
                inset 0 1px 0 rgba(255,255,255,0.25);
        }}

        .tabs-wrapper {{
            display: flex;
            flex-direction: column;
            align-items: center;
            margin-bottom: 35px;
            gap: 12px;
        }}
        .tabs {{
            display: inline-flex;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: center;
            background: linear-gradient(180deg, #0a0a0a 0%, #0f0f0f 100%);
            padding: 12px 20px;
            border-radius: 16px;
            box-shadow:
                inset 0 2px 10px rgba(0,0,0,0.6),
                0 4px 15px rgba(0,0,0,0.3);
        }}
        .huntarr-tabs {{
            display: inline-flex;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: center;
            background: linear-gradient(180deg, #0a0808 0%, #0c0808 100%);
            padding: 10px 16px;
            border-radius: 12px;
            box-shadow:
                inset 0 2px 8px rgba(0,0,0,0.5),
                0 3px 12px rgba(0,0,0,0.2);
        }}
        .tab-btn {{
            background: linear-gradient(180deg, #1c1c1c 0%, #151515 100%);
            color: #888;
            border: none;
            padding: 14px 28px;
            border-radius: 12px;
            cursor: pointer;
            font-family: 'Inter', sans-serif;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        }}
        .tab-btn:hover {{
            background: linear-gradient(180deg, #282828 0%, #1f1f1f 100%);
            color: #bbb;
            transform: translateY(-1px);
        }}
        .tab-btn.active {{
            background: linear-gradient(180deg, #a01010 0%, #8b0000 40%, #6a0000 100%);
            color: #ffd700;
            box-shadow:
                0 6px 20px rgba(139, 0, 0, 0.5),
                inset 0 1px 0 rgba(255,255,255,0.1);
        }}
        .tab-panel {{ display: none; }}
        .tab-panel.active {{ display: block; animation: fadeIn 0.4s ease; }}

        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(15px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}

        table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            margin: 15px 0 35px 0;
            background: linear-gradient(180deg, #181818 0%, #141414 50%, #111 100%);
            border-radius: 16px;
            overflow: hidden;
            box-shadow:
                0 10px 40px rgba(0,0,0,0.5),
                0 2px 10px rgba(0,0,0,0.3),
                inset 0 1px 0 rgba(255,255,255,0.03);
        }}
        th, td {{
            padding: 16px 14px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        th {{
            background: linear-gradient(180deg, #222 0%, #1a1a1a 100%);
            color: #d4af37;
            font-size: 0.7em;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            font-weight: 600;
        }}
        th.sortable {{
            cursor: pointer;
            user-select: none;
            transition: color 0.2s ease;
            white-space: nowrap;
        }}
        th.sortable:hover {{
            color: #ffd700;
        }}
        th.sortable::after {{
            content: ' \\25B2\\25BC';
            opacity: 0.3;
            font-size: 0.6em;
            margin-left: 4px;
            vertical-align: middle;
        }}
        th.sortable.asc::after {{
            content: ' \\25B2';
            opacity: 1;
            font-size: 0.7em;
        }}
        th.sortable.desc::after {{
            content: ' \\25BC';
            opacity: 1;
            font-size: 0.7em;
        }}
        th:first-child {{ border-radius: 16px 0 0 0; }}
        th:last-child {{ border-radius: 0 16px 0 0; }}
        tr {{
            transition: all 0.2s ease;
        }}
        tr:hover {{
            background: rgba(139, 0, 0, 0.1);
        }}
        tr:last-child td {{
            border-bottom: none;
        }}
        tr:last-child td:first-child {{ border-radius: 0 0 0 16px; }}
        tr:last-child td:last-child {{ border-radius: 0 0 16px 0; }}
        td:first-child, th:first-child {{ width: 50px; text-align: center; }}
        input[type="checkbox"] {{
            width: 18px;
            height: 18px;
            cursor: pointer;
            accent-color: #8b0000;
            border-radius: 4px;
        }}
        .shared-badge {{
            display: inline-block;
            background: linear-gradient(135deg, #8b0000, #a00);
            color: #fff;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 12px;
            margin-left: 8px;
            vertical-align: middle;
            box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }}
        .tv-special-badge {{
            display: inline-block;
            background: linear-gradient(135deg, #6b46c1, #805ad5);
            color: #fff;
            font-size: 10px;
            font-weight: 600;
            padding: 2px 6px;
            border-radius: 4px;
            margin-left: 8px;
            vertical-align: middle;
            box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }}
        .animated-badge {{
            display: inline-block;
            background: linear-gradient(135deg, #0891b2, #06b6d4);
            color: #fff;
            font-size: 10px;
            font-weight: 600;
            padding: 2px 6px;
            border-radius: 4px;
            margin-left: 8px;
            vertical-align: middle;
            box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }}

        /* Status badges for Horizon Huntarr */
        .status-badge {{
            display: inline-block;
            font-size: 10px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }}
        .status-badge.post-production {{
            background: linear-gradient(135deg, #059669, #10b981);
            color: #fff;
        }}
        .status-badge.in-production {{
            background: linear-gradient(135deg, #0284c7, #0ea5e9);
            color: #fff;
        }}
        .status-badge.planned {{
            background: linear-gradient(135deg, #7c3aed, #8b5cf6);
            color: #fff;
        }}
        .status-badge.rumored {{
            background: linear-gradient(135deg, #475569, #64748b);
            color: #fff;
        }}
        .status-badge.unknown {{
            background: linear-gradient(135deg, #374151, #4b5563);
            color: #9ca3af;
        }}

        /* Streaming service icons */
        .streaming-icons {{
            display: flex;
            flex-wrap: wrap;
            gap: 3px;
            max-width: 280px;
        }}
        .streaming-icon {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
            white-space: nowrap;
            box-shadow: 0 1px 2px rgba(0,0,0,0.3);
            max-width: 260px;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .streaming-icon.user-service {{
            border: 2px solid #d4af37;
            box-shadow: 0 0 6px rgba(212, 175, 55, 0.4);
        }}
        .streaming-icon.netflix {{ background: #e50914; color: #fff; }}
        .streaming-icon.hulu {{ background: #1ce783; color: #000; }}
        .streaming-icon.disney_plus {{ background: #113ccf; color: #fff; }}
        .streaming-icon.amazon_prime {{ background: #00a8e1; color: #fff; }}
        .streaming-icon.paramount_plus {{ background: #0064ff; color: #fff; }}
        .streaming-icon.apple_tv_plus {{ background: #000; color: #fff; }}
        .streaming-icon.max {{ background: #002be7; color: #fff; }}
        .streaming-icon.peacock {{ background: #000; color: #fff; }}
        .streaming-icon.crunchyroll {{ background: #f47521; color: #fff; }}
        .streaming-icon.crackle {{ background: #f36f21; color: #fff; }}
        .streaming-icon.tubi {{ background: #fa382f; color: #fff; }}
        .streaming-icon.mubi {{ background: #0b0c0f; color: #fff; }}
        .streaming-icon.shudder {{ background: #000; color: #fff; }}
        .streaming-icon.acquire {{ background: #444; color: #aaa; font-style: italic; }}
        .streaming-icon.rent {{ background: linear-gradient(135deg, #004B93, #0066CC); color: #FFD700; font-weight: 600; }}
        .streaming-icon.buy {{ background: linear-gradient(135deg, #2563eb, #3b82f6); color: #fff; }}

        .instructions {{
            background: linear-gradient(180deg, #181818 0%, #121212 100%);
            padding: 30px 35px;
            border-radius: 20px;
            margin-top: 60px;
            border: 1px solid rgba(255,255,255,0.05);
            box-shadow:
                0 10px 40px rgba(0,0,0,0.4),
                inset 0 1px 0 rgba(255,255,255,0.03);
        }}
        .instructions h3 {{
            background: none;
            border: none;
            padding: 0;
            color: #d4af37;
            margin-bottom: 20px;
            box-shadow: none;
            font-size: 1.1em;
        }}
        .instructions ul {{
            margin: 0;
            padding-left: 24px;
            color: #999;
        }}
        .instructions li {{
            margin-bottom: 12px;
            line-height: 1.7;
        }}
        .instructions strong {{
            color: #ccc;
            font-weight: 600;
        }}

        .footer {{
            text-align: center;
            margin-top: 50px;
            padding-top: 30px;
            border-top: 1px solid rgba(255,255,255,0.05);
            color: #555;
            font-size: 0.8em;
            letter-spacing: 1px;
        }}
        .footer a {{
            color: #8b0000;
            text-decoration: none;
            font-weight: 500;
            transition: color 0.3s ease;
        }}
        .footer a:hover {{
            color: #d4af37;
        }}

        /* Filter bar - Art Deco Cinema style */
        .filter-bar {{
            display: flex;
            gap: 20px;
            align-items: flex-end;
            justify-content: center;
            background: linear-gradient(180deg, #1a1714 0%, #141210 100%);
            padding: 22px 35px 18px;
            border-radius: 6px;
            margin-bottom: 30px;
            border: 2px solid #3d3428;
            box-shadow:
                0 10px 40px rgba(0,0,0,0.5),
                inset 0 1px 0 rgba(212, 175, 55, 0.15),
                inset 0 -1px 0 rgba(0,0,0,0.3);
            position: relative;
        }}
        /* Top gold pinstripe */
        .filter-bar::before {{
            content: '';
            position: absolute;
            top: -2px;
            left: 30px;
            right: 30px;
            height: 3px;
            background: linear-gradient(90deg, transparent, #b8960c 15%, #d4af37 35%, #f0d060 50%, #d4af37 65%, #b8960c 85%, transparent);
            border-radius: 0 0 2px 2px;
        }}
        /* Film strip sprocket holes - left */
        .filter-bar::after {{
            content: '';
            position: absolute;
            left: 10px;
            top: 50%;
            transform: translateY(-50%);
            width: 6px;
            height: 60%;
            background: repeating-linear-gradient(
                180deg,
                transparent 0px,
                transparent 4px,
                #2a2520 4px,
                #2a2520 10px,
                transparent 10px,
                transparent 14px
            );
            opacity: 0.6;
        }}
        .filter-group {{
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .filter-group label {{
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: #d4af37;
            font-weight: 600;
            font-variant: small-caps;
        }}
        .filter-group input[type="text"],
        .filter-group input[type="number"] {{
            background: linear-gradient(180deg, #0c0b09 0%, #100f0d 100%);
            border: 1px solid #4a4030;
            border-top-color: #2a2520;
            border-left-color: #2a2520;
            color: #e8dcc8;
            padding: 10px 12px;
            border-radius: 3px;
            font-family: 'Inter', sans-serif;
            font-size: 13px;
            width: 75px;
            transition: all 0.2s ease;
            box-shadow:
                inset 1px 1px 3px rgba(0,0,0,0.5),
                0 1px 0 rgba(212, 175, 55, 0.1);
        }}
        .filter-group input[type="text"] {{
            width: 140px;
        }}
        .filter-group input:focus {{
            outline: none;
            border-color: #d4af37;
            box-shadow:
                inset 1px 1px 3px rgba(0,0,0,0.5),
                0 0 8px rgba(212, 175, 55, 0.4);
        }}
        .filter-group input::placeholder {{
            color: #5a5040;
            font-style: italic;
            font-size: 12px;
        }}
        /* Year inputs side by side */
        .year-inputs {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .year-inputs input {{
            width: 65px;
        }}
        .year-separator {{
            color: #5a5040;
            font-size: 14px;
        }}
        .streaming-filter {{
            position: relative;
        }}
        .streaming-dropdown {{
            position: relative;
        }}
        .dropdown-toggle {{
            background: linear-gradient(180deg, #0c0b09 0%, #100f0d 100%);
            border: 1px solid #4a4030;
            border-top-color: #2a2520;
            border-left-color: #2a2520;
            color: #e8dcc8;
            padding: 10px 12px;
            border-radius: 3px;
            font-family: 'Inter', sans-serif;
            font-size: 13px;
            cursor: pointer;
            min-width: 135px;
            text-align: left;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: all 0.2s ease;
            box-shadow:
                inset 1px 1px 3px rgba(0,0,0,0.5),
                0 1px 0 rgba(212, 175, 55, 0.1);
        }}
        .dropdown-toggle:hover {{
            border-color: #5a4a30;
        }}
        .dropdown-toggle .arrow {{
            font-size: 10px;
            color: #d4af37;
        }}
        .dropdown-menu {{
            display: none;
            position: absolute;
            top: 100%;
            left: -20px;
            width: 180px;
            background: linear-gradient(180deg, #1c1a16 0%, #14120f 100%);
            border: 2px solid #3d3428;
            border-radius: 4px;
            margin-top: 6px;
            padding: 8px 6px;
            z-index: 1000;
            max-height: 320px;
            overflow-y: auto;
            box-shadow:
                0 15px 45px rgba(0,0,0,0.7),
                inset 0 1px 0 rgba(212, 175, 55, 0.1);
        }}
        /* Art deco corner accents on dropdown */
        .dropdown-menu::before {{
            content: '';
            position: absolute;
            top: 4px;
            left: 4px;
            width: 12px;
            height: 12px;
            border-left: 2px solid #d4af37;
            border-top: 2px solid #d4af37;
        }}
        .dropdown-menu::after {{
            content: '';
            position: absolute;
            top: 4px;
            right: 4px;
            width: 12px;
            height: 12px;
            border-right: 2px solid #d4af37;
            border-top: 2px solid #d4af37;
        }}
        .dropdown-menu.show {{
            display: block;
            animation: dropIn 0.2s ease;
        }}
        @keyframes dropIn {{
            from {{ opacity: 0; transform: translateY(-8px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        .dropdown-menu label {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px;
            margin: 2px 0;
            cursor: pointer;
            font-size: 12px;
            font-weight: 500;
            border-radius: 3px;
            text-transform: none;
            letter-spacing: 0;
            font-variant: normal;
            transition: all 0.15s ease;
            border: 1px solid transparent;
        }}
        /* Service-specific colors */
        .dropdown-menu label[data-service="user-service"] {{
            color: #ffd700;
            background: linear-gradient(90deg, rgba(212, 175, 55, 0.1) 0%, transparent 100%);
            border-left: 3px solid #d4af37;
        }}
        .dropdown-menu label[data-service="netflix"] {{
            color: #ff6b6b;
            background: linear-gradient(90deg, rgba(229, 9, 20, 0.15) 0%, transparent 100%);
            border-left: 3px solid #e50914;
        }}
        .dropdown-menu label[data-service="hulu"] {{
            color: #6ee7a0;
            background: linear-gradient(90deg, rgba(28, 231, 131, 0.12) 0%, transparent 100%);
            border-left: 3px solid #1ce783;
        }}
        .dropdown-menu label[data-service="disney_plus"] {{
            color: #7da0e0;
            background: linear-gradient(90deg, rgba(17, 60, 207, 0.15) 0%, transparent 100%);
            border-left: 3px solid #113ccf;
        }}
        .dropdown-menu label[data-service="amazon_prime"] {{
            color: #6dcff6;
            background: linear-gradient(90deg, rgba(0, 168, 225, 0.12) 0%, transparent 100%);
            border-left: 3px solid #00a8e1;
        }}
        .dropdown-menu label[data-service="max"] {{
            color: #8080ff;
            background: linear-gradient(90deg, rgba(0, 43, 231, 0.15) 0%, transparent 100%);
            border-left: 3px solid #002be7;
        }}
        .dropdown-menu label[data-service="paramount_plus"] {{
            color: #6699ff;
            background: linear-gradient(90deg, rgba(0, 100, 255, 0.12) 0%, transparent 100%);
            border-left: 3px solid #0064ff;
        }}
        .dropdown-menu label[data-service="apple_tv_plus"] {{
            color: #e0e0e0;
            background: linear-gradient(90deg, rgba(255, 255, 255, 0.08) 0%, transparent 100%);
            border-left: 3px solid #888;
        }}
        .dropdown-menu label[data-service="peacock"] {{
            color: #e0e0e0;
            background: linear-gradient(90deg, rgba(255, 255, 255, 0.06) 0%, transparent 100%);
            border-left: 3px solid #666;
        }}
        .dropdown-menu label[data-service="crunchyroll"] {{
            color: #ffa060;
            background: linear-gradient(90deg, rgba(244, 117, 33, 0.12) 0%, transparent 100%);
            border-left: 3px solid #f47521;
        }}
        .dropdown-menu label[data-service="tubi"] {{
            color: #ff7070;
            background: linear-gradient(90deg, rgba(250, 56, 47, 0.12) 0%, transparent 100%);
            border-left: 3px solid #fa382f;
        }}
        .dropdown-menu label[data-service="rent"] {{
            color: #ffd700;
            background: linear-gradient(90deg, rgba(0, 75, 147, 0.2) 0%, transparent 100%);
            border-left: 3px solid #004B93;
        }}
        .dropdown-menu label[data-service="acquire"] {{
            color: #999;
            background: linear-gradient(90deg, rgba(100, 100, 100, 0.1) 0%, transparent 100%);
            border-left: 3px solid #555;
        }}
        .dropdown-menu label:hover {{
            border: 1px solid rgba(212, 175, 55, 0.4);
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,0.1),
                0 2px 8px rgba(0,0,0,0.3);
            transform: translateX(2px);
        }}
        .dropdown-menu label:has(input:checked) {{
            border: 1px solid rgba(212, 175, 55, 0.6);
            box-shadow:
                inset 0 2px 4px rgba(0,0,0,0.4),
                inset 0 -1px 0 rgba(255,255,255,0.1),
                0 0 10px rgba(212, 175, 55, 0.2);
        }}
        .dropdown-menu input[type="checkbox"] {{
            width: 14px;
            height: 14px;
            accent-color: #d4af37;
            cursor: pointer;
        }}
        .clear-filters-btn {{
            background: linear-gradient(180deg, #a01010 0%, #8b0000 40%, #6a0000 100%);
            color: #ffd700;
            border: none;
            padding: 10px 18px;
            border-radius: 3px;
            cursor: pointer;
            font-family: 'Inter', sans-serif;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s ease;
            box-shadow:
                0 3px 10px rgba(139, 0, 0, 0.4),
                inset 0 1px 0 rgba(255,255,255,0.1),
                inset 0 -1px 0 rgba(0,0,0,0.2);
        }}
        .clear-filters-btn:hover {{
            transform: translateY(-1px);
            box-shadow:
                0 5px 15px rgba(139, 0, 0, 0.5),
                inset 0 1px 0 rgba(255,255,255,0.15);
        }}
        tr.filtered-out {{
            display: none;
        }}
    </style>
</head>
<body>
    <div class="curtain-top"></div>

    <div class="page-wrapper">
        <div class="brand">
            <h1>CURATARR</h1>
            <div class="subtitle">Watchlist</div>
            <div class="timestamp">Updated {now.strftime('%B %d, %Y at %H:%M')}</div>
        </div>

        <div class="header-actions">
            <button class="export-btn" onclick="exportRadarr()">Export to Radarr (<span id="movie-count">0</span>)</button>
            <button class="export-btn sonarr" onclick="exportSonarr()">Export to Sonarr (<span id="show-count">0</span>)</button>
            <button class="export-btn trakt" onclick="exportTrakt()">Export for Trakt (<span id="total-count">0</span>)</button>
        </div>

        <div class="filter-bar">
            <div class="filter-group">
                <label>Search</label>
                <input type="text" id="filter-search" placeholder="Title..." oninput="applyFilters()">
            </div>
            <div class="filter-group">
                <label>Rating</label>
                <input type="number" id="filter-rating-min" placeholder="Min" min="0" max="10" step="0.1" oninput="applyFilters()">
            </div>
            <div class="filter-group filter-group-year">
                <label>Year</label>
                <div class="year-inputs">
                    <input type="number" id="filter-year-min" placeholder="From" min="1900" max="2030" oninput="applyFilters()">
                    <span class="year-separator">–</span>
                    <input type="number" id="filter-year-max" placeholder="To" min="1900" max="2030" oninput="applyFilters()">
                </div>
            </div>
            <div class="filter-group">
                <label>Days Listed</label>
                <input type="number" id="filter-days-max" placeholder="Max" min="0" oninput="applyFilters()">
            </div>
            <div class="filter-group streaming-filter">
                <label>Streaming</label>
                <div class="streaming-dropdown" id="streaming-dropdown">
                    <button type="button" class="dropdown-toggle" onclick="toggleStreamingDropdown()">
                        All Services <span class="arrow">&#9662;</span>
                    </button>
                    <div class="dropdown-menu" id="streaming-menu">
                        <label data-service="user-service"><input type="checkbox" value="user-service" onchange="applyFilters()"> My Services</label>
                        <label data-service="netflix"><input type="checkbox" value="netflix" onchange="applyFilters()"> Netflix</label>
                        <label data-service="hulu"><input type="checkbox" value="hulu" onchange="applyFilters()"> Hulu</label>
                        <label data-service="disney_plus"><input type="checkbox" value="disney_plus" onchange="applyFilters()"> Disney+</label>
                        <label data-service="amazon_prime"><input type="checkbox" value="amazon_prime" onchange="applyFilters()"> Prime</label>
                        <label data-service="max"><input type="checkbox" value="max" onchange="applyFilters()"> Max</label>
                        <label data-service="paramount_plus"><input type="checkbox" value="paramount_plus" onchange="applyFilters()"> Paramount+</label>
                        <label data-service="apple_tv_plus"><input type="checkbox" value="apple_tv_plus" onchange="applyFilters()"> Apple TV+</label>
                        <label data-service="peacock"><input type="checkbox" value="peacock" onchange="applyFilters()"> Peacock</label>
                        <label data-service="crunchyroll"><input type="checkbox" value="crunchyroll" onchange="applyFilters()"> Crunchyroll</label>
                        <label data-service="tubi"><input type="checkbox" value="tubi" onchange="applyFilters()"> Tubi</label>
                        <label data-service="rent"><input type="checkbox" value="rent" onchange="applyFilters()"> Rent</label>
                        <label data-service="acquire"><input type="checkbox" value="acquire" onchange="applyFilters()"> Acquire</label>
                    </div>
                </div>
            </div>
            <button class="clear-filters-btn" onclick="clearFilters()">Clear</button>
        </div>

        <div class="tabs-wrapper">
            <div class="tabs">
                {tabs_html}
            </div>
            {huntarr_tabs_row}
        </div>

        {panels_html}

        <div class="instructions">
            <h3>How to Use</h3>
            <ul>
                <li>Click a user tab to view their personalized recommendations</li>
                <li>Check the items you want to export</li>
                <li><strong>Radarr:</strong> Download IMDB IDs for selected movies and import via Lists</li>
                <li><strong>Sonarr:</strong> Download IMDB IDs for selected shows and import via Lists</li>
                <li><strong>Trakt:</strong> Download IMDB IDs for all selected items to paste into a Trakt list</li>
                <li>Exports include selections from all users, not just the active tab</li>
            </ul>
        </div>

        <div class="footer">
            Powered by <a href="https://github.com/OrchestratedChaos/curatarr" target="_blank">Curatarr</a>
        </div>
    </div>

    <script>
        // Tab switching
        document.querySelectorAll('.tab-btn').forEach(btn => {{
            btn.addEventListener('click', function() {{
                const userId = this.getAttribute('data-user');

                // Update active tab
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                this.classList.add('active');

                // Show corresponding panel
                document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
                document.querySelector(`.tab-panel[data-user="${{userId}}"]`).classList.add('active');
            }});
        }});

        // Select-all checkbox functionality (per table)
        document.querySelectorAll('.select-all-table').forEach(selectAll => {{
            selectAll.addEventListener('change', function() {{
                const table = this.closest('table');
                table.querySelectorAll('.select-item').forEach(cb => {{
                    cb.checked = this.checked;
                }});
                updateCounts();
            }});
        }});

        // Update counts when individual items are checked
        document.querySelectorAll('.select-item').forEach(cb => {{
            cb.addEventListener('change', updateCounts);
        }});

        function updateCounts() {{
            // Count ALL selected items across ALL users (excluding filtered-out rows)
            const movieCount = document.querySelectorAll('tr[data-type="movie"]:not(.filtered-out) .select-item:checked').length;
            const showCount = document.querySelectorAll('tr[data-type="show"]:not(.filtered-out) .select-item:checked').length;
            document.getElementById('movie-count').textContent = movieCount;
            document.getElementById('show-count').textContent = showCount;
            document.getElementById('total-count').textContent = movieCount + showCount;
        }}

        function exportRadarr() {{
            // Export from ALL users (excluding filtered-out rows)
            const rows = document.querySelectorAll('tr[data-type="movie"]:not(.filtered-out)');
            const imdbIds = [];
            rows.forEach(row => {{
                const checkbox = row.querySelector('.select-item');
                if (checkbox && checkbox.checked) {{
                    const imdb = row.getAttribute('data-imdb');
                    if (imdb && imdb.startsWith('tt')) {{
                        imdbIds.push(imdb);
                    }}
                }}
            }});
            if (imdbIds.length === 0) {{
                alert('No selected movies with IMDB IDs to export. Select items first.');
                return;
            }}
            downloadFile('radarr_import.txt', [...new Set(imdbIds)].join('\\n'));
            alert('Exported ' + imdbIds.length + ' movies for Radarr import.');
        }}

        function exportSonarr() {{
            // Export from ALL users (excluding filtered-out rows)
            const rows = document.querySelectorAll('tr[data-type="show"]:not(.filtered-out)');
            const imdbIds = [];
            rows.forEach(row => {{
                const checkbox = row.querySelector('.select-item');
                if (checkbox && checkbox.checked) {{
                    const imdb = row.getAttribute('data-imdb');
                    if (imdb && imdb.startsWith('tt')) {{
                        imdbIds.push(imdb);
                    }}
                }}
            }});
            if (imdbIds.length === 0) {{
                alert('No selected TV shows with IMDB IDs to export. Select items first.');
                return;
            }}
            downloadFile('sonarr_import.txt', [...new Set(imdbIds)].join('\\n'));
            alert('Exported ' + imdbIds.length + ' shows for Sonarr import.');
        }}

        function exportTrakt() {{
            // Export ALL selected items (movies + shows) for Trakt import (excluding filtered-out rows)
            const allRows = document.querySelectorAll('tr[data-imdb]:not(.filtered-out)');
            const imdbIds = [];
            allRows.forEach(row => {{
                const checkbox = row.querySelector('.select-item');
                if (checkbox && checkbox.checked) {{
                    const imdb = row.getAttribute('data-imdb');
                    if (imdb && imdb.startsWith('tt')) {{
                        imdbIds.push(imdb);
                    }}
                }}
            }});
            if (imdbIds.length === 0) {{
                alert('No selected items with IMDB IDs to export. Select items first.');
                return;
            }}
            // Trakt accepts IMDB IDs one per line for list import
            downloadFile('trakt_import.txt', [...new Set(imdbIds)].join('\\n'));
            alert('Exported ' + imdbIds.length + ' items for Trakt.\\n\\nTo import:\\n1. Go to trakt.tv/users/YOUR_USERNAME/lists\\n2. Create or edit a list\\n3. Click "Add Items" and paste the IMDB IDs');
        }}

        function downloadFile(filename, content) {{
            const blob = new Blob([content], {{ type: 'text/plain' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }}

        // Column sorting
        function sortTable(th, colIndex) {{
            const table = th.closest('table');
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));

            // Determine sort direction
            const isAsc = th.classList.contains('asc');
            const isDesc = th.classList.contains('desc');

            // Clear all sort classes in this table
            table.querySelectorAll('th.sortable').forEach(header => {{
                header.classList.remove('asc', 'desc');
            }});

            // Set new sort direction
            let direction;
            if (!isAsc && !isDesc) {{
                direction = 'desc'; // Default to descending (highest first)
            }} else if (isDesc) {{
                direction = 'asc';
            }} else {{
                direction = 'desc';
            }}
            th.classList.add(direction);

            // Sort rows
            rows.sort((a, b) => {{
                const aCell = a.cells[colIndex];
                const bCell = b.cells[colIndex];

                // Handle streaming icons column (sort by service name)
                const aIcons = aCell.querySelectorAll('.streaming-icon');
                const bIcons = bCell.querySelectorAll('.streaming-icon');
                if (aIcons.length > 0 || bIcons.length > 0) {{
                    // Get first service name (or "zzz" for Acquire to sort last)
                    const aFirst = aCell.querySelector('.streaming-icon')?.textContent?.trim() || '';
                    const bFirst = bCell.querySelector('.streaming-icon')?.textContent?.trim() || '';
                    const aName = aFirst === 'Acquire' ? 'zzz' : aFirst;
                    const bName = bFirst === 'Acquire' ? 'zzz' : bFirst;
                    return direction === 'asc' ? aName.localeCompare(bName) : bName.localeCompare(aName);
                }}

                // Get text, excluding badge content for title column
                let aVal = aCell.childNodes[0]?.textContent?.trim() || aCell.textContent.trim();
                let bVal = bCell.childNodes[0]?.textContent?.trim() || bCell.textContent.trim();

                // Remove any trailing badge text (like "2/3")
                aVal = aVal.replace(/\\s+\\d+\\/\\d+$/, '').trim();
                bVal = bVal.replace(/\\s+\\d+\\/\\d+$/, '').trim();

                // Handle percentages (Score column)
                if (aVal.endsWith('%') && bVal.endsWith('%')) {{
                    return direction === 'asc'
                        ? parseFloat(aVal) - parseFloat(bVal)
                        : parseFloat(bVal) - parseFloat(aVal);
                }}

                // Handle fractions like "2/4" (Owned column)
                if (aVal.match(/^\\d+\\/\\d+$/) && bVal.match(/^\\d+\\/\\d+$/)) {{
                    const aNum = parseFloat(aVal.split('/')[0]);
                    const bNum = parseFloat(bVal.split('/')[0]);
                    return direction === 'asc' ? aNum - bNum : bNum - aNum;
                }}

                // Handle plain numbers (Year, Rating, Days)
                const aNum = parseFloat(aVal);
                const bNum = parseFloat(bVal);
                if (!isNaN(aNum) && !isNaN(bNum)) {{
                    return direction === 'asc' ? aNum - bNum : bNum - aNum;
                }}

                // Text comparison (Title, Collection)
                return direction === 'asc'
                    ? aVal.localeCompare(bVal)
                    : bVal.localeCompare(aVal);
            }});

            // Reattach sorted rows
            rows.forEach(row => tbody.appendChild(row));
        }}

        // Initialize sortable headers
        document.querySelectorAll('th.sortable').forEach(th => {{
            th.addEventListener('click', function() {{
                const colIndex = Array.from(this.parentNode.children).indexOf(this);
                sortTable(this, colIndex);
            }});
        }});

        // Initialize counts on load
        updateCounts();

        // Streaming dropdown toggle
        function toggleStreamingDropdown() {{
            const menu = document.getElementById('streaming-menu');
            menu.classList.toggle('show');
        }}

        // Close dropdown when clicking outside
        document.addEventListener('click', function(e) {{
            const dropdown = document.getElementById('streaming-dropdown');
            if (!dropdown.contains(e.target)) {{
                document.getElementById('streaming-menu').classList.remove('show');
            }}
        }});

        // Update dropdown button text based on selections
        function updateStreamingButtonText() {{
            const checkboxes = document.querySelectorAll('#streaming-menu input[type="checkbox"]:checked');
            const button = document.querySelector('.dropdown-toggle');
            if (checkboxes.length === 0) {{
                button.innerHTML = 'All Services <span class="arrow">&#9662;</span>';
            }} else if (checkboxes.length === 1) {{
                const label = checkboxes[0].parentNode.textContent.trim();
                button.innerHTML = label + ' <span class="arrow">&#9662;</span>';
            }} else {{
                button.innerHTML = checkboxes.length + ' selected <span class="arrow">&#9662;</span>';
            }}
        }}

        // Main filter function
        function applyFilters() {{
            const searchTerm = document.getElementById('filter-search').value.toLowerCase().trim();
            const ratingMin = parseFloat(document.getElementById('filter-rating-min').value) || 0;
            const yearMin = parseInt(document.getElementById('filter-year-min').value) || 0;
            const yearMax = parseInt(document.getElementById('filter-year-max').value) || 9999;
            const daysMax = parseInt(document.getElementById('filter-days-max').value) || Infinity;

            // Get selected streaming services
            const streamingCheckboxes = document.querySelectorAll('#streaming-menu input[type="checkbox"]:checked');
            const selectedServices = Array.from(streamingCheckboxes).map(cb => cb.value);

            updateStreamingButtonText();

            // Filter rows in all tables
            document.querySelectorAll('tbody tr').forEach(row => {{
                let show = true;

                // Get cell values - handle different table structures
                const cells = row.querySelectorAll('td');
                if (cells.length < 3) return; // Skip malformed rows

                // Title is in cell 1 (after checkbox)
                const titleCell = cells[1];
                const title = titleCell?.textContent?.toLowerCase() || '';

                // Find year, rating, days cells - they contain just numbers
                let year = 0, rating = 0, days = 0;
                let streamingCell = null;

                cells.forEach((cell, idx) => {{
                    if (idx === 0) return; // Skip checkbox
                    const text = cell.textContent.trim();

                    // Check if it's a streaming icons cell
                    if (cell.querySelector('.streaming-icons') || cell.querySelector('.streaming-icon')) {{
                        streamingCell = cell;
                        return;
                    }}

                    // Year: 4-digit number between 1900-2030
                    if (/^\\d{{4}}$/.test(text) && parseInt(text) >= 1900 && parseInt(text) <= 2030) {{
                        year = parseInt(text);
                    }}
                    // Rating: decimal between 0-10
                    else if (/^\\d+\\.\\d$/.test(text) && parseFloat(text) <= 10) {{
                        rating = parseFloat(text);
                    }}
                    // Days: plain integer (last numeric column)
                    else if (/^\\d+$/.test(text) && parseInt(text) < 10000) {{
                        days = parseInt(text);
                    }}
                }});

                // Search filter
                if (searchTerm && !title.includes(searchTerm)) {{
                    show = false;
                }}

                // Rating filter
                if (rating < ratingMin) {{
                    show = false;
                }}

                // Year filter
                if (year > 0 && (year < yearMin || year > yearMax)) {{
                    show = false;
                }}

                // Days filter
                if (days > daysMax) {{
                    show = false;
                }}

                // Streaming service filter
                if (selectedServices.length > 0 && streamingCell) {{
                    const icons = streamingCell.querySelectorAll('.streaming-icon');
                    let hasMatch = false;

                    icons.forEach(icon => {{
                        const classes = Array.from(icon.classList);
                        // Check for "user-service" class match
                        if (selectedServices.includes('user-service') && classes.includes('user-service')) {{
                            hasMatch = true;
                        }}
                        // Check for specific service match
                        selectedServices.forEach(service => {{
                            if (service !== 'user-service' && classes.includes(service)) {{
                                hasMatch = true;
                            }}
                        }});
                    }});

                    if (!hasMatch) {{
                        show = false;
                    }}
                }}

                // Apply visibility
                row.classList.toggle('filtered-out', !show);
            }});

            updateCounts();
        }}

        // Clear all filters
        function clearFilters() {{
            document.getElementById('filter-search').value = '';
            document.getElementById('filter-rating-min').value = '';
            document.getElementById('filter-year-min').value = '';
            document.getElementById('filter-year-max').value = '';
            document.getElementById('filter-days-max').value = '';

            document.querySelectorAll('#streaming-menu input[type="checkbox"]').forEach(cb => {{
                cb.checked = false;
            }});

            updateStreamingButtonText();
            applyFilters();
        }}
    </script>
</body>
</html>'''
