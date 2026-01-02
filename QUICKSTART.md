# Quick Start Guide

Get your Plex Recommendation System running in 5 minutes!

**Note:** This is an enhanced fork of [netplexflix](https://github.com/netplexflix) with multi-user support and smart collections.

---

## Step 1: Clone & Configure (3 minutes)

```bash
# Clone the repository
git clone <your-repo-url>
cd plex-recommender

# Edit the config file
nano config.yml
```

**Required settings:**
- `tmdb.api_key` - Get free key from https://www.themoviedb.org/settings/api
- `plex.url` - Your Plex server URL
- `plex.token` - See: https://support.plex.tv/articles/204059436
- `users.list` - Your Plex usernames (comma-separated)

**Example:**
```yaml
plex:
  url: http://192.168.1.100:32400
  token: YOUR_PLEX_TOKEN

tmdb:
  api_key: YOUR_TMDB_API_KEY

users:
  list: user1, user2, user3
  preferences:
    user1:
      display_name: User1
      exclude_genres: [horror]
      streaming_services: [netflix, hulu, disney_plus]
```

---

## Step 2: Run! (2 minutes)

```bash
chmod +x run.sh
./run.sh
```

That's it! The script will:
- ✓ Auto-install all dependencies
- ✓ Generate recommendations for all users
- ✓ Create smart collections in Plex
- ✓ Optionally set up daily cron job

**First run takes 5-10 minutes** to analyze watch history.

---

## What Gets Created

**Smart collections for each user** (in Plex):
- 🎬 User1 - Recommendation (Movies)
- 🎬 User2 - Recommendation (Movies)
- 📺 User1 - Recommendation (TV Shows)
- 📺 User2 - Recommendation (TV Shows)
- ... and so on for each user

**External watchlists** (markdown files):
- `recommendations/external/{username}_watchlist.md` - shopping lists grouped by streaming service
  - Shows what's on **your services** (Netflix, Hulu, etc.)
  - Shows what's on **other services** (consider subscribing?)
  - Shows what needs **acquisition** (not on any streaming service)

These auto-update daily with new recommendations!

**Smart rotation:** Unwatched recs older than 7 days are automatically removed and replaced with fresh picks. Keeps your recommendations current as your tastes change!

---

## Pin to Home Screen (Optional)

In Plex Web App:

1. Go to Movies library → Collections tab
2. Find your collection (e.g., "🎬 User1 - Recommendation")
3. Click the three dots (⋮) → **"Pin to Home"**
4. Repeat for TV Shows library

---

## Subsequent Runs

Just run `./run.sh` anytime to update recommendations.

Or let the cron job handle it automatically (daily at 3 AM).

---

## Useful Commands

```bash
# Run recommendations (manual update)
./run.sh

# View recent logs
tail -n 50 logs/daily-run.log

# View per-user detailed logs
ls scripts/Movie-Recommendations-for-Plex/Logs/
ls scripts/TV-Show-Recommendations-for-Plex/Logs/

# Check cron schedule
crontab -l | grep plex

# Modify cron schedule
crontab -e
```

---

## Troubleshooting

### "Config not found" or "TMDB API key missing"

Make sure you edited `config.yml` in the project root and set:
- `tmdb.api_key`
- `plex.url`
- `plex.token`

### No recommendations generated

**Check logs:**
```bash
tail -n 100 logs/daily-run.log
```

**Common causes:**
- TMDB API key invalid
- Plex URL/token incorrect
- Library names don't match (`movie_library`, `tv_library` in config)
- User has minimal watch history (need 5+ watched items)

### Collections not appearing in Plex

Run the script again - it creates collections automatically:
```bash
./run.sh
```

Then refresh Plex Web App (F5).

### "Module not found" (Python errors)

The script should auto-install dependencies, but if it fails:
```bash
pip3 install -r requirements.txt --upgrade
```

---

## Customization

All settings in one place: `config.yml`

### Change Number of Recommendations

```yaml
movies:
  limit_results: 50      # Movies per user

tv:
  limit_results: 20      # TV shows per user
```

### Add/Remove Users

```yaml
users:
  list: user1, user2, user3
  preferences:
    user1:
      display_name: User1
      exclude_genres: [horror]
      streaming_services: [netflix, hulu]
    user2:
      display_name: User2
      streaming_services: [netflix, amazon_prime]
```

After changes, run:
```bash
./run.sh
```

---

## Need More Help?

See the full [README.md](README.md) for:
- Detailed configuration options
- How the similarity algorithm works
- Advanced Trakt integration
- Per-user genre exclusions
- Troubleshooting guide

---

**That's it! Enjoy your personalized recommendations!** 🎬📺
