# Pinning Recommendations to Plex Home

Show your personalized collections on the Plex home screen.

---

## What Gets Created

For each user, collections are automatically created:

```
🎬 John - Recommendations    (Movies)
📺 John - Recommendations    (TV Shows)
```

These update automatically based on watch history.

---

## How to Pin

### Step 1: Find Your Collection
1. Open Plex Web
2. Go to Movies library
3. Click **Collections** tab

### Step 2: Pin It
1. Find your collection (e.g., `🎬 John - Recommendations`)
2. Click ⋮ (three dots)
3. Select **"Pin to Home"**

### Step 3: Repeat for TV
Same process in your TV Shows library.

---

## Result

Your pinned collections appear:
- At the top of library home
- On the main Plex dashboard

They update daily with fresh recommendations.

---

## Visibility

Plex collections are library-level:
- All users see all collections
- Each is clearly labeled by user name
- Users can pin their own for quick access
- Great for discovery ("What's John watching?")

---

## Troubleshooting

### Collection Not Appearing

```bash
./run.sh
```
Then refresh Plex Web (F5).

### Collection Empty

- User needs 5+ watched items for recommendations
- Check logs: `tail -50 logs/daily-run.log`

### Can't Pin

Only Plex admin can pin collections library-wide. Non-admins can add to playlists instead.

---

**Enjoy your personalized recommendations!**
