# Plex Recommender

**Personalized recommendations for your Plex library. Simple setup. Powerful results.**

Turn your Plex server into a smart recommendation engine. Analyze what you and your users watch, then surface the hidden gems already in your library—plus discover what to add next.

---

## Why This Exists

Your Plex library has thousands of titles. Your users have watched maybe 10% of them. The problem isn't content—it's discovery.

**Plex Recommender solves this by:**
- Analyzing each user's watch history
- Scoring unwatched content by similarity (genres, cast, keywords, language)
- Creating personalized collections that update automatically
- Generating external watchlists so you know what to acquire next

---

## Features

### For Your Library (What to Watch)
- **Per-user recommendations** — Each user gets their own curated collection
- **Smart scoring** — Weights genres, directors, cast, keywords, and language
- **Recency bias** — Recent watches influence recommendations more
- **Rewatch detection** — Content you love gets weighted higher
- **Genre exclusions** — Skip horror for the kids, documentaries for movie night
- **Auto-updating collections** — `🎬 John - Recommendations` appears in Plex

### For Acquisition (What to Get)
- **External watchlists** — Content NOT in your library that users would love
- **Streaming service grouping** — "Available on Netflix" vs "Need to acquire"
- **Auto-cleanup** — Items removed when they appear in your library
- **Genre balancing** — Matches user viewing habits proportionally

### For You (Simple & Robust)
- **One command** — `./run.sh` handles everything
- **Single config file** — All settings in one place
- **Auto-scheduling** — Optional daily cron job
- **Clean logs** — Know exactly what happened

---

## Quick Start

```bash
# 1. Clone and enter directory
git clone <your-repo-url>
cd plex-recommender

# 2. Edit config.yml with your details (see links below)

# 3. Run it
./run.sh
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
Markdown files showing what to acquire:
```
recommendations/external/john_watchlist.md

## Movies to Watch

### Available on Your Services
#### Netflix (4 movies)
- The Power of the Dog (2021) - Drama
- Glass Onion (2022) - Mystery

#### Disney+ (2 movies)
- ...

### Need to Acquire (15 movies)
- Oppenheimer (2023) - Drama, History
- ...
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
```

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
Score = (genre_match × 0.20) +
        (keyword_match × 0.30) +
        (director_match × 0.25) +
        (actor_match × 0.15) +
        (language_match × 0.10)
```

Weighted by recency (recent watches count more) and rewatch count (loved content counts more).

---

## Project Structure

```
plex-recommender/
├── movie_recommender.py     # Movie recommendations
├── tv_recommender.py        # TV show recommendations
├── external_recommender.py  # External watchlist generator
├── utils.py                 # Shared utilities
├── config.yml               # Your configuration
├── run.sh                   # Main entry point
├── cache/                   # TMDB metadata cache
├── logs/                    # Execution logs
└── recommendations/
    └── external/            # Generated watchlists
```

---

## Scheduling

First run prompts for cron setup. Or add manually:

```bash
# Daily at 3 AM
0 3 * * * cd /path/to/plex-recommender && ./run.sh >> logs/daily-run.log 2>&1
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

```bash
# Check logs
tail -100 logs/daily-run.log

# Run with debug output
./run.sh --debug

# Verify config
python3 -c "import yaml; print(yaml.safe_load(open('config.yml')))"
```

**Common issues:**
- TMDB API key invalid → Get free key from themoviedb.org
- Plex connection failed → Check URL and token
- No recommendations → User needs more watch history

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
