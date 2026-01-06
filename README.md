# Curatarr

**Personalized recommendations for your Plex library. Simple setup. Powerful results.**

Turn your Plex server into a smart recommendation engine. Analyze what you and your users watch, then surface the hidden gems already in your library—plus discover what to add next.

---

## Why This Exists

Your Plex library has thousands of titles. Your users have watched maybe 10% of them. The problem isn't content—it's discovery.

**Curatarr solves this by:**
- Analyzing each user's watch history
- Scoring unwatched content by similarity (keywords, genres, cast, directors)
- Creating personalized collections that update automatically
- Generating external watchlists so you know what to acquire next

---

## Features

### For Your Library (What to Watch)
- **Per-user recommendations** — Each user gets their own curated collection
- **Smart scoring** — Weights keywords, genres, cast, and directors
- **Recency bias** — Recent watches influence recommendations more
- **Rewatch detection** — Content you love gets weighted higher
- **Genre exclusions** — Skip horror for the kids, documentaries for movie night
- **Auto-updating collections** — `🎬 John - Recommendations` appears in Plex

### For Acquisition (What to Get)
- **External watchlists** — Content NOT in your library that users would love
- **Streaming service grouping** — "Available on Netflix" vs "Need to acquire"
- **Sonarr integration** — Push TV recommendations directly to Sonarr
- **Radarr integration** — Push movie recommendations directly to Radarr
- **Auto-cleanup** — Items removed when they appear in your library
- **Genre balancing** — Matches user viewing habits proportionally

### For You (Simple & Robust)
- **One command** — `./run.sh` handles everything
- **Single config file** — All settings in one place
- **Auto-updates** — Pulls latest code from GitHub on each run (optional)
- **Smart caching** — Auto-clears incompatible caches after updates
- **Auto-scheduling** — Optional daily cron job
- **Clean logs** — Know exactly what happened

---

## Quick Start

### macOS / Linux
```bash
git clone https://github.com/OrchestratedChaos/curatarr.git
cd curatarr
./run.sh    # Setup wizard runs on first launch
```

### Windows (PowerShell)
```powershell
git clone https://github.com/OrchestratedChaos/curatarr.git
cd curatarr
.\run.ps1   # Setup wizard runs on first launch
```

### Docker
```bash
git clone https://github.com/OrchestratedChaos/curatarr.git
cd curatarr
cp config/config.example.yml config/config.yml
# Edit config/config.yml with your details
docker compose up --build
```

**Required config:**
- `plex.url` — Your Plex server URL (e.g., `http://192.168.1.100:32400`)
- `plex.token` — [How to find your Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
- `tmdb.api_key` — [Get free TMDB API key](https://www.themoviedb.org/settings/api)
- `users.list` — Your Plex usernames (comma-separated)

First run takes 5-10 minutes to analyze your library. After that, it's fast.

---

## What You Get

### In Plex
Collections automatically appear:
```
🎬 John - Recommendations       (50 movies)
🎬 Sarah - Recommendations      (50 movies)
📺 John - Recommendations       (20 shows)
📺 Sarah - Recommendations      (20 shows)
```

Pin them to your home screen. They update daily.

### External Watchlists
Interactive HTML files with export buttons:
```
recommendations/external/john_watchlist.html

- Select which movies/shows to export
- Click "Export to Radarr" → downloads IMDB IDs for import
- Click "Export to Sonarr" → downloads IMDB IDs for import
- Grouped by streaming service availability
- Auto-opens in browser after run (configurable)
```

Also generates markdown for reference:
```
recommendations/external/john_watchlist.md
```

---

## Configuration

### Minimal Config
```yaml
plex:
  url: http://your-plex-server:32400
  token: YOUR_PLEX_TOKEN
  movie_library: Movies
  tv_library: TV Shows

tmdb:
  api_key: YOUR_TMDB_API_KEY

users:
  list: john, sarah, kids
```

**Get your keys:**
- [TMDB API Key](https://www.themoviedb.org/settings/api) (free account required)
- [Plex Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

### Environment Variables

For Docker or CI environments, you can use environment variables instead of storing tokens in config.yml:

| Variable | Overrides |
|----------|-----------|
| `PLEX_URL` | `plex.url` |
| `PLEX_TOKEN` | `plex.token` |
| `TMDB_API_KEY` | `tmdb.api_key` |

Environment variables take precedence over config file values.

### Per-User Preferences
```yaml
users:
  list: john, sarah, kids
  preferences:
    john:
      display_name: John
      streaming_services: [netflix, hulu, disney_plus]
    sarah:
      display_name: Sarah
      exclude_genres: [horror]
    kids:
      display_name: Kids
      exclude_genres: [horror, thriller, war]
```

### General Settings
```yaml
general:
  auto_update: true           # Pull latest code from GitHub on run
  log_retention_days: 7       # Keep logs for 7 days
```

### Tuning (Optional)
```yaml
movies:
  limit_results: 50           # Recommendations per user
  quality_filters:
    min_rating: 5.0           # TMDB rating threshold
    min_vote_count: 50        # Minimum votes

recency_decay:
  enabled: true
  days_0_30: 1.0              # Recent watches: full weight
  days_31_90: 0.75            # 1-3 months: 75%
  days_91_180: 0.50           # 3-6 months: 50%

collections:
  stale_removal_days: 7       # Rotate unwatched recommendations

external_recommendations:
  min_relevance_score: 0.25   # See note below
  auto_open_html: false       # Open HTML watchlist in browser after run
```

### Sonarr Integration (Optional)

Push your external TV recommendations directly to Sonarr:

```yaml
# config/sonarr.yml
enabled: true
url: http://localhost:8989
api_key: YOUR_SONARR_API_KEY

# Sync behavior
auto_sync: true             # Auto-add when external recs finish
user_mode: mapping          # mapping, per_user, or combined
plex_users: [john]          # Which users to sync (for mapping mode)

# Import settings
root_folder: /tv            # Where to store shows
quality_profile: HD-1080p   # Quality profile name
tag: Curatarr               # Tag for easy cleanup

# Safe defaults (shows just get added, no downloads)
monitor: false              # Don't monitor for new episodes
search_missing: false       # Don't search for episodes
```

**Setup:** Run `./run.sh` and follow Step 7, or manually create `config/sonarr.yml`.

**User modes:**
- `mapping` — Only sync users listed in `plex_users`
- `per_user` — Sync all users separately
- `combined` — Merge everyone's recommendations

### Radarr Integration (Optional)

Push your external movie recommendations directly to Radarr:

```yaml
# config/radarr.yml
enabled: true
url: http://localhost:7878
api_key: YOUR_RADARR_API_KEY

# Sync behavior
auto_sync: true             # Auto-add when external recs finish
user_mode: mapping          # mapping, per_user, or combined
plex_users: [john]          # Which users to sync (for mapping mode)

# Import settings
root_folder: /movies        # Where to store movies
quality_profile: HD-1080p   # Quality profile name
tag: Curatarr               # Tag for easy cleanup

# Safe defaults (movies just get added, no downloads)
monitor: false              # Don't monitor for downloads
search_for_movie: false     # Don't search for movie
```

**Setup:** Run `./run.sh` and follow Step 8, or manually create `config/radarr.yml`.

### MDBList Integration (Optional)

Export recommendations to MDBList for use with Kometa/PMM and other tools:

```yaml
# config/mdblist.yml
enabled: true
api_key: YOUR_MDBLIST_API_KEY

# Sync behavior
auto_sync: true             # Auto-export when external recs finish
user_mode: mapping          # mapping, per_user, or combined
plex_users: [john]          # Which users to sync (for mapping mode)

# List settings
list_prefix: Curatarr       # Lists named "Curatarr Movies", "Curatarr TV"
replace_existing: true      # Clear list before adding (vs. append)
```

**Setup:** Run `./run.sh` and follow Step 9, or manually create `config/mdblist.yml`.

### Simkl Integration (Optional)

Full integration with Simkl for anime/TV/movie tracking with excellent anime database:

```yaml
# config/simkl.yml
enabled: true
client_id: YOUR_SIMKL_CLIENT_ID
access_token: (filled by setup wizard)

# Import watch history (great for anime from Crunchyroll, etc.)
import:
  enabled: true
  include_anime: true

# Discovery from Simkl trending/popular
discovery:
  enabled: true
  anime_focus: true        # Prioritize anime discovery

# Export recommendations to Simkl watchlist
export:
  enabled: true
  auto_sync: true
  user_mode: mapping
  plex_users: [your_username]
```

**Setup:** Run `./run.sh` and follow Step 10, or manually create `config/simkl.yml`.

### External Recommendations: Relevance Score

The `min_relevance_score` setting (0.0-1.0) controls how strictly personal the external watchlist recommendations are:

- **Score** = How many of your watched items recommend this title (normalized 0-100%)
- **Rating** = TMDB audience rating (used only as tiebreaker)

**How it works:**
1. Items above the threshold are prioritized (sorted by personal relevance)
2. Lower-scored items only appear if not enough high-relevance items exist
3. This ensures you get personally relevant content, not just popular movies

**Tuning:**
- `0.25` (default) — Balanced. Most users should start here.
- `0.50` — Stricter. Only highly relevant recommendations.
- `0.10` — Looser. More variety, but less personalized.

If you're seeing too many "random" recommendations, increase this value.

---

## How It Works

1. **Fetch watch history** — Pulls each user's watched content from Plex
2. **Build preference profile** — Counts genres, directors, actors, keywords watched
3. **Score unwatched content** — Calculates similarity to user's taste
4. **Apply filters** — Excludes genres, enforces quality thresholds
5. **Create collections** — Labels content in Plex, collections auto-populate
6. **Generate watchlists** — External recommendations grouped by streaming service

### Similarity Scoring
```
Score = (keyword_match × 0.50) +    # Most specific signal - themes, topics
        (genre_match × 0.25) +       # Baseline preference
        (actor_match × 0.20) +       # Cast preferences
        (director_match × 0.05)      # Style indicator (most don't pick by director)
```

**Scoring uses sum with diminishing returns** — Multiple weak matches add up rather than averaging down. A movie with 15 matching keywords scores well even if each individual match is partial.

Weighted by recency (recent watches count more), user ratings (5-star content counts more), and rewatch count (loved content counts more).

**Weight redistribution:** If a movie's component has no matches (e.g., unknown director), that weight redistributes proportionally to components that did match—so you still get meaningful scores.

---

## Project Structure

```
curatarr/
├── recommenders/
│   ├── movie.py             # Movie recommendations
│   ├── tv.py                # TV show recommendations
│   ├── external.py          # External watchlist generator
│   └── base.py              # Shared base classes
├── utils/                   # Shared utilities (11 modules, incl. sonarr.py)
├── tests/                   # Unit tests
├── config.yml               # Your configuration
├── run.sh                   # Main entry point (macOS/Linux)
├── run.ps1                  # Main entry point (Windows)
├── Dockerfile               # Docker image definition
├── docker-compose.yml       # Docker Compose config
├── cache/                   # TMDB metadata cache
├── logs/                    # Execution logs
└── recommendations/
    └── external/            # Generated watchlists
```

---

## Scheduling

First run prompts for automatic scheduling. Or add manually:

### macOS / Linux (cron)
```bash
# Daily at 3 AM
0 3 * * * cd /path/to/curatarr && ./run.sh >> logs/daily-run.log 2>&1
```

### Windows (Task Scheduler)
The PowerShell script offers to create a scheduled task automatically. Or manually:
1. Open Task Scheduler
2. Create Basic Task → "Curatarr"
3. Trigger: Daily at 3:00 AM
4. Action: Start a program
   - Program: `powershell.exe`
   - Arguments: `-ExecutionPolicy Bypass -File "C:\path\to\curatarr\run.ps1"`

### Docker (cron on host)
```bash
# Daily at 3 AM
0 3 * * * cd /path/to/curatarr && docker compose run --rm curatarr >> logs/daily-run.log 2>&1
```

---

## FAQ

**Q: Do I need Plex Pass?**
No. Works with free Plex.

**Q: Will this modify my media files?**
No. Only adds labels to Plex metadata.

**Q: How many watched items needed?**
At least 5 for meaningful recommendations.

**Q: Can users see each other's recommendations?**
Collections are visible to all, but each is personalized and clearly labeled.

**Q: What about new users with no history?**
They're skipped until they have enough watch history.

---

## Troubleshooting

### macOS / Linux
```bash
# Check logs
tail -100 logs/daily-run.log

# Run with debug output
./run.sh --debug

# Verify config
python3 -c "import yaml; print(yaml.safe_load(open('config.yml')))"
```

### Windows (PowerShell)
```powershell
# Check logs
Get-Content logs/daily-run.log -Tail 100

# Run with debug output
.\run.ps1 -Debug

# Verify config
python -c "import yaml; print(yaml.safe_load(open('config.yml')))"
```

**Common issues:**
- TMDB API key invalid → Get free key from themoviedb.org
- Plex connection failed → Check URL and token
- No recommendations → User needs more watch history
- "Cache outdated" message → Normal after updates, rebuilds automatically
- Want to disable auto-update → Set `general.auto_update: false` in config.yml

### Docker
```bash
# View logs
docker compose logs

# Rebuild after code changes
docker compose build --no-cache

# Check container status
docker compose ps
```

**Docker-specific issues:**
- Connection refused to Plex → Use host IP (not `localhost`), try `host.docker.internal` on Docker Desktop
- Permission denied on cache/logs → Run `chmod -R 777 cache logs recommendations` on host

**Manual update (if auto-update disabled):**
```bash
git pull origin main
```

---

## Feature Requests

Have an idea for Curatarr? We track feature requests as GitHub Issues and **your vote matters!**

### How to Vote

1. Browse [open enhancement requests](https://github.com/OrchestratedChaos/curatarr/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement)
2. Find a feature you want
3. **Click the 👍 reaction** on the issue (top of the issue, next to the title)
4. That's it! Issues with more votes get prioritized

### How to Request a Feature

1. [Search existing issues](https://github.com/OrchestratedChaos/curatarr/issues) to avoid duplicates
2. [Open a new issue](https://github.com/OrchestratedChaos/curatarr/issues/new) with the `enhancement` label
3. Describe what you want and why it would be useful

---

## Credits

Inspired by [netplexflix's](https://github.com/netplexflix) Movie/TV Recommendations for Plex. This project takes the core concept of TMDB-based similarity scoring and rebuilds it with:

- Simplified architecture (4 files vs complex nested structure)
- Multi-user support with per-user preferences
- External watchlists with streaming service grouping
- Automated collection management
- Single unified configuration

---

## License

MIT License. Use it, fork it, make it yours.

---

**Take your Plex to the next level.**
