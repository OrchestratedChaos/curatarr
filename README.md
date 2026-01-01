# Plex Recommendation System

Extended fork of [netplexflix's](https://github.com/netplexflix) excellent Movie/TV Recommendations for Plex, enhanced with multi-user support, per-user preferences, and smart collections automation.

## Features

**Core Algorithm (from netplexflix):**
- TMDB-based similarity scoring (genre, cast, director, keywords, language)
- Watch history analysis with recency weighting
- Quality filters (rating, vote count)
- Radarr/Sonarr integration (disabled by default)
- Trakt integration (available but not used - we use TMDB-based external watchlists instead)

**Enhancements (this fork):**
- **Multi-User Support**: Separate recommendations for each Plex user
- **Per-User Preferences**: Individual genre exclusions, display names, streaming services
- **Smart Collections**: Auto-updating Plex collections (🎬/📺 Recommended - {User})
- **External Watchlists**: Streaming-service-grouped shopping lists with genre balancing
- **Flexible Account Matching**: Handles username variations
- **Cron Scheduling**: Automated daily updates
- **Centralized Utilities**: Shared code for cleaner organization

---

## Differences from Upstream (netplexflix)

**What we changed:**
- ✅ Unified config for movies+TV (upstream: [two separate repos](https://github.com/netplexflix/Movie-Recommendations-for-Plex), each with own config)
- ✅ Per-user preferences - genre exclusions, display names (upstream: multi-user support but no per-user settings)
- ✅ TMDB-based external watchlists with genre balancing (upstream: Trakt-based external recommendations)
- ✅ Automated smart collection creation (upstream: manual label-based workflow - you create collections yourself)
- ✅ One-command setup with `run.sh` (upstream: multi-step - pip install, rename config, manual run)
- ✅ Time-based recommendation rotation - removes items older than 7 days (upstream: randomization from top 10% for variety)
- ✅ Time-based log retention - keep last N days (upstream: file-count based - keep last N files)

**What we kept:**
- ✅ Core TMDB similarity scoring algorithm (genre, cast, director, keywords)
- ✅ Watch history analysis with recency weighting
- ✅ Quality filters and rating multipliers
- ✅ Radarr/Sonarr integration (available but disabled by default)

**What we disabled:**
- ❌ Trakt integration (configured but not used - `plex_only: true`)
- ❌ Radarr/Sonarr auto-download (available in config but `enabled: false`)

---

## Quick Overview

**Collections Created:**
- 🎬 Recommended - User1 (Movies)
- 🎬 Recommended - User2 (Movies)
- 🎬 Recommended - User3 (Movies)
- 🎬 Recommended - User4 (Movies)
- 🎬 Recommended - User5 (Movies)
- 🎬 Recommended - User6 (Movies)
- 📺 Recommended - [each user] (TV Shows)

**Current Users:**
- user1 (User1) - excludes horror
- user2 (User2)
- user3 (User3)
- user4 (User4)
- user5 (User5)
- user6 (User6)

---

## Prerequisites

- **Python 3.8+** - for netplexflix scripts
- **TMDB API Key** - free at https://www.themoviedb.org/settings/api
- **Plex Media Server** - with Movies and TV Shows libraries
- **Git** - to clone netplexflix repositories

Check versions:
```bash
python3 --version  # Should be 3.8+
git --version      # Any recent version
```

---

## Installation

### Quick Start (3 Steps)

1. **Clone this repository:**
```bash
git clone <your-repo-url>
cd plex-recommender
```

2. **Edit `config.yml`:**
   - Set `tmdb.api_key` (get free key from https://www.themoviedb.org/settings/api)
   - Set `plex.url` and `plex.token` (see: https://support.plex.tv/articles/204059436)
   - Update `users.list` with your Plex usernames
   - Customize `users.preferences` for display names and genre exclusions

3. **Run the script:**
```bash
chmod +x run.sh
./run.sh
```

That's it! The script will:
- ✓ Check/install all dependencies automatically
- ✓ Run recommendations for all users
- ✓ Create smart collections in Plex
- ✓ Optionally set up daily cron job

### Configuration Example

```yaml
# config.yml (single file, all settings)
plex:
  url: YOUR_PLEX_URL
  token: YOUR_PLEX_TOKEN
  movie_library: Movies
  tv_library: TV Shows

tmdb:
  api_key: YOUR_TMDB_API_KEY

users:
  list: user1, user2, user3
  preferences:
    user1:
      display_name: User1
      exclude_genres: [horror]
    user2:
      display_name: User2
```

---

## Usage

### Run Recommendations

```bash
./run.sh
```

That's it! The script handles everything:
- Checks dependencies
- Runs movie & TV recommendations
- Creates/updates smart collections
- First run takes 5-10 minutes to analyze watch history

### View Logs

```bash
# Main log
tail -n 50 logs/daily-run.log

# Per-user detailed logs
ls scripts/Movie-Recommendations-for-Plex/Logs/
ls scripts/TV-Show-Recommendations-for-Plex/Logs/
```

---

## Scheduling (Automated Daily Runs)

On first run, the script will ask if you want to set up automatic daily updates.

**Option 1:** Let the script set it up for you (recommended)
- Automatically runs daily at 3 AM
- No manual cron configuration needed

**Option 2:** Set up manually
```bash
crontab -e
# Add: 0 3 * * * cd /path/to/plex-recommender && ./run.sh >> logs/daily-run.log 2>&1
```

**Modify Schedule:**
- Twice daily: `0 3,15 * * *` (3 AM and 3 PM)
- Weekly: `0 3 * * 0` (Sundays at 3 AM)
- Weekdays only: `0 3 * * 1-5` (Mon-Fri at 3 AM)

Test cron expressions: https://crontab.guru

---

## Configuration

### Smart Recommendation Rotation (7-Day Default)

The system uses **intelligent rotation** to keep recommendations fresh:

**How it works:**
- ✅ **Watched items**: Removed immediately from recommendations
- ✅ **Stale items**: Unwatched recommendations older than 7 days are removed
- ✅ **Fresh items**: Recent recommendations (< 7 days) are kept
- ✅ **Auto-fill**: New high-scoring items fill available slots

**Why this matters:**
- Prevents stale recommendations from sitting in your list for weeks/months
- Adapts to changing tastes (holidays, moods, new releases)
- If you haven't watched it in 7 days, you probably won't - system rotates it out
- Truly great matches will score high again and return

**Customize the rotation period:**
```yaml
collections:
  stale_removal_days: 7  # Change to 14, 30, etc. if 7 is too aggressive
```

**Common settings:**
- `7` = Weekly rotation (keeps recs very fresh, adapts quickly to taste changes)
- `14` = Bi-weekly rotation (balanced)
- `30` = Monthly rotation (conservative, gives more time to browse)

---

### External Watchlists (Acquisition Shopping Lists)

In addition to recommendations from your library, the system generates **external watchlists** - per-user markdown files listing content NOT in your library that you'd probably like.

**Features:**
- **Streaming service grouping**: Organizes by what's on your services, other services, or needs acquisition
- **Auto-removes acquired items**: Checks daily - if an item appears in your Plex library, it's removed from the watchlist
- **Manual exclusions**: Add titles to `{username}_ignore.txt` to permanently skip them
- **Genre balancing**: Proportionally matches your viewing habits to avoid genre flooding
- **Age tracking**: "Days on List" column shows how long each item has been recommended

**Watchlist Structure:**
```markdown
## Movies to Watch

### Available on Your Services
#### Netflix (4 movies)
#### Hulu (2 movies)

### Available on Other Services
#### Max (3 movies)  ← Consider subscribing?
#### Apple TV+ (5 movies)

### Acquire (20 movies)
*Not on any streaming service - need physical/digital copy*
```

**Generated files:**
```
recommendations/external/
  ├── user1_watchlist.md    # Per-user shopping lists
  ├── user2_watchlist.md
  └── {username}_ignore.txt         # Manual exclusions (optional)
```

**Configuration:**
```yaml
external_recommendations:
  enabled: true  # Generate external watchlists
  movie_limit: 30  # Number of movie recommendations per user
  show_limit: 20  # Number of TV show recommendations per user

users:
  preferences:
    username:
      streaming_services:  # Your subscription services (US region)
        - netflix
        - hulu
        - disney_plus
        - paramount_plus
        - amazon_prime
```

**Use case:**
- See what you can **watch tonight** on your existing services
- Discover if a **new subscription** would be worthwhile (e.g., "10 recommendations on Max!")
- Know what to **acquire** when buying/downloading content

---

### Optional Features (Available but Disabled)

This fork includes several optional features from the original netplexflix project that are **disabled by default**. Enable them based on your needs:

#### Radarr Integration (Auto-Download Movies)

Automatically send recommended movies to Radarr for download.

**Status:** Disabled by default
**To enable:**
```yaml
movies:
  radarr:
    enabled: true
    url: http://localhost:7878
    api_key: YOUR_RADARR_API_KEY
    root_folder: /path/to/movies
    quality_profile: HD-1080p
```

**How it works:** When enabled, recommended movies are automatically added to Radarr's queue for download.

---

#### Sonarr Integration (Auto-Download TV Shows)

Automatically send recommended TV shows to Sonarr for download.

**Status:** Disabled by default
**To enable:**
```yaml
tv:
  sonarr:
    enabled: true
    url: http://localhost:8989
    api_key: YOUR_SONARR_API_KEY
    root_folder: /path/to/tv
    quality_profile: HD-1080p
```

**How it works:** When enabled, recommended TV shows are automatically added to Sonarr's queue for download.

---

#### Trakt Integration (Alternative External Recommendations)

Use Trakt API for external recommendations instead of TMDB-based watchlists.

**Status:** Disabled by default (we use TMDB-based external watchlists instead)
**To enable:**

1. Create Trakt app: https://trakt.tv/oauth/applications/new
2. Update config:
```yaml
general:
  plex_only: false  # Allow Trakt external recommendations

trakt:
  client_id: YOUR_CLIENT_ID
  client_secret: YOUR_CLIENT_SECRET
```

3. First run will prompt for Trakt authorization

**Difference from external watchlists:**
- **External watchlists (default)**: TMDB-based, markdown files, auto-removes acquired items
- **Trakt integration**: Trakt API-based, can sync watch history, different recommendation algorithm

**Note:** You can use both simultaneously if desired.

---

### Per-User Preferences

Edit config files to add/modify users:

```yaml
plex_users:
  users: username1, username2, username3

user_preferences:
  username1:
    display_name: Friendly Name
    exclude_genres:
      - horror
      - documentary
  username2:
    display_name: Another User
```

### Recommendation Settings

```yaml
general:
  limit_plex_results: 50      # Number of movie recommendations per user
  limit_tv_results: 20         # Number of TV show recommendations per user
  plex_only: true             # Only recommend from your library (not external)

plex:
  add_label: true
  append_usernames: true      # Creates per-user labels (Recommended_username)
  label_name: Recommended

# Quality filters
quality_filters:
  min_rating: 5.0            # Minimum TMDB rating (0-10)
  min_vote_count: 15         # Minimum votes required

# Similarity weights
weights:
  genre_weight: 0.20
  keyword_weight: 0.30
  director_weight: 0.25
  actor_weight: 0.15
  language_weight: 0.10
```

### Recency Weighting

Recent watches count more toward recommendations:

```yaml
recency_decay:
  enabled: true
  days_0_30: 1.0           # Last 30 days: full weight
  days_31_90: 0.75         # 1-3 months: 75%
  days_91_180: 0.50        # 3-6 months: 50%
  days_181_365: 0.25       # 6-12 months: 25%
  days_365_plus: 0.10      # 1+ years: 10%
```

---

## How It Works

1. **Watch History Analysis**: Fetches each user's Plex watch history
2. **Similarity Scoring**: Uses TMDB data to calculate similarity scores:
   - Genre overlap (20% weight)
   - Keyword matching (30% weight)
   - Director match (25% weight)
   - Cast overlap (15% weight)
   - Language match (10% weight)
3. **Rating Multipliers**: Higher-rated watches boost similar content
4. **Recency Decay**: Recent watches weighted more heavily
5. **Label Application**: Adds `Recommended_{username}` labels to top matches
6. **Smart Collections**: Collections auto-update based on labels

---

## Troubleshooting

### No Recommendations Generated

**Check logs:**
```bash
tail -n 100 logs/daily-run.log
```

**Common causes:**
- TMDB API key missing/invalid
- Plex URL or token incorrect
- Library names don't match config
- User has minimal watch history (need 5+ watched items)

### Collections Not Appearing

Run the smart collection script:
```bash
python3 create-smart-collections.py
```

Or manually create collections in Plex:
1. Go to Movies → Collections
2. Create Collection
3. Set Smart Filter: `Label is Recommended_{username}`

### Plex Token Expired

Get new token:
1. Plex Web → Settings → (your server) → General
2. Copy "X-Plex-Token" from URL
3. Update in both config files

### Python Dependencies Missing

```bash
pip3 install -r requirements.txt --upgrade
```

---

## Advanced Topics

### Adding a New User

1. Edit both config files:
```yaml
plex_users:
  users: existing_users, new_username

user_preferences:
  new_username:
    display_name: Display Name
    exclude_genres: []  # optional
```

2. Run recommendations:
```bash
bash scripts/run-all.sh
```

3. Recreate collections:
```bash
python3 create-smart-collections.py
```

### Genre Exclusions

Per-user genre filtering:

```yaml
user_preferences:
  username:
    exclude_genres:
      - horror
      - documentary
      - animation
```

TMDB genre list: Action, Adventure, Animation, Comedy, Crime, Documentary, Drama, Family, Fantasy, History, Horror, Music, Mystery, Romance, Science Fiction, Thriller, War, Western

---

## Project Structure

```
plex-recommender/
├── scripts/
│   ├── Movie-Recommendations-for-Plex/    # netplexflix (modified)
│   │   ├── MRFP.py                        # + per-user preferences, flexible matching
│   │   ├── config.yml
│   │   └── Logs/
│   ├── TV-Show-Recommendations-for-Plex/  # netplexflix (modified)
│   │   ├── TRFP.py                        # + per-user preferences, flexible matching
│   │   ├── config.yml
│   │   └── Logs/
│   ├── shared_plex_utils.py               # Custom utilities (new)
│   ├── run-movie-recommendations.sh        # Wrapper script (new)
│   ├── run-tv-recommendations.sh           # Wrapper script (new)
│   └── run-all.sh                          # Orchestrator (new)
├── logs/
│   └── daily-run.log                       # Main log file
├── create-smart-collections.py             # Smart collections (new)
└── setup.sh                                # Setup script (new)
```

---

## FAQ

**Q: How often should recommendations run?**
A: Daily is good. More frequent updates don't help much since watch history changes slowly.

**Q: Can I change the number of recommendations?**
A: Yes, edit `limit_plex_results` and `limit_tv_results` in config.yml (both scripts).

**Q: Do I need Plex Pass?**
A: No, this works with free Plex.

**Q: Will this modify my files?**
A: No, it only adds labels to Plex metadata. No files are touched.

**Q: Can other users see my recommendations?**
A: Collections are library-level (all users see all collections), but each collection is user-specific. Your collection shows your personalized picks.

**Q: How do I pin collections to the home screen?**
A: Plex Web → Library → Collections tab → Click collection → Three dots (⋮) → Pin to Home

**Q: What if a user has no watch history?**
A: Script skips them (need at least 5 watched items for meaningful recommendations).

---

## Credits

**This project is built on:**

- **netplexflix** - Core recommendation algorithm
  - [Movie-Recommendations-for-Plex](https://github.com/netplexflix/Movie-Recommendations-for-Plex) - TMDB similarity scoring, watch history analysis
  - [TV-Show-Recommendations-for-Plex](https://github.com/netplexflix/TV-Show-Recommendations-for-Plex) - TV show recommendation engine
  - Original algorithms for genre/cast/director/keyword matching (MRFP.py, TRFP.py - modified)

**Significant Enhancements (this fork):**
- **New Files**: `create-smart-collections.py`, `generate-external-recommendations.py`, `run.sh`, `shared_plex_utils.py`
- **Multi-user support**: Per-user recommendations, preferences, genre exclusions, streaming services, flexible username matching
- **External watchlists**: TMDB-based shopping lists with streaming service grouping, genre balancing, auto-removal of acquired items
- **Smart collections**: Automated Plex collection creation and updates
- **Config consolidation**: Single `config.yml` replacing multiple config files
- **Setup automation**: One-command setup with dependency checking and cron scheduling
- **7-day rotation**: Intelligent recommendation refresh to adapt to changing tastes

**Data:**
- **TMDB** - Movie/TV metadata and similarity data

---

## License

This project extends netplexflix's Movie/TV Recommendations for Plex scripts.

**Core recommendation engine** (MRFP.py, TRFP.py): Modified from [netplexflix](https://github.com/netplexflix) - original authors retain copyright.

**Enhancements and additions** (shared_plex_utils.py, create-smart-collections.py, wrapper scripts, documentation): Provided as-is under MIT License.

See individual files for detailed attribution.

---

**Enjoy your personalized Plex recommendations!** 🎬📺
