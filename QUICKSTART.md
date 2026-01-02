# Quick Start Guide

Get personalized Plex recommendations in under 5 minutes.

---

## What You Need

1. A Plex server with some watch history
2. A free TMDB API key
3. Your Plex token

That's it. The script installs everything else automatically.

---

## Step 1: Get Your Keys (2 minutes)

### TMDB API Key
1. [Create free TMDB account](https://www.themoviedb.org/signup)
2. Go to [API Settings](https://www.themoviedb.org/settings/api)
3. Click "Create" → "Developer"
4. Copy your API key

### Plex Token
Follow the official guide: [Finding Your Plex Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

Quick method:
1. Open Plex Web → Play any media
2. Click `Get Info` → `View XML`
3. Copy `X-Plex-Token=XXXXX` from the URL

---

## Step 2: Configure (1 minute)

Edit `config.yml`:

```yaml
plex:
  url: http://YOUR-PLEX-IP:32400
  token: YOUR_PLEX_TOKEN
  movie_library: Movies
  tv_library: TV Shows

tmdb:
  api_key: YOUR_TMDB_API_KEY

users:
  list: your_username
```

---

## Step 3: Run

```bash
./run.sh
```

The script will:
- Install Python dependencies automatically
- Analyze your watch history
- Create personalized collections in Plex

First run takes 5-10 minutes. After that, it's fast.

---

## What You Get

Collections in Plex:
```
🎬 John - Recommendations
📺 John - Recommendations
```

Pin them to your home screen. They update daily.

---

## Add More Users

```yaml
users:
  list: john, sarah, kids
  preferences:
    john:
      display_name: John
    sarah:
      display_name: Sarah
      exclude_genres: [horror]
```

---

## Schedule Daily Updates

First run will ask. Or manually:

```bash
crontab -e
# Add:
0 3 * * * cd /path/to/plex-recommender && ./run.sh >> logs/daily-run.log 2>&1
```

---

## Troubleshooting

```bash
# Check logs
tail -50 logs/daily-run.log

# Debug mode
./run.sh --debug
```

**Common issues:**
- "TMDB API error" → Check your API key at [TMDB Settings](https://www.themoviedb.org/settings/api)
- "Plex connection failed" → Verify URL and [token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
- "No recommendations" → User needs 5+ watched items

---

**That's it. Enjoy your personalized recommendations!**
