"""
Output generation for external recommendations.
Generates markdown watchlists and combined HTML views.
"""

import os
from datetime import datetime
from typing import Dict, List

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
    get_imdb_id_func
) -> str:
    """
    Generate single HTML watchlist with tabs for all users.
    Users can switch between tabs, select items, and export to Radarr/Sonarr/Trakt.

    Args:
        all_users_data: List of user data dicts with movies_categorized and shows_categorized
        output_dir: Directory to write HTML file
        tmdb_api_key: TMDB API key for fetching IMDB IDs
        get_imdb_id_func: Function to fetch IMDB ID from TMDB ID

    Returns:
        Path to the generated HTML file
    """
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'watchlist.html')

    now = datetime.now()

    # Collect all IMDB IDs across all users (to avoid duplicate API calls)
    print("  Fetching IMDB IDs for export...")
    all_imdb_ids = {}  # tmdb_id -> imdb_id

    def collect_imdb_ids_from_categorized(categorized, media_type):
        """Helper to collect IMDB IDs from categorized items."""
        # Flatten all items from all categories
        items = []
        for service_items in categorized.get('user_services', {}).values():
            items.extend(service_items)
        for service_items in categorized.get('other_services', {}).values():
            items.extend(service_items)
        items.extend(categorized.get('acquire', []))

        # Fetch IMDB IDs for items not already cached
        for item in items:
            tmdb_id = item.get('tmdb_id')
            if tmdb_id and tmdb_id not in all_imdb_ids:
                imdb_id = get_imdb_id_func(tmdb_api_key, tmdb_id, media_type)
                if imdb_id:
                    all_imdb_ids[tmdb_id] = imdb_id

    for user_data in all_users_data:
        collect_imdb_ids_from_categorized(user_data['movies_categorized'], 'movie')
        collect_imdb_ids_from_categorized(user_data['shows_categorized'], 'tv')

    def render_table(items, media_type, user_id):
        """Render HTML table for items with checkboxes (unchecked by default)"""
        rows = []
        for item in items:
            tmdb_id = item.get('tmdb_id', '')
            imdb_id = all_imdb_ids.get(tmdb_id, '')
            days_listed = (now - datetime.fromisoformat(item['added_date'])).days
            rows.append(f'''
                <tr data-tmdb="{tmdb_id}" data-imdb="{imdb_id}" data-type="{media_type}" data-user="{user_id}">
                    <td><input type="checkbox" class="select-item"></td>
                    <td>{item['title']}</td>
                    <td>{item['year']}</td>
                    <td>{item['rating']:.1f}</td>
                    <td>{item['score']:.0%}</td>
                    <td>{days_listed}</td>
                </tr>''')
        return '\n'.join(rows)

    def render_service_section(service, items, media_type, user_id):
        """Render a service section with table"""
        service_display = SERVICE_DISPLAY_NAMES.get(service, service.title())
        return f'''
            <h4>{service_display} ({len(items)} {media_type}s)</h4>
            <table>
                <thead>
                    <tr><th><input type="checkbox" class="select-all-table"></th><th>Title</th><th>Year</th><th>Rating</th><th>Score</th><th>Days</th></tr>
                </thead>
                <tbody>
                    {render_table(items, media_type, user_id)}
                </tbody>
            </table>'''

    # Build tabs HTML
    tabs_html = ""
    panels_html = ""

    for i, user_data in enumerate(all_users_data):
        display_name = user_data['display_name']
        user_id = user_data['username'].lower().replace(' ', '_')
        movies_cat = user_data['movies_categorized']
        shows_cat = user_data['shows_categorized']
        is_active = "active" if i == 0 else ""

        # Tab button
        tabs_html += f'<button class="tab-btn {is_active}" data-user="{user_id}">{display_name}</button>\n'

        # Panel content
        panel_content = ""

        # Movies section
        if any([movies_cat['user_services'], movies_cat['other_services'], movies_cat['acquire']]):
            panel_content += "<h2>Movies to Watch</h2>"

            if movies_cat['user_services']:
                panel_content += "<h3>Available on Your Services</h3>"
                for service, items in sorted(movies_cat['user_services'].items(), key=lambda x: -len(x[1])):
                    panel_content += render_service_section(service, items, 'movie', user_id)

            if movies_cat['other_services']:
                panel_content += "<h3>Available on Other Services</h3>"
                for service, items in sorted(movies_cat['other_services'].items(), key=lambda x: -len(x[1])):
                    panel_content += render_service_section(service, items, 'movie', user_id)

            if movies_cat['acquire']:
                panel_content += f"<h3>Need to Acquire ({len(movies_cat['acquire'])} movies)</h3>"
                panel_content += f'''
                    <table>
                        <thead>
                            <tr><th><input type="checkbox" class="select-all-table"></th><th>Title</th><th>Year</th><th>Rating</th><th>Score</th><th>Days</th></tr>
                        </thead>
                        <tbody>
                            {render_table(movies_cat['acquire'], 'movie', user_id)}
                        </tbody>
                    </table>'''

        # Shows section
        if any([shows_cat['user_services'], shows_cat['other_services'], shows_cat['acquire']]):
            panel_content += "<h2>TV Shows to Watch</h2>"

            if shows_cat['user_services']:
                panel_content += "<h3>Available on Your Services</h3>"
                for service, items in sorted(shows_cat['user_services'].items(), key=lambda x: -len(x[1])):
                    panel_content += render_service_section(service, items, 'show', user_id)

            if shows_cat['other_services']:
                panel_content += "<h3>Available on Other Services</h3>"
                for service, items in sorted(shows_cat['other_services'].items(), key=lambda x: -len(x[1])):
                    panel_content += render_service_section(service, items, 'show', user_id)

            if shows_cat['acquire']:
                panel_content += f"<h3>Need to Acquire ({len(shows_cat['acquire'])} shows)</h3>"
                panel_content += f'''
                    <table>
                        <thead>
                            <tr><th><input type="checkbox" class="select-all-table"></th><th>Title</th><th>Year</th><th>Rating</th><th>Score</th><th>Days</th></tr>
                        </thead>
                        <tbody>
                            {render_table(shows_cat['acquire'], 'show', user_id)}
                        </tbody>
                    </table>'''

        if not panel_content:
            panel_content = "<p>No recommendations available for this user.</p>"

        panels_html += f'<div class="tab-panel {is_active}" data-user="{user_id}">{panel_content}</div>\n'

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
    <title>Plex Watchlist</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: linear-gradient(135deg, #0d0d0d 0%, #1a1a1a 50%, #0d0d0d 100%);
            color: #e8e8e8;
            min-height: 100vh;
        }}
        h1 {{
            color: #d4af37;
            margin-bottom: 5px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            font-size: 2em;
        }}
        h2 {{
            background: linear-gradient(90deg, #8b0000 0%, #660000 100%);
            color: #d4af37;
            padding: 12px 15px;
            border-radius: 3px;
            margin-top: 30px;
            border-left: 4px solid #d4af37;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-size: 1.1em;
        }}
        h3 {{
            background: #1c1c1c;
            color: #c0c0c0;
            padding: 10px 12px;
            border-radius: 3px;
            border-left: 3px solid #8b0000;
            font-size: 0.95em;
        }}
        h4 {{
            color: #d4af37;
            margin: 15px 0 8px 0;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
            margin-bottom: 25px;
            padding-bottom: 20px;
            border-bottom: 1px solid #333;
        }}
        .export-buttons {{
            display: flex;
            gap: 10px;
        }}
        .export-btn {{
            background: linear-gradient(180deg, #8b0000 0%, #5c0000 100%);
            color: #d4af37;
            border: 1px solid #d4af37;
            padding: 12px 24px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 13px;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s ease;
        }}
        .export-btn:hover {{
            background: linear-gradient(180deg, #a00000 0%, #700000 100%);
            box-shadow: 0 0 10px rgba(212, 175, 55, 0.3);
        }}
        .export-btn.sonarr {{
            background: linear-gradient(180deg, #2a2a2a 0%, #1a1a1a 100%);
        }}
        .export-btn.sonarr:hover {{
            background: linear-gradient(180deg, #3a3a3a 0%, #2a2a2a 100%);
        }}
        .export-btn.trakt {{
            background: linear-gradient(180deg, #ed1c24 0%, #b71c1c 100%);
            color: #fff;
            border-color: #ed1c24;
        }}
        .export-btn.trakt:hover {{
            background: linear-gradient(180deg, #ff3333 0%, #ed1c24 100%);
            box-shadow: 0 0 10px rgba(237, 28, 36, 0.4);
        }}
        .tabs {{
            display: flex;
            gap: 3px;
            margin-bottom: 25px;
            flex-wrap: wrap;
            background: #111;
            padding: 5px;
            border-radius: 5px;
        }}
        .tab-btn {{
            background: #1a1a1a;
            color: #888;
            border: none;
            padding: 12px 24px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 13px;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s ease;
        }}
        .tab-btn:hover {{
            background: #2a2a2a;
            color: #c0c0c0;
        }}
        .tab-btn.active {{
            background: linear-gradient(180deg, #8b0000 0%, #5c0000 100%);
            color: #d4af37;
        }}
        .tab-panel {{ display: none; }}
        .tab-panel.active {{ display: block; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 10px 0 25px 0;
            background: #141414;
            border-radius: 3px;
            overflow: hidden;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3);
        }}
        th, td {{
            padding: 12px 10px;
            text-align: left;
            border-bottom: 1px solid #2a2a2a;
        }}
        th {{
            background: #1c1c1c;
            color: #d4af37;
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        tr:hover {{ background: #1f1f1f; }}
        td:first-child, th:first-child {{ width: 40px; text-align: center; }}
        input[type="checkbox"] {{
            width: 16px;
            height: 16px;
            cursor: pointer;
            accent-color: #8b0000;
        }}
        .timestamp {{ color: #666; font-size: 12px; }}
        .instructions {{
            background: #141414;
            padding: 20px;
            border-radius: 3px;
            margin-top: 40px;
            border: 1px solid #2a2a2a;
        }}
        .instructions h3 {{
            background: none;
            border: none;
            padding: 0;
            color: #d4af37;
            margin-bottom: 15px;
        }}
        .instructions ul {{
            margin: 0;
            padding-left: 20px;
            color: #999;
        }}
        .instructions li {{ margin-bottom: 8px; }}
        .instructions strong {{ color: #c0c0c0; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>Plex Watchlist</h1>
            <p class="timestamp">Last updated: {now.strftime('%Y-%m-%d %H:%M')}</p>
        </div>
        <div class="export-buttons">
            <button class="export-btn" onclick="exportRadarr()">Export to Radarr (<span id="movie-count">0</span>)</button>
            <button class="export-btn sonarr" onclick="exportSonarr()">Export to Sonarr (<span id="show-count">0</span>)</button>
            <button class="export-btn trakt" onclick="exportTrakt()">Export for Trakt (<span id="total-count">0</span>)</button>
        </div>
    </div>

    <div class="tabs">
        {tabs_html}
    </div>

    {panels_html}

    <div class="instructions">
        <h3>How to Use</h3>
        <ul>
            <li>Click a user tab to view their recommendations</li>
            <li>Check the items you want to export</li>
            <li><strong>Radarr:</strong> Download IMDB IDs for selected movies → import via Lists</li>
            <li><strong>Sonarr:</strong> Download IMDB IDs for selected shows → import via Lists</li>
            <li><strong>Trakt:</strong> Download IMDB IDs for all selected → paste into Trakt list</li>
            <li>Exports include selections from ALL users, not just the active tab</li>
        </ul>
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

        // Initialize counts on load
        updateCounts();
    </script>
</body>
</html>'''
