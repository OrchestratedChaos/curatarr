# Pinning Recommendations to Plex Home

Make your personalized recommendation collections visible on Plex landing pages.

---

## Collections Created

The system creates these collections:

**Movies:**
- 🎬 User1 - Recommendation
- 🎬 User2 - Recommendation
- 🎬 User3 - Recommendation
- 🎬 User4 - Recommendation
- 🎬 User5 - Recommendation
- 🎬 User6 - Recommendation

**TV Shows:**
- 📺 User1 - Recommendation
- 📺 User2 - Recommendation
- 📺 User3 - Recommendation
- 📺 User4 - Recommendation
- 📺 User5 - Recommendation
- 📺 User6 - Recommendation

Each collection auto-updates based on labels (`Recommended_{username}`).

---

## How to Pin Collections to Home

### Step 1: Open Plex Web App

Navigate to your Plex server in a web browser.

### Step 2: Go to Library

Click on **Movies** library from the sidebar.

### Step 3: View Collections

Click the **Collections** tab at the top.

### Step 4: Pin Your Collection

1. Find your collection (e.g., "🎬 User1 - Recommendation")
2. Click the three dots (⋮) on the collection
3. Select **"Pin to Home"**

### Step 5: Repeat for TV Shows

1. Go to **TV Shows** library
2. Click **Collections** tab
3. Find your TV recommendations collection
4. Click three dots (⋮) → **"Pin to Home"**

---

## Result

Pinned collections will appear:
- At the top of the library home screen
- In the main Plex home dashboard (depending on Plex settings)

---

## Per-User Visibility

**Important**: Plex collections are library-level, not user-specific.

### What This Means:
- ✅ All users can see all collections
- ✅ Each collection shows personalized picks for that user
- ✅ Users can browse other users' recommendations
- ✅ Labels prevent overlap (each user's picks stay separate)

### User Experience:
- All users see all collections
- Each collection is clearly labeled with the user's name
- Users can pin their own collection for quick access
- Others can browse different users' recommendations for discovery

---

## Collection Details

### Auto-Updating
Collections automatically update when:
- Daily cron job runs (3 AM)
- Labels change based on new watch history
- No manual intervention needed

### Collection Contents
- **Movies**: Up to 50 personalized movie recommendations per user
- **TV Shows**: Up to 20 personalized TV show recommendations per user
- Based on similarity scoring (genre, cast, director, keywords, language)
- Recent watches weighted more heavily

---

## Troubleshooting

### Collections Not Appearing

**Solution 1**: Re-run recommendations (collections are created automatically)
```bash
./run.sh
```

**Solution 2**: Check if collections exist
1. Plex Web → Movies → Collections tab
2. Look for "🎬 [username] - Recommendation" collections
3. If missing, run the script above

### Can't Pin Collection

**Check permissions**: Only the Plex admin can pin collections library-wide.

**Workaround**: Non-admin users can:
1. Add collections to playlists
2. Use Plex's "Watch Later" feature for individual items
3. Ask admin to pin collections

### Collection is Empty

**Possible causes:**
- User has minimal watch history (need 5+ watched items)
- Filters too strict (min_rating too high, min_vote_count too high)
- Labels not applied (check logs for errors)

**Check labels on items:**
1. Plex Web → Movies → Pick any movie
2. Click three dots (⋮) → "Get Info"
3. Look for "Labels" section
4. Should show `Recommended_{username}` if item is recommended

**Check logs:**
```bash
tail -n 100 logs/daily-run.log
```

---

## Customization

### Change Collection Sort Order

Collections default to alphabetical (by title). To change:

1. Plex Web → Library → Collections → Your Collection
2. Click "Edit Collection"
3. Change "Sort by" option:
   - Title (A-Z)
   - Recently Added
   - Release Date
   - Rating

### Hide Other Users' Collections

If you only want to see your own collection:

1. You can't hide collections (Plex limitation)
2. Workaround: Pin your own collection to top
3. Other collections will be further down the list

---

## Daily Updates

System updates recommendations automatically:

**Schedule**: Daily at 3 AM (via cron)

**Process**:
1. Fetches watch history for all users
2. Calculates similarity scores using TMDB data
3. Updates labels on Plex items
4. Smart collections refresh automatically

**No manual steps needed** - collections stay current with your viewing habits.

---

## Need Help?

See the full [README.md](README.md) for:
- Installation guide
- Configuration options
- Troubleshooting
- Advanced features

---

**Enjoy browsing personalized recommendations!** 🎬📺
