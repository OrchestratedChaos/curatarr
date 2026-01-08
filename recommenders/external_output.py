"""
Output generation for external recommendations.
Generates markdown watchlists and combined HTML views.
"""

import os
from datetime import datetime
from typing import Dict, List

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


def render_streaming_icons(services: List[str], user_services: List[str]) -> str:
    """
    Render HTML streaming service icons/badges.
    User's services get a gold border highlight.
    """
    if not services:
        return '<span class="streaming-icon acquire">Acquire</span>'

    icons = []
    for service in services:
        short_name = SERVICE_SHORT_NAMES.get(service, service.title())
        css_class = f"streaming-icon {service}"
        if service in user_services:
            css_class += " user-service"
        icons.append(f'<span class="{css_class}">{short_name}</span>')

    return ' '.join(icons)


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
    missing_sequels: List[Dict] = None
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
        missing_sequels: List of missing sequel items (shared across users)

    Returns:
        Path to the generated HTML file
    """
    movie_counts = movie_counts or {}
    show_counts = show_counts or {}
    missing_sequels = missing_sequels or []
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'watchlist.html')

    now = datetime.now()

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
            if tmdb_id and tmdb_id not in all_imdb_ids and (tmdb_id, media_type) not in [(p[0], p[1]) for p in pending_lookups]:
                pending_lookups.append((tmdb_id, media_type))

    for user_data in all_users_data:
        collect_tmdb_ids_from_categorized(user_data['movies_categorized'], 'movie')
        collect_tmdb_ids_from_categorized(user_data['shows_categorized'], 'tv')

    # Also collect from missing sequels
    for item in missing_sequels:
        tmdb_id = item.get('tmdb_id')
        if tmdb_id and tmdb_id not in all_imdb_ids and (tmdb_id, 'movie') not in [(p[0], p[1]) for p in pending_lookups]:
            pending_lookups.append((tmdb_id, 'movie'))

    # Fetch IMDB IDs with progress
    total_lookups = len(pending_lookups)
    if total_lookups > 0:
        print(f"  {CYAN}Fetching IMDB IDs for export ({total_lookups} items)...{RESET}")
        for i, (tmdb_id, media_type) in enumerate(pending_lookups, 1):
            if i % 10 == 0 or i == total_lookups:
                print(f"\r    {CYAN}Progress: {i}/{total_lookups}{RESET}", end="", flush=True)
            imdb_id = get_imdb_id_func(tmdb_api_key, tmdb_id, media_type)
            if imdb_id:
                all_imdb_ids[tmdb_id] = imdb_id
        print(f"\r    {GREEN}Fetched {len(all_imdb_ids)} IMDB IDs{RESET}          ")
    else:
        print(f"  {GREEN}No IMDB lookups needed{RESET}")

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
            # Render streaming icons
            streaming_services = item.get('streaming_services', [])
            streaming_html = render_streaming_icons(streaming_services, user_services)
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
        """Render HTML table for missing sequels"""
        rows = []
        for item in items:
            tmdb_id = item.get('tmdb_id', '')
            imdb_id = all_imdb_ids.get(tmdb_id, '')
            collection_name = item.get('collection_name', 'Unknown')
            owned = item.get('owned_count', 0)
            total = item.get('total_count', 0)
            streaming_services = item.get('streaming_services', [])
            streaming_html = render_streaming_icons(streaming_services, user_services)
            # Add TV Special badge if this is a TV movie
            tv_badge = '<span class="tv-special-badge">TV Special</span>' if item.get('is_tv_movie') else ''
            rows.append(f'''
                <tr data-tmdb="{tmdb_id}" data-imdb="{imdb_id}" data-type="movie" data-user="huntarr">
                    <td><input type="checkbox" class="select-item"></td>
                    <td>{item['title']} {tv_badge}</td>
                    <td>{item.get('year', '')}</td>
                    <td>{collection_name}</td>
                    <td>{owned}/{total}</td>
                    <td><div class="streaming-icons">{streaming_html}</div></td>
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

    # Add Missing Sequels tab if there are any
    if missing_sequels:
        # Get user_services from first user for highlighting (shared tab)
        first_user_services = all_users_data[0].get('user_services', []) if all_users_data else []

        tabs_html += f'<button class="tab-btn" data-user="huntarr">Huntarr</button>\n'
        sequels_content = f"<h2>Huntarr ({len(missing_sequels)})</h2>"
        sequels_content += "<p class=\"subtitle\">Hunt down missing movies from collections you've started.</p>"
        sequels_content += f'''
            <table>
                <thead>
                    <tr><th><input type="checkbox" class="select-all-table"></th><th class="sortable">Title</th><th class="sortable">Year</th><th class="sortable">Collection</th><th class="sortable">Owned</th><th class="sortable">Streaming</th></tr>
                </thead>
                <tbody>
                    {render_sequels_table(missing_sequels, first_user_services)}
                </tbody>
            </table>'''
        panels_html += f'<div class="tab-panel" data-user="huntarr">{sequels_content}</div>\n'

    html_content = _generate_html_template(tabs_html, panels_html, now)

    with open(output_file, 'w') as f:
        f.write(html_content)

    return output_file


def _generate_html_template(tabs_html: str, panels_html: str, now: datetime) -> str:
    """Generate the full HTML template with CSS and JavaScript."""
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
            justify-content: center;
            margin-bottom: 35px;
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

        /* Streaming service icons */
        .streaming-icons {{
            display: flex;
            flex-wrap: wrap;
            gap: 3px;
            max-width: 200px;
        }}
        .streaming-icon {{
            display: inline-block;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 9px;
            font-weight: 600;
            white-space: nowrap;
            box-shadow: 0 1px 2px rgba(0,0,0,0.3);
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

        <div class="tabs-wrapper">
            <div class="tabs">
                {tabs_html}
            </div>
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
            // Count ALL selected items across ALL users
            const movieCount = document.querySelectorAll('tr[data-type="movie"] .select-item:checked').length;
            const showCount = document.querySelectorAll('tr[data-type="show"] .select-item:checked').length;
            document.getElementById('movie-count').textContent = movieCount;
            document.getElementById('show-count').textContent = showCount;
            document.getElementById('total-count').textContent = movieCount + showCount;
        }}

        function exportRadarr() {{
            // Export from ALL users
            const rows = document.querySelectorAll('tr[data-type="movie"]');
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
            // Export from ALL users
            const rows = document.querySelectorAll('tr[data-type="show"]');
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
            // Export ALL selected items (movies + shows) for Trakt import
            const allRows = document.querySelectorAll('tr[data-imdb]');
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
    </script>
</body>
</html>'''
