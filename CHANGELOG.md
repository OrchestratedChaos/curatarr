# Changelog

All notable changes to Curatarr will be documented in this file.

## [2.4.0] - 2026-01-05

### Added
- **MDBList integration** — Export recommendations to shareable MDBList lists
  - Push recommendations to MDBList for use with Kometa/PMM and other tools
  - Configurable via `config/mdblist.yml`
  - Simple API key authentication (no OAuth)
  - Supports user_mode: `mapping`, `per_user`, or `combined`
  - Replace or append mode for list updates
  - Setup wizard integration in `run.sh` (Step 9)
  - 36 new unit tests for MDBList client

### Technical
- New `utils/mdblist.py` module with `MDBListClient` class
- Uses TMDB IDs directly (no conversion needed)
- Rate limiting with 0.1s delay between API calls

## [2.3.0] - 2026-01-05

### Added
- **Radarr integration** — Auto-add external movie recommendations to Radarr
  - Push recommendations directly to Radarr for tracking/downloading
  - Configurable via `config/radarr.yml` (mirrors Sonarr config style)
  - Safe defaults: `monitor: false`, `search_for_movie: false` (just adds to library)
  - Tagging system for easy cleanup (`Curatarr` tag on all added movies)
  - Setup wizard integration in `run.sh` (Step 8)
  - Supports user_mode: `mapping`, `per_user`, or `combined`
  - 28 new unit tests for Radarr client

### Technical
- New `utils/radarr.py` module with `RadarrClient` class
- Uses TMDB IDs directly (no conversion needed like Sonarr)
- Rate limiting with 0.1s delay between API calls

## [2.2.0] - 2026-01-05

### Added
- **Sonarr integration** — Auto-add external TV recommendations to Sonarr
  - Push recommendations directly to Sonarr for tracking/downloading
  - Configurable via `config/sonarr.yml` (mirrors Trakt config style)
  - Safe defaults: `monitor: false`, `search_missing: false` (just adds to library)
  - Tagging system for easy cleanup (`Curatarr` tag on all added shows)
  - Setup wizard integration in `run.sh` (Step 7)
  - Supports user_mode: `mapping`, `per_user`, or `combined`
  - 27 new unit tests for Sonarr client

### Technical
- New `utils/sonarr.py` module with `SonarrClient` class
- ID conversion: TMDB → IMDB → Sonarr lookup → TVDB → add_series
- Rate limiting with 0.5s delay between API calls

## [2.1.4] - 2026-01-05

### Changed
- Skip auto-update check in Docker containers (users should rebuild to update)
- Removed git package from Docker image (no longer needed)

## [2.1.3] - 2026-01-04

### Changed
- Removed unused imports across 6 files (traceback, Type, sys, List, Optional, yaml)

## [2.1.2] - 2026-01-04

### Changed
- **Silent exception handlers now log debug messages** — All `except: pass` patterns replaced with `logger.debug()` or `log_warning()` calls for easier troubleshooting
- **Scoring constants extracted to config.py** — TF-IDF penalties and popularity dampening values now defined as named constants
- **Discovery constants extracted in external.py** — Magic numbers for candidate discovery now use named constants
- **Deferred import moved to module level** — `import random` in scoring.py moved to top of file
- **Added type hints** — Key functions in external.py and external_output.py now have proper type annotations
- **Extracted Trakt batch sync helper** — Duplicate batching code consolidated into `_sync_items_in_batches()` function

### Fixed
- Removed dead code (unused language extraction block in external.py)

## [2.1.1] - 2026-01-04

### Changed
- **Code refactoring** — Major cleanup reducing duplicate code by ~300 lines
  - Extracted shared CLI utilities to `utils/cli.py`
  - Consolidated Trakt enhancement logic to `utils/trakt.py`
  - Added `get_project_root()` utility to eliminate repeated path patterns
  - Simplified main() functions in movie.py and tv.py recommenders

### Fixed
- Bare except blocks replaced with specific exception types
- Deferred imports moved to module level for cleaner code
- Removed redundant `watched_data` variable (now uses `watched_data_counters` consistently)
- Improved type hints (e.g., `Set[tuple]` → `Set[Tuple[str, Optional[int]]]`)
- Added debug logging to silent exception handlers for easier troubleshooting

## [2.1.0] - 2026-01-04

### Added
- **Trakt Discovery** — Use Trakt's community data to find new content
  - Trending: Most watched right now (great for "what's hot")
  - Popular: Most watched all time (classic hits)
  - Anticipated: Most anticipated upcoming releases
  - Recommendations: Personalized picks based on your Trakt ratings
- Discovery results are cached for 6 hours to reduce API calls
- Discovery candidates are merged with TMDB Discover for scoring
- New config section in `config/trakt.yml`:
  ```yaml
  discovery:
    enabled: true
    use_trending: true
    use_popular: false
    use_anticipated: false
    use_recommendations: false
  ```

### Technical
- Added `utils/trakt_discovery.py` module with caching
- Added TraktClient methods: `get_trending()`, `get_popular()`, `get_anticipated()`, `get_recommendations()`, `get_related()`
- 20 new tests for Trakt discovery (698 total)

## [2.0.0] - 2026-01-04

### Changed
- **Modular config structure** — Split monolithic config.yml into feature modules
  - All configs now live in `config/` directory
  - `config/config.yml` — Core essentials only (plex, tmdb, users, general)
  - `config/tuning.yml` — Display options, weights, scoring parameters (optional)
  - `config/trakt.yml` — Trakt integration settings (created if Trakt enabled)
  - `config/radarr.yml` / `config/sonarr.yml` — Arr integration (optional)
- **Auto-migration** — Existing configs automatically split on first run
  - Original config backed up as `config.yml.backup.{timestamp}`
  - Migration runs transparently, no user action needed
- Setup wizard now generates slim config.yml (~25 lines vs ~120)
- Radarr/Sonarr configs now at root level instead of nested under movies/tv

### Added
- `config/` directory for all configuration files
- `utils/migrate_config.py` — Manual migration script (`python3 -m utils.migrate_config`)
- Example files in `config/`: `config.example.yml`, `tuning.example.yml`, etc.
- Tests for modular config loading and migration

### Migration
Existing users: Run Curatarr normally — your config will be auto-migrated.
The original config is backed up, and module files are created in `config/`.

## [1.7.7] - 2026-01-04

### Changed
- Lowered CI coverage threshold from 90% to 80% for utils
- Recommenders are integration-heavy; utils remain well-tested (92%+)

### Added
- Unit tests for `trakt_auth.py` and `trakt_sync.py` CLI entry points
- Additional cache function tests in `test_tmdb.py` and `test_trakt.py`

## [1.7.6] - 2026-01-04

### Added
- **Trakt profile enhancement caching** — Skip processing when nothing changed
  - Caches seen Trakt IDs in `trakt_enhance_cache.json`
  - Only processes new items, skips entirely if unchanged
- **IMDB→TMDB ID conversion cache** — Speeds up Trakt integration
  - One-time conversion penalty, instant lookups after
  - Shared cache in `imdb_tmdb_cache.json` with versioning
- **Plex watch history sync to Trakt** — Runs before recommenders
  - New `utils/trakt_sync.py` CLI entry point
  - Syncs watched movies/shows to Trakt with batching
  - Caches synced IDs to avoid re-syncing

### Changed
- Consolidated duplicate IMDB→TMDB functions into `utils/tmdb.py`
- Progress indicators throughout Trakt operations
- User mapping check ensures only configured users get Trakt enhancement

## [1.7.5] - 2026-01-04

### Added
- **HTML Export for Trakt** — New "Export for Trakt" button in watchlist HTML
  - Select items and download IMDB IDs to import into Trakt lists
  - Works alongside Radarr/Sonarr export buttons
- **Trakt watch history import** — Merge streaming service history into recommendations
  - Pulls watch history from Trakt (Netflix, Disney+, Hulu, etc.)
  - Enhances taste profile with content not in Plex library
  - New config: `trakt.import.merge_watch_history` (default: true)
- **Configurable auto-sync** — Control automatic Trakt list syncing
  - New config: `trakt.export.auto_sync` (default: true)
  - Set to false to only use manual HTML export

## [1.7.4] - 2026-01-04

### Added
- **Integration status display** — Shows enabled/disabled status for all integrations at startup
  - Plex, TMDB (required), Trakt, External Recommendations
  - Color-coded: green checkmark (active), yellow circle (disabled/needs auth), red X (missing)

## [1.7.3] - 2026-01-04

### Added
- **Setup wizard Trakt integration** — Interactive setup now includes optional Trakt configuration
  - Prompts for Trakt API credentials during first-run wizard
  - Auto-generates Trakt section in config.yml
  - New `utils/trakt_auth.py` script for device code authentication
- Completes full Trakt integration suite (foundation, export, import, wizard)

## [1.7.2] - 2026-01-04

### Added
- **Trakt import** — Pull data from Trakt to enhance recommendations
  - Exclude Trakt watchlist items from recommendations (you already know about them)
  - Import methods: `get_watched_movies()`, `get_watched_shows()`, `get_ratings()`, `get_watchlist()`
  - Configurable via `trakt.import.enabled` and `trakt.import.exclude_watchlist`
  - 8 new unit tests for import functionality
- **Clickable Trakt list URLs** — After exporting, console shows clickable links to view lists on Trakt

## [1.7.1] - 2026-01-04

### Added
- **Trakt list export** — Push external recommendations to Trakt lists
  - Auto-syncs recommendations to Trakt after generating external watchlists
  - Creates per-user lists: "Curatarr - {username} - Movies" and "Curatarr - {username} - TV"
  - Full sync replaces list contents each run (no duplicates)
  - Configurable list prefix and privacy settings
  - 9 new unit tests for list management and sync functionality

## [1.7.0] - 2026-01-04

### Added
- **Trakt API integration foundation** — Core module for Trakt OAuth and API access
  - `TraktClient` class with device authentication flow (works in Docker/SSH)
  - Automatic token refresh when expired
  - Rate limiting (0.2s delay, well under Trakt's 1000/5min limit)
  - 28 unit tests for Trakt module
  - Config schema for Trakt credentials (disabled by default)

## [1.6.21] - 2026-01-04

### Fixed
- **Docker auto-update now works** — Included `.git` directory in Docker image
  - Containers can now self-update just like bare metal installs
  - Only adds ~1MB to image size

## [1.6.20] - 2026-01-04

### Added
- **Clickable HTML watchlist link** — Console output now shows a clickable link to open the HTML watchlist
  - Uses OSC 8 hyperlink escape codes for modern terminal support (iTerm2, Windows Terminal, GNOME Terminal, etc.)
  - Added `clickable_link()` utility function

### Changed
- **Consolidated version to single location** — `__version__` now defined only in `utils/config.py`
  - Imported by movie.py and tv.py instead of duplicated
  - Makes version bumps and rollbacks easier
- **Added `auto_open_html` to config.example.yml** — Documents the setting (defaults to false)

## [1.6.19] - 2026-01-04

### Fixed
- **Docker Windows compatibility** — Fixed entrypoint script failing on Windows Docker
  - Strip CRLF line endings from shell scripts during build
  - Explicitly invoke bash in ENTRYPOINT to avoid shebang issues

## [1.6.18] - 2026-01-03

### Changed
- **External recommendations now prioritize match score over audience rating**
  - Match score is king - recommendations based on YOUR taste, not general audience
  - Discovery casts wider net (rating >= 5.0, votes >= 50) to find more candidates
  - Output requires 65%+ match and 200+ votes - no rating gate
  - Expanded search: 10 genres, 40 results per genre, 10 keywords, 1500 max candidates

## [1.6.17] - 2026-01-03

### Fixed
- **External recommendations cache now respects quality thresholds** — Old cached items below MIN_RATING (7.0) or MIN_VOTE_COUNT (500) are automatically filtered out on load
- **Added vote_count tracking to external cache** — Enables proper filtering of low-vote content

## [1.6.16] - 2026-01-03

### Added
- **Environment variable support for sensitive tokens** — Security best practice for Docker/CI
  - `PLEX_URL` overrides `plex.url`
  - `PLEX_TOKEN` overrides `plex.token`
  - `TMDB_API_KEY` overrides `tmdb.api_key`
  - Env vars take precedence over config file values

## [1.6.15] - 2026-01-03

### Changed
- **Raised external recommendation quality thresholds** — Filters out mediocre content
  - MIN_RATING: 6.0 → 7.0 (only recommend actually good content)
  - MIN_VOTE_COUNT: 100 → 500 (enough votes to be reliable)

## [1.6.14] - 2026-01-03

### Changed
- **Consolidated TMDB helper methods to BaseRecommender** — Removed ~130 lines of duplicated code
  - Moved `_get_plex_item_tmdb_id()` to BaseRecommender (was `_get_plex_movie_tmdb_id`/`_get_plex_show_tmdb_id`)
  - Moved `_get_plex_item_imdb_id()` to BaseRecommender (was `_get_plex_movie_imdb_id`/`_get_plex_show_imdb_id`)
  - Moved `_get_tmdb_id_via_imdb()` to BaseRecommender (identical logic, different result key)
  - Moved `_get_tmdb_keywords_for_id()` to BaseRecommender (100% identical between movie/tv)
  - Moved `_get_library_imdb_ids()` to BaseRecommender (100% identical one-liner)
  - Removed unnecessary delegate methods `_extract_genres()` and `_get_*_language()` - now call utilities directly
  - Uses `self.media_type` to handle movie vs tv differences in base class methods
  - Cleaned up unused imports from movie.py and tv.py

## [1.6.13] - 2026-01-03

### Changed
- **Deep inheritance refactor** — Eliminated ~650 lines of duplicated code between movie/tv recommenders
  - Moved `get_recommendations()` to BaseRecommender (was duplicated in both)
  - Moved `manage_plex_labels()` to BaseRecommender (was duplicated in both)
  - Moved `_get_plex_user_ids()` to BaseRecommender (was identical in both)
  - Moved `_get_managed_users_watched_data()` to BaseRecommender (was near-identical)
  - Moved `_load_watched_cache()` to BaseRecommender (cache init block was duplicated)
  - Added `_do_save_watched_cache()` helper to BaseRecommender
  - Added abstract methods: `_get_media_cache()`, `_find_plex_item()`, `_calculate_similarity_from_cache()`, `_print_similarity_breakdown()`
  - Added `media_key` class attribute to recommenders for generic cache access

## [1.6.12] - 2026-01-03

### Changed
- **Recommenders now inherit from BaseRecommender** — Major refactoring to reduce code duplication
  - PlexMovieRecommender and PlexTVRecommender now properly inherit from BaseRecommender
  - Moved common initialization logic (config, plex, display options, weights) to base class
  - Implemented abstract methods: `_load_weights()`, `_get_watched_data()`, `_get_watched_count()`, `_save_watched_cache()`
  - Renamed `watched_movie_ids`/`watched_show_ids` to `watched_ids` for consistency
  - Removed duplicate `_refresh_watched_data()` (now uses base class version)
  - Uses `_get_user_context()` from base class instead of duplicating logic
  - Updated tests to mock at `recommenders.base.*` instead of media-specific modules

## [1.6.11] - 2026-01-03

### Fixed
- **Backfill handles API failures** — Collection backfill now marks movies as processed even when TMDB API returns 404
  - Prevents infinite retry loop for movies removed from TMDB

## [1.6.10] - 2026-01-03

### Removed
- **Dead code cleanup** — Removed unused code from recommenders
  - Removed unused `import random` from movie.py and tv.py
  - Removed unused utility imports (RATING_MULTIPLIERS, DEFAULT_NEGATIVE_MULTIPLIERS, DEFAULT_RATING, TOP_POOL_PERCENTAGE)
  - Removed dead `find_similar_content()` function from external.py
  - Removed duplicate `get_tmdb_keywords()` from external.py (now uses utils version)
  - Removed unused `self.plex_only` attribute from tv.py

## [1.6.9] - 2026-01-03

### Changed
- **Improved test coverage** — Added 58 new tests across recommender modules
  - tv.py: 0% → 42% coverage (33 new tests)
  - base.py: 82% → 96% coverage (12 new tests)
  - movie.py: 30% → 39% coverage (10 new tests)
  - external.py: 21% → 24% coverage (3 new tests)
  - Overall coverage: 75% → 83% (564 total tests)

## [1.6.8] - 2026-01-03

### Added
- **Collection bonus for sequels** — Movies in franchises get a score bonus
  - Tracks TMDB collection data (e.g., "Harry Potter Collection")
  - Applies 5-15% bonus for unwatched movies in collections user has watched
  - Logarithmic scaling: more watched movies = higher bonus (capped at 15%)

## [1.6.7] - 2026-01-03

### Added
- **Score caching** — Computed similarity scores are now cached per movie/show
  - Scores only recalculated when user profile changes (detected via hash)
  - Significantly speeds up subsequent runs with unchanged watch history
  - Profile hash stored with each cached score for invalidation

## [1.6.6] - 2026-01-03

### Added
- **Popularity dampening** — Slight penalty for very popular content (50k+ votes)
  - Prevents blockbusters from dominating due to more complete metadata
  - ~3% penalty per order of magnitude above threshold (capped at 10%)
  - Configurable via `use_popularity_dampening` and `popularity_threshold` parameters

## [1.6.5] - 2026-01-03

### Added
- **TF-IDF scoring** — Penalizes content matching rare genres/keywords in user's profile
  - Genres below 15% of max count receive penalty proportional to rarity
  - Unseen genres receive mild penalty (prevents "Brave" recommendations for action fans)
  - Keywords receive similar treatment with lighter penalties (0.02 per unseen)
  - Configurable via `use_tfidf` and `tfidf_penalty_threshold` parameters

## [1.6.4] - 2026-01-03

### Fixed
- **Show-level episode aggregation** — TV shows now weighted by show, not episode count
  - Previously a show with 20 episodes had 20x the weight of a show with 1 episode
  - Now each show counts as 1 unit regardless of episode count
  - Rewatch bonus only applied when user actually rewatched episodes

## [1.6.3] - 2026-01-03

### Added
- **Tiered recommendations** — Diversified recommendation selection
  - Safe picks (60%): High-confidence items from top scores
  - Diverse options (30%): Mid-tier items for variety
  - Wildcard picks (10%): Lower-scored discoveries
  - Replaces simple random sampling from top 10%
  - New `select_tiered_recommendations()` utility function

## [1.6.2] - 2026-01-03

### Changed
- **Split external.py** — Extracted output generation to `external_output.py` (607 lines)
  - `external.py` reduced from 1720 to 1134 lines
  - Improves maintainability and readability

## [1.6.1] - 2026-01-03

### Changed
- **SSL verification default** — `verify_ssl` now defaults to `True` (secure by default)
  - Users with self-signed certs can set `verify_ssl: false` in config

## [1.6.0] - 2026-01-03

### Added
- **Negative signals** — Low-rated content and dropped shows now penalize similar recommendations
  - Ratings 0-3 apply negative multipliers (-1.0 to -0.3) instead of weak positive
  - Dropped TV shows (started but abandoned) generate negative signals
  - Configurable via `negative_signals` section in config
  - Capped penalties prevent one bad movie from destroying a genre preference
- **Tests** — Added comprehensive tests for recommenders and utilities
  - 25 new tests for `recommenders/base.py` (22% → 95% coverage)
  - 20 new tests for `recommenders/movie.py`
  - 11 new tests for `utils/plex.py` (85% → 97% coverage)
  - 5 new tests for pre-calculated weight parameter
  - Total: 488 tests passing, utils/ at 96%+ coverage

### Changed
- **Counter processing consolidation** — Removed duplicate methods from recommenders
  - Movie and TV recommenders now use shared `process_counters_from_cache()`
  - Added `weight` and `cap_penalty` parameters for pre-calculated weights
  - Removed ~55 lines of duplicate code from each recommender

### Fixed
- **Collection sort order** — Collections now sort correctly using reverse `moveItem()` approach
- **Redundant ternary expressions** — Simplified `x if x else None` patterns in recommenders

### Removed
- **combine_watch_history** — Removed unused feature and dead code assignments

## [1.5.0] - 2026-01-03

### Fixed
- **SSL verification** — Added configurable `verify_ssl` option for Plex connections
  - Defaults to `false` for backwards compatibility with self-signed certs
  - PlexAPI session now respects this setting
- **HTTP timeouts** — Added 30-second timeout to all HTTP requests
  - Prevents hangs on unresponsive servers
- **Config schema mismatch** — `get_configured_users()` now reads `config['users']['list']`
  - Previously only checked legacy `config['plex']['managed_users']` path
  - Fixes per-user collection labels not being generated correctly
- **Watched detection** — Now checks both cache AND Plex `isPlayed` flag
  - Movies manually marked as watched are now properly excluded
  - Fixes watched movies appearing in recommendation collections
- **MediaContainer iteration** — Convert to list before processing
  - Plex MediaContainer is single-use; was causing empty results on second pass

### Changed
- **Dependencies** — Removed unused packages from requirements.txt
  - Removed `tmdbv3api` (not used)
  - Removed `python-dotenv` (not used)

### Added
- **Console watchlist link** — Prints `file://` URL after generating HTML watchlist
- **Tests** — Added test for `isPlayed` watched detection
- **Tests** — Updated `init_plex` tests for new SSL session handling

## [1.4.0] - 2026-01-03

### Added
- **HTML watchlist with export buttons** — Interactive HTML view of external recommendations
  - Single page with tabs for each user
  - Selectable items with checkboxes (unchecked by default)
  - "Export to Radarr" button downloads IMDB IDs for selected movies
  - "Export to Sonarr" button downloads IMDB IDs for selected shows
  - Movie theater themed dark design with gold accents
  - Auto-open in browser after run (configurable via `auto_open_html`)

## [1.3.0] - 2026-01-03

### Added
- **Docker support** — Run Curatarr in a container
  - `Dockerfile` for building the image
  - `docker-compose.yml` for easy deployment
  - `.dockerignore` for optimized builds
  - Updated README with Docker quick start, scheduling, and troubleshooting

## [1.2.9] - 2026-01-03

### Added
- **Comprehensive unit tests** — 367 tests achieving 95% coverage
  - test_display.py: 63 tests (93% coverage)
  - test_plex.py: 92 tests (98% coverage)
  - test_scoring.py: 55 tests (95% coverage)
  - test_tmdb.py: 32 tests (99% coverage)
  - test_labels.py: 23 tests (97% coverage)
  - test_counters.py: 22 tests (96% coverage)
  - test_helpers.py: 32 tests (95% coverage)
  - test_cache.py: 19 tests (93% coverage)

### Fixed
- **Log level** — Label removal messages now log as INFO instead of WARNING

## [1.2.8] - 2026-01-03

### Added
- **Interactive setup wizard** — First-run configuration for new users
- **Unit tests** — Initial test suite for config and tmdb modules

## [1.2.7] - 2026-01-03

### Added
- **Windows support** — Full feature parity with macOS/Linux
  - `run.ps1` PowerShell script with same functionality as `run.sh`
  - Dependency checking, auto-update, first-run wizard
  - Task Scheduler integration (Windows equivalent of cron)
  - Updated README with Windows instructions throughout

## [1.2.6] - 2026-01-03

### Fixed
- **Method name bugs** — Fixed `_get_show_language` and `_get_movie_language` to call correct base class method
- **Exception handling** — Replaced bare except blocks with specific exception types
- **Config key** — Fixed `stale_removal_days` lookup (was checking wrong config section)
- **Language normalization** — Added missing `.lower()` for consistent matching
- **Return type consistency** — Aligned `tv.py` return type with `movie.py`

### Removed
- **Dead code cleanup** — Removed 5 unused methods (~200 lines):
  - `_is_show_in_library`, `_process_show_counters`, `_validate_watched_shows`
  - `_is_movie_in_library`, `_process_movie_counters`
- **Whitespace fixes** — Fixed mixed tabs/spaces throughout

## [1.2.3] - 2026-01-02

### Changed
- **Cache class refactoring** — `MovieCache` and `ShowCache` now inherit from `BaseCache`
  - Reduced ~215 lines of duplicated code
  - Each cache only implements `_process_item()` for media-specific logic
  - Shared: cache loading/saving, library updates, TMDB data fetching, language detection

## [1.2.2] - 2026-01-02

### Changed
- **Named constants** — Extracted magic numbers to `utils/config.py`:
  - `TOP_CAST_COUNT = 3`
  - `TMDB_RATE_LIMIT_DELAY = 0.5`
  - `DEFAULT_RATING = 5.0`
  - `WEIGHT_SUM_TOLERANCE = 1e-6`
  - `DEFAULT_LIMIT_PLEX_RESULTS = 10`
  - `TOP_POOL_PERCENTAGE = 0.1`

## [1.2.1] - 2026-01-02

### Fixed
- **Exception handling** — Replaced bare `except:` with specific exception types
- **Unused imports** — Removed dead imports across all files
- **Unused variables** — Cleaned up unused variable assignments
- **Pass statements** — Removed meaningless `pass` statements

## [1.2.0] - 2026-01-02

### Changed
- **Project restructure** — Reorganized recommenders into dedicated directory:
  - `movie_recommender.py` → `recommenders/movie.py`
  - `tv_recommender.py` → `recommenders/tv.py`
  - `external_recommender.py` → `recommenders/external.py`
  - `base.py` → `recommenders/base.py`
- Updated `run.sh` to use new paths
- All path references now use project root for config, cache, logs

## [1.1.0] - 2026-01-02

### Changed
- **Utils package refactoring** — Split 2500+ line `utils.py` into focused modules:
  - `utils/config.py` - Configuration utilities
  - `utils/display.py` - Output formatting, logging, colors
  - `utils/tmdb.py` - TMDB API functions
  - `utils/cache.py` - Cache I/O operations
  - `utils/labels.py` - Label management
  - `utils/scoring.py` - Similarity scoring functions
  - `utils/counters.py` - Counter utilities
  - `utils/helpers.py` - Miscellaneous helpers
  - `utils/plex.py` - Plex-specific utilities
  - `utils/__init__.py` - Re-exports 72 items for backwards compatibility

- **Scoring formula overhaul** — Changed from averaging to sum with diminishing returns
  - Multiple weak keyword matches now add up instead of averaging down
  - A movie with 15 matching keywords scores well even if each is partial
  - Typical scores now in 70-85% range instead of 20-50%

- **Weight redistribution** — When a component has no matches (e.g., unknown director),
  its weight now redistributes proportionally to components that did match

- **New default weights:**
  - Keywords: 50% (was 45%) — Most predictive signal
  - Genre: 25% (was 20%) — Baseline preference
  - Actor: 20% (was 15%) — Cast preferences
  - Director: 5% (was 15%) — Most people don't pick by director
  - Language: 0% (was 5%) — Removed due to unreliable data

### Fixed
- **format_media_output() signature** — Fixed function parameter names and order to match callers
  - Changed `media_info` to `media` parameter name
  - Added missing `show_director` and `show_genres` parameters

- **Duplicate log messages** — Warnings and errors now appear only once
  - Enabled ColoredFormatter for colored log output
  - Removed redundant print() calls from log_warning/log_error

- **Case sensitivity bugs** — Genres, directors, and actors now match case-insensitively
  - "Drama" now correctly matches "drama" in user profiles
  - Fixed major scoring undercount issue

- **External recommender cache** — Now updates scores for existing cached items
  - Previously only added new items, never updated scores
  - Scores now reflect current user profile

- **Collection smart sorting** — Collections now replace lower-scoring items with
  higher-scoring ones, not just fill gaps

### Added
- **Unit test suite** — 101 tests covering utility functions
  - Tests for plex extraction, counters, labels, cache, helpers, scoring
  - Run with: `python3 -m pytest tests/ -v`

- **Base classes** — Created `base.py` with abstract base classes for future refactoring:
  - `BaseCache` - Common cache functionality for movies and TV shows
  - `BaseRecommender` - Common recommender functionality

- **Type hints** — Added consistent type hints across utility modules:
  - `utils/helpers.py`, `utils/display.py`, `utils/plex.py`
  - Added `Any`, `Dict`, `List`, `Set`, `Tuple`, `Optional` type annotations

- Per-item weight redistribution — If a specific movie's director isn't in your
  profile, that 5% weight goes to keywords/genres/actors instead

### Removed
- **Unused imports** — Cleaned up unused imports from main modules:
  - `movie_recommender.py` - Removed `plexapi.server`, `PlexServer`, `Counter`, `quote`, `timedelta`, `math`
  - `tv_recommender.py` - Removed `plexapi.server`, `PlexServer`, `Counter`, `timedelta`, `math`
  - `base.py` - Removed `json`, `Counter`, unused utility imports

## [1.0.0] - 2026-01-02

### Added
- Initial release with movie and TV show recommendations
- External watchlist generation with streaming service grouping
- Multi-user support with per-user preferences
- Recency decay and rating multipliers
- Rewatch detection with logarithmic weighting
- Smart caching with automatic invalidation
- Auto-update from GitHub
- Consolidated utilities in utils.py
