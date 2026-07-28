# Curatarr

**Personalized recommendations for your Plex library. Simple setup. Powerful results.**

Turn your Plex server into a smart recommendation engine. Analyze what you and your users watch, then surface the hidden gems already in your library—plus discover what to add next.

![Curatarr dashboard](docs/img/dashboard.png)

---

## Why This Exists

Your Plex library has thousands of titles. Your users have watched maybe 10% of them. The problem isn't content—it's discovery.

**Curatarr solves this by:**
- Analyzing each user's watch history
- Scoring unwatched content by similarity (keywords, genres, cast, directors)
- Creating personalized collections that update automatically
- Generating external watchlists so you know what to acquire next

---

## Download

Grab the binary for your platform - no Python, no `git clone`:

| Platform | Asset |
|---|---|
| Windows x64 | `curatarr-windows-x86_64.exe` |
| macOS (Apple Silicon) | `curatarr-macos-arm64` |
| Linux x64 | `curatarr-linux-x86_64` |
| Linux arm64 | `curatarr-linux-arm64` |

macOS binaries are Apple Silicon only - Intel Mac users should run from
source (see [Quick Start](#quick-start) below). Details in
[docs/BINARIES.md](docs/BINARIES.md).

[Latest release](https://github.com/OrchestratedChaos/curatarr/releases/latest) · full platform-specific steps and checksums in [docs/BINARIES.md](docs/BINARIES.md)

Double-click it and it opens the dashboard at `http://127.0.0.1:8787` in your browser - no Python install, no terminal.

First run, your OS will warn about an unsigned binary - Windows SmartScreen: **More info → Run anyway**; macOS Gatekeeper: right-click → **Open**. Details in [docs/BINARIES.md](docs/BINARIES.md#unsigned-binaries).

Binaries self-update: the app notifies you (CLI and web UI banner) when a newer release exists, and the web UI's **Update now** button (or `curatarr --self-update` from a terminal) downloads it, cryptographically verifies it (signed checksum + hash match - see [docs/BINARIES.md](docs/BINARIES.md#self-updating)), and swaps itself in place. No manual download required, but nothing applies automatically without you clicking/running one of those - for that, use a [source install](#quick-start) and set `general.update_mode: force`.

---

## Features

### For Your Library (What to Watch)
- **Per-user recommendations** — Each user gets their own curated collection
- **Per-user by default** — Each user's library Browse/Search only shows their own recommendation collection, not others' (UI-level separation, not access control — see FAQ)
- **Smart scoring** — Weights keywords, genres, cast, and directors
- **Recency bias** — Recent watches influence recommendations more
- **Rewatch detection** — Content you love gets weighted higher
- **Genre exclusions** — Skip horror for the kids, documentaries for movie night
- **Auto-updating collections** — `🎬 John - Recommendations` appears in Plex (customizable naming template — `collections.movie_name_template`/`tv_name_template` in tuning.yml)

### For Acquisition (What to Get)
- **External watchlists** — Content NOT in your library that users would love
- **Sequel Huntarr** — Find missing movies from collections you've started (complete that trilogy!)
- **Horizon Huntarr** — Track upcoming unreleased movies from franchises you own
- **Streaming service grouping** — "Available on Netflix" vs "Need to acquire"
- **Sonarr/Radarr integration** — Push recommendations directly for download
- **Trakt/Simkl/MDBList export** — Sync to tracking services and list managers
- **Auto-cleanup** — Items removed when they appear in your library
- **Genre balancing** — Matches user viewing habits proportionally

### For You (Simple & Robust)
- **One command** — `./run.sh` handles everything
- **Multi-library support** — Each Plex library gets its own Sonarr/Radarr root folder, quality profile, tags, monitor/search, and optionally its own *arr instance; recommendations run per-library so Movies, TV, Anime, and Kids each follow their own rules
- **Modular config** — Main settings plus optional integration files
- **Update notifications** — Notifies (CLI + dismissible web UI banner) when a newer signed release exists, for every `update_mode` including `off`; set `update_mode: force` to auto-apply on each run instead
- **Smart caching** — Auto-clears incompatible caches after updates
- **Auto-scheduling** — Optional daily cron job
- **Clean logs** — Know exactly what happened

---

## Quick Start

Below is the source install (Python required, auto-updates via signed releases).
Prefer a no-Python download instead? See [Download](#download) above.

### macOS / Linux
Requires Python 3.10+ (`python3 --version`). `run.sh` checks this up front and
tells you clearly if it isn't met - it won't leave a half-updated install.
```bash
git clone https://github.com/OrchestratedChaos/curatarr.git
cd curatarr
./run.sh    # Setup wizard runs on first launch
```

### Windows (PowerShell)
Requires Python 3.10+ (`python --version`). `run.ps1` checks this up front and
tells you clearly if it isn't met - it won't leave a half-updated install.
```powershell
git clone https://github.com/OrchestratedChaos/curatarr.git
cd curatarr
.\run.ps1   # Setup wizard runs on first launch
```

Below the Python floor, or don't want to manage a Python install at all? Use
the [standalone binary](#download) instead - it bundles its own Python and
UI deps, so it's unaffected by this.

### Docker
```bash
git clone https://github.com/OrchestratedChaos/curatarr.git
cd curatarr
./setup.sh              # Interactive setup wizard (recommended)
docker compose up -d
```

Or manually configure:
```bash
cp config/config.example.yml config/config.yml
# Edit config/config.yml with your details
docker compose up -d
```

Opens the same web UI as below at `http://localhost:8787` - no
build required, `docker-compose.yml` pulls the published multi-arch
(amd64/arm64) image from GHCR. See [docs/DOCKER.md](docs/DOCKER.md) for
volumes, scheduling recommendation runs, and updating.

First run takes 5-10 minutes to analyze your library. After that, it's fast.

---

## Web UI (beta)

A local dashboard for running recommendations and checking status without the terminal:

```bash
./run-ui.sh     # macOS/Linux
.\run-ui.ps1   # Windows (PowerShell)
```

Or skip the source install entirely and grab a [standalone binary](#download)
- it opens straight to this same UI.

Opens `http://127.0.0.1:8787` in your browser once the server is ready (binds to
localhost only). From there you can see each user's last-run status, trigger a
run (full pipeline, or just movie/tv/external) with a live streaming log, and
browse generated watchlists and past logs.

**Config screens** let you set up curatarr entirely from the browser instead of
hand-editing YAML:

- **Connections** (`/config/connections`) - Plex, TMDB, Tautulli, Sonarr, Radarr,
  and Trakt, each with a Test Connection button. (Simkl and MDBList are
  YAML-only for now - `config/simkl.yml` / `config/mdblist.yml` - not yet on
  this screen.)
- **Users** (`/config/users`) - add/remove Plex users and per-user preferences
  (display name, excluded genres, max content rating, streaming services).
  A "Fetch from Plex" button pulls the real account/Home/friend user list
  instead of typing names by hand, and can be re-run any time Plex users change.
- **Settings** (`/config/settings`) - scoring weights, quality filters, recency
  decay, rating multipliers, negative signals, external recommendation limits,
  and the Sonarr/Radarr/Trakt auto-sync safety toggles (surfaced with a warning -
  turning auto-sync on starts writing to your download clients on every run).
- **Libraries** (`/config/libraries`) - manage multiple Plex libraries, each
  with its own Sonarr/Radarr root folder, quality profile, tags, monitor/search
  behavior, and optionally its own *arr instance. Same "Fetch from Plex" button
  as Users, for library sections.

Secrets (tokens/API keys) are never shown once saved - fields show a
"configured" / "not set" status, and you only need to enter a new value to
change one. Saves are validated (e.g. scoring weights must sum to 1.0) and
written atomically, so a bad submission can't corrupt your config files.

### Screenshots

| Dashboard | Run |
|---|---|
| ![Dashboard](docs/img/dashboard.png) | ![Run](docs/img/run.png) |

| Results | Connections |
|---|---|
| ![Results](docs/img/results.png) | ![Connections](docs/img/connections.png) |

---

## Observability

Local-first only - nothing here ever leaves your machine or gets shipped to a
third-party service.

### Structured logging

Set `logging.format: json` in `config/config.yml` (default is `text`, the
existing colored console output - nothing changes unless you opt in) for
JSON-lines log output instead: one JSON object per line with `timestamp`,
`level`, `logger`, `message`, plus any structured fields a given log call
attaches (e.g. `user`, `engine`, `duration`). Convenient for feeding logs into
`jq`, Loki, or another log aggregator. Secrets are redacted the same way
either format - a leaked Plex/Sonarr/Radarr/etc. token is masked in JSON
output exactly as it already is in the human-readable one.

### `/metrics` (Prometheus)

The web UI exposes Prometheus text-format metrics at `/metrics`:
recommender run count/duration by engine and outcome, outbound API request
count/latency/error count by service (Plex, Sonarr, Radarr, TMDB, Trakt,
Simkl, MDBList, Tautulli), local cache hit/miss, self-update attempts/
failures, unhandled error count, and a `curatarr_build_info` gauge carrying
the running version. Rendered directly from a small local state file - no
new runtime dependency, and scraping never makes a network call or triggers
a Plex/TMDB request.

**Same auth as everything else**: for a native install (bound to
`127.0.0.1` only) `/metrics` needs no token, same as every other route. For
Docker (bound `0.0.0.0` - see [docs/DOCKER.md](docs/DOCKER.md#authentication))
it requires the exact same `CURATARR_AUTH_TOKEN` as the rest of the app -
`/metrics` labels expose library names, user counts, and which integrations
are configured, which isn't public data any more than the config screens
are. Only `/login` and `/healthz` are ever unauthenticated.

Example Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: curatarr
    metrics_path: /metrics
    static_configs:
      - targets: ["curatarr-host:8787"]
    authorization:
      credentials: "YOUR_CURATARR_AUTH_TOKEN"   # omit entirely for a loopback-only native install
```

### `/healthz` and `/status.json`

`/healthz` stays unauthenticated and deliberately minimal - liveness plus
version, nothing else - so it's safe to leave open to a container
orchestrator's health check with no token. Richer readiness detail (last run
time/outcome, whether `config.yml` currently loads, whether a run is in
progress) lives instead on the authenticated `/status.json`, which - like
`/metrics` - needs a token on a non-loopback bind.

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
Interactive HTML file with export buttons:
```
recommendations/external/watchlist.html

- All users combined in one interface
- Select which movies/shows to export
- Click "Export to Radarr/Sonarr" → downloads IMDB IDs
- Grouped by streaming service availability
- Auto-opens in browser after run (configurable)
```

Also generates per-user markdown for reference:
```
recommendations/external/john_watchlist.md
recommendations/external/sarah_watchlist.md
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

For Docker or CI environments, you can use environment variables instead of storing secrets in config.yml/sonarr.yml/radarr.yml/trakt.yml/simkl.yml/mdblist.yml. This is a convenience for operators using Docker secrets or an orchestrator's own secrets management - not a replacement for one; Curatarr itself still doesn't encrypt anything at rest.

| Variable | Overrides |
|----------|-----------|
| `PLEX_URL` | `plex.url` |
| `PLEX_TOKEN` | `plex.token` |
| `TMDB_API_KEY` | `tmdb.api_key` |
| `TAUTULLI_API_KEY` | `tautulli.api_key` |
| `SONARR_API_KEY` | `sonarr.api_key` |
| `RADARR_API_KEY` | `radarr.api_key` |
| `TRAKT_CLIENT_SECRET` | `trakt.client_secret` |
| `TRAKT_ACCESS_TOKEN` | `trakt.access_token` |
| `TRAKT_REFRESH_TOKEN` | `trakt.refresh_token` |
| `SIMKL_CLIENT_ID` | `simkl.client_id` |
| `SIMKL_ACCESS_TOKEN` | `simkl.access_token` |
| `MDBLIST_API_KEY` | `mdblist.api_key` |

Environment variables take precedence over config file values, and the web UI's Connections screen correctly shows a field as "configured" when it's set only via the environment (never the value itself - only ever a masked status).

### Per-User Preferences
```yaml
users:
  list: john, sarah, kids
  preferences:
    john:
      display_name: John
      # Adds to (never replaces) the top-level streaming_services list -
      # same merge behavior as exclude_genres above.
      streaming_services: [netflix, hulu, disney_plus]
    sarah:
      display_name: Sarah
      exclude_genres: [horror]
    kids:
      display_name: Kids
      exclude_genres: [horror, thriller, war]
      max_rating: PG  # Only G and PG content (movies: G < PG < PG-13 < R < NC-17)
```

**Content Rating Filter:**
- Movies: `G`, `PG`, `PG-13`, `R`, `NC-17` (from least to most restrictive)
- TV: `TV-Y`, `TV-Y7`, `TV-G`, `TV-PG`, `TV-14`, `TV-MA` (from least to most restrictive)
- Recommendations above the user's `max_rating` are filtered out

### Multi-Library Setup (Optional)

By default Curatarr uses the single `plex.movie_library` / `plex.tv_library`
pair above. To manage multiple Plex libraries separately (e.g. Movies + Kids
Movies, or TV Shows + Anime) - each with its own Sonarr/Radarr routing - add
a `libraries:` block to `config/config.yml` instead:

```yaml
libraries:
  - id: movies                       # stable slug; auto-derived from name if omitted
    name: Movies
    section: Movies                  # Plex section title
    media_type: movie                # movie | tv
    arr:                             # optional; each field falls back to the global sonarr.yml/radarr.yml
      root_folder: /data/movies
      quality_profile: HD-1080p
      tag: Curatarr
      monitor: false
      search: false
      minimum_availability: released # movie-only
    instance:                        # optional; routes this library to its own *arr instance
      url: http://localhost:7878
      api_key: YOUR_RADARR_API_KEY
  - id: tv-shows
    name: TV Shows
    section: TV Shows
    media_type: tv
    arr:
      root_folder: /data/tv
      quality_profile: HD-1080p
      series_type: standard          # tv-only
```

Each library is scanned and scored independently. `arr:` sets the
root folder/quality profile/tag/monitor/search/availability/series-type for
that library - any field left out falls back to the matching global
`radarr.yml`/`sonarr.yml` setting for that media type. `instance:` is
separate and optional: it points the library at its own Radarr/Sonarr
server instead of the default one in `radarr.yml`/`sonarr.yml`, for setups
where (say) Anime routes to a different Radarr than Movies. Manage all of
this from the browser instead at `/config/libraries` (see
[Web UI](#web-ui-beta)).

**Already using `plex.movie_library`/`plex.tv_library`?** Nothing to change -
Curatarr auto-synthesizes those into a single-entry `libraries:` list at
load time, so existing configs keep working unchanged.

### Tautulli Integration (Optional)

Supplements Plex's own watch history with history from Tautulli - mainly
useful for shared/external users whose Plex-native history retention is
thin.

```yaml
# In config/config.yml
tautulli:
  enabled: true
  url: http://YOUR_TAUTULLI_URL:8181
  api_key: YOUR_TAUTULLI_API_KEY   # Settings -> Web Interface -> API Key
```

Users are matched to Plex accounts by email (falls back to username).
Disabled by default; if Tautulli is unreachable or a user can't be matched,
Curatarr silently falls back to Plex-only history.

### General Settings
```yaml
general:
  plex_only: true              # Only recommend from Plex library
  update_mode: notify          # notify (default) | force | off - see below
  log_retention_days: 7        # Keep logs for 7 days

logging:
  level: INFO                  # DEBUG, INFO, WARNING, ERROR
  format: text                 # text (default) | json - see Observability below
```

`general.update_mode` controls how Curatarr handles new releases:
- `notify` (default) — CLI prints a one-line notice and the web UI shows a
  dismissible banner when a newer release exists; nothing is applied
  automatically. Source installs (`run.sh`/`run.ps1`) additionally prompt
  `Update available: vX. Update now? [y/N]` on each interactive run. The web
  UI banner's **Update now** button works for both install types: source
  installs verify and apply the same signed release used by `run.sh`/
  `run.ps1`; binaries download, cryptographically verify (signed checksum +
  hash match - see [docs/BINARIES.md](docs/BINARIES.md#self-updating)), and
  swap themselves in place. Either way it reconnects automatically once the
  server restarts. Binaries can also self-update from the command line:
  `curatarr --self-update`.
- `force` — source installs (`run.sh`/`run.ps1`) auto-apply verified signed
  releases from GitHub on each run, no prompt (the old `auto_update: true`
  behavior). Binaries never auto-apply anything regardless of this setting -
  `force` on a binary install just behaves like `notify` (banner + CLI
  notice only; use the **Update now** button or `--self-update` to apply).
- `off` — same CLI notice and web UI banner as `notify` above: `off` never
  means "don't tell me", only "don't ask me and don't apply automatically"
  (an opted-out install silently missing every update forever was
  considered a bug, not a feature). The one thing `off` actually disables is
  `run.sh`/`run.ps1`'s interactive `Update available: vX. Update now? [y/N]`
  prompt on launch (source installs only) - the dismissible banner, its
  **Update now** button, and the CLI notice all still work exactly like
  `notify`.

Either way, the web UI banner's dismiss button (**×**) doesn't hide a
version forever - it snoozes that specific version for 7 days, after which
it's shown again if you're still on it. A release newer than the one you
dismissed is never held back by an existing snooze; it shows immediately.

`general.auto_update` (legacy) is still read as a fallback if `update_mode`
isn't set: `true` behaves like `force`, `false` behaves like `off`. Existing
configs keep working unchanged; new installs get `update_mode: notify`.

### Tuning (Optional)
```yaml
movies:
  limit_results: 50           # Recommendations per user (the final
                              # collection count - see general.limit_plex_results
                              # below for the internal scoring buffer)
  quality_filters:
    min_rating: 5.0           # TMDB rating threshold
    min_vote_count: 50        # Minimum votes

recency_decay:
  enabled: true
  days_0_30: 1.0              # Recent watches: full weight
  days_31_90: 0.75            # 1-3 months: 75%
  days_91_180: 0.50           # 3-6 months: 50%

collections:
  stale_removal_days: 7       # Rotate unwatched Plex collection labels

general:
  limit_plex_results: 100     # Advanced: candidate-scoring buffer per run
                              # (defaults to 2x limit_results; rarely needs changing)

external_recommendations:
  min_relevance_score: 0.65   # See note below
  auto_open_html: false       # Open HTML watchlist in browser after run
```

### Trakt Integration (Optional)

Full integration with Trakt for watch history import, discovery, and list export:

```yaml
# config/trakt.yml
enabled: true
client_id: YOUR_TRAKT_CLIENT_ID
client_secret: YOUR_TRAKT_CLIENT_SECRET
access_token: (filled by setup wizard)

# Import watch history from Trakt
import:
  enabled: true
  include_ratings: true

# Discovery from Trakt trending/popular
discovery:
  enabled: true
  include_trending: true
  include_popular: true

# Export recommendations to Trakt lists
export:
  enabled: true
  auto_sync: true
  user_mode: mapping
  plex_users: [your_username]
```

**Setup:** Run `./run.sh` and follow Step 6, or manually create `config/trakt.yml`.

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

YAML-only for now - not yet in the [Connections UI](#web-ui-beta). Export
recommendations to MDBList for use with Kometa/PMM and other tools:

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

**Tip:** MDBList exports work great with [Agregarr](https://agregarr.org) for Plex collection placeholders. See the [wiki](https://github.com/OrchestratedChaos/curatarr/wiki/Agregarr-Integration) for setup instructions.

### Simkl Integration (Optional)

YAML-only for now - not yet in the [Connections UI](#web-ui-beta). Full
integration with Simkl for anime/TV/movie tracking with excellent anime database:

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

### Huntarr: Collection Movie Finder

Huntarr scans your Plex library for movies that belong to collections (trilogies, franchises, etc.) and helps you track what's missing and what's coming.

```yaml
# In config/config.yml
huntarr:
  sequel_huntarr: true    # Find missing movies from collections you've started
  horizon_huntarr: true   # Track upcoming unreleased movies from collections you own
```

**Sequel Huntarr** — Missing collection movies:
- Scans all movies in your library for TMDB collection IDs
- Shows collection name and how many you own (e.g., "2/3")
- Displays streaming availability for missing movies
- Only includes released movies (no placeholders)

**Horizon Huntarr** — Upcoming releases:
- Finds unreleased movies from franchises you own
- Shows production status (Post Production, In Production, Planned, Rumored)
- Displays expected release date (or TBA)
- Perfect for tracking that next Marvel or Star Wars film

**Both features appear as separate tabs in the HTML watchlist, centered below user tabs.**

**Command-line flag:**
- `--huntarr-only` — Run only Huntarr features, skip recommendations (`./run.sh --huntarr-only`)

**Caching:** Collection data cached for 7 days. IMDB IDs cached permanently. Cache auto-invalidates when your library changes.

### External Recommendations: Relevance Score

The `min_relevance_score` setting (0.0-1.0) controls how strictly personal the external watchlist recommendations are:

- **Score** = How many of your watched items recommend this title (normalized 0-100%)
- **Rating** = TMDB audience rating (used only as tiebreaker)

**How it works:**
1. Items above the threshold are prioritized (sorted by personal relevance)
2. Lower-scored items only appear if not enough high-relevance items exist
3. This ensures you get personally relevant content, not just popular movies

**Tuning:**
- `0.65` (default) — Balanced. Most users should start here.
- `0.50` — Looser. More variety, less strictly personalized.
- `0.25` — Much looser. Maximum variety, least personalized.

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
├── config/                  # Configuration files
│   ├── config.yml           # Main config (Plex, TMDB, users, libraries, tautulli)
│   ├── tuning.yml           # Scoring weights and display options
│   ├── trakt.yml            # Trakt integration
│   ├── sonarr.yml           # Sonarr integration
│   ├── radarr.yml           # Radarr integration
│   ├── mdblist.yml          # MDBList integration
│   └── simkl.yml            # Simkl integration
├── recommenders/
│   ├── movie.py             # Movie recommendations
│   ├── tv.py                # TV show recommendations
│   ├── external.py          # External watchlist generator
│   └── base.py              # Shared base classes
├── utils/                   # Shared utilities (23 modules)
├── tests/                   # Unit tests (~1,754 across 41 files)
├── run.sh                   # Main entry point (macOS/Linux)
├── run.ps1                  # Main entry point (Windows)
├── run-ui.sh                # Web UI launcher (macOS/Linux)
├── run-ui.ps1               # Web UI launcher (Windows)
├── web/                     # Local web UI (Flask, beta)
├── curatarr_app.py          # Standalone-binary entry point (see docs/BINARIES.md)
├── curatarr.spec            # PyInstaller build spec
├── Dockerfile               # Docker image definition (see docs/DOCKER.md)
├── docker-compose.yml       # Docker Compose template
├── docker-entrypoint.sh     # Docker CMD dispatcher (web UI / one-shot recommend)
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
0 3 * * * cd /path/to/curatarr && docker compose run --rm curatarr-recommend >> logs/daily-run.log 2>&1
```
See [docs/DOCKER.md](docs/DOCKER.md#scheduling-recommendation-runs) for details.

---

## FAQ

**Q: Do I need Plex Pass?**
No. Works with free Plex.

**Q: Will this modify my media files?**
No. Only adds labels to Plex metadata.

**Q: How many watched items needed?**
At least 5 for meaningful recommendations.

**Q: Can users see each other's recommendations?**
When browsing or searching the library, no — by default each user's recommendation collection is excluded from every other user's view (Plex label restrictions), and the admin/server owner always sees all of them (Plex limitation). This is UI-level separation, not an access-control boundary: Plex enforces the exclusion on the collection object, not on the items inside it, so a user who already has (or guesses — ratingKeys are small sequential integers) a collection's ratingKey can still retrieve its contents directly through the Plex API. Disable with `private_collections: false` in tuning.yml if you want shared visibility instead.

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
python3 -c "import yaml; print(yaml.safe_load(open('config/config.yml')))"
```

### Windows (PowerShell)
```powershell
# Check logs
Get-Content logs/daily-run.log -Tail 100

# Run with debug output
.\run.ps1 -Debug

# Verify config
python -c "import yaml; print(yaml.safe_load(open('config/config.yml')))"
```

**Common issues:**
- TMDB API key invalid → Get free key from themoviedb.org
- Plex connection failed → Check URL and token
- No recommendations → User needs more watch history
- "Cache outdated" message → Normal after updates, rebuilds automatically
- Want to stop `run.sh`/`run.ps1`'s interactive "Update now? [y/N]" prompt on launch → Set `general.update_mode: off` in config/config.yml (the dismissible CLI/web notices still appear either way - see `general.update_mode` above)
- Want updates auto-applied instead of just notified → Set `general.update_mode: force`
- Want a specific update's notice to stop appearing for a while → Click the **×** on the web UI banner (snoozes that version for 7 days)

### Docker
See [docs/DOCKER.md](docs/DOCKER.md#troubleshooting) for the full Docker
troubleshooting guide (logs, healthcheck, permissions, LAN/reverse-proxy
access). Docker installs update via `docker pull` - see
[Updating](docs/DOCKER.md#updating) - not `git pull` or the web UI's
"Update now" button, neither of which apply inside a container.

**Manual update for a source install (if update_mode is `off` or `notify`):**
```bash
git pull origin main
```

---

## Development

### Running Tests

The real recipe is [.github/workflows/tests.yml](.github/workflows/tests.yml)
(what actually runs on every push/PR); the short version:

```bash
python -m pip install --upgrade pip
pip install --require-hashes -r requirements.lock -r requirements-ui.lock -r requirements-docker.lock
pip install pytest pytest-cov pip-audit

pytest tests/ -v --tb=short --cov=. --cov-report=term-missing --cov-fail-under=85
```

Lint/format (also CI-enforced - `ruff format --check` blocks merges,
`ruff check`/`mypy` are advisory-only for now):

```bash
pip install ruff mypy
ruff format --check .
ruff check .
mypy .
```

---

## Contributing

### Feature Requests

Have an idea for Curatarr? We track feature requests as GitHub Issues and **your vote matters!**

**How to vote:**
1. Browse [open enhancement requests](https://github.com/OrchestratedChaos/curatarr/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement)
2. Find a feature you want
3. **Click the 👍 reaction** on the issue (top of the issue, next to the title)
4. That's it! Issues with more votes get prioritized

**How to request a feature:**
1. [Search existing issues](https://github.com/OrchestratedChaos/curatarr/issues) to avoid duplicates
2. [Open a new issue](https://github.com/OrchestratedChaos/curatarr/issues/new) with the `enhancement` label
3. Describe what you want and why it would be useful

### Code Contributions

This is a solo-maintained project and external pull requests are not
accepted - any PR not opened by the maintainer is closed automatically.
Bug reports and feature requests via GitHub issues are very welcome (see
above); if you want changes beyond that, fork it.

---

## Credits

Inspired by [netplexflix's](https://github.com/netplexflix) Movie/TV Recommendations for Plex. This project takes the core concept of TMDB-based similarity scoring and rebuilds it with:

- Clean modular architecture
- Multi-user support with per-user preferences
- External watchlists with streaming service grouping
- Automated collection management
- Integration ecosystem (Trakt, Simkl, Sonarr, Radarr, MDBList)

---

## Support

Curatarr is free and will stay free - no premium tier, no paywalled
features, ever. If you'd like to support development, you can do so via
[Ko-fi](https://ko-fi.com/orchestratedchaos). It's entirely optional and
buys nothing - no priority support, no feature influence, no say over the
roadmap. Just a way to say thanks if you feel like it.

## License

MIT License. Use it, fork it, make it yours.

---

**Take your Plex to the next level.**
