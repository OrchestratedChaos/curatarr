# Changelog

All notable changes to Plex Recommender will be documented in this file.

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
- **Docker support** — Run Plex Recommender in a container
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
