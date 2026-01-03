# Changelog

All notable changes to Plex Recommender will be documented in this file.

## [Unreleased]

### Changed
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
- **Case sensitivity bugs** — Genres, directors, and actors now match case-insensitively
  - "Drama" now correctly matches "drama" in user profiles
  - Fixed major scoring undercount issue

- **External recommender cache** — Now updates scores for existing cached items
  - Previously only added new items, never updated scores
  - Scores now reflect current user profile

- **Collection smart sorting** — Collections now replace lower-scoring items with
  higher-scoring ones, not just fill gaps

### Added
- Per-item weight redistribution — If a specific movie's director isn't in your
  profile, that 5% weight goes to keywords/genres/actors instead

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
