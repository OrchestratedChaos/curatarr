# Plex Recommender - Future Improvements

## Architectural Refactoring

### 1. Split Utils into Package
**Priority:** High
**Effort:** Medium
**Files:** `utils.py` → `utils/` directory

Split the 2500+ line `utils.py` into focused modules:

```
utils/
├── __init__.py          # Re-exports for backwards compatibility
├── tmdb.py              # TMDB API functions
├── plex.py              # Plex-specific utilities
├── cache.py             # Cache I/O operations
├── labels.py            # Label management
├── display.py           # Output formatting & user prompts
├── counters.py          # Counter utilities
└── config.py            # Config utilities
```

Module breakdown:
- [ ] `tmdb.py` - `fetch_tmdb_with_retry`, `get_tmdb_id_for_item`, `get_tmdb_keywords`, `get_tmdb_config`
- [ ] `plex.py` - `get_library_imdb_ids`, `extract_genres`, `extract_ids_from_guids`, `get_plex_user_ids`, `extract_rating`
- [ ] `cache.py` - `load_media_cache`, `save_media_cache`, `save_watched_cache`
- [ ] `labels.py` - `build_label_name`, `categorize_labeled_items`, `remove_labels_from_items`, `add_labels_to_items`
- [ ] `display.py` - `format_media_output`, `print_similarity_breakdown`, `user_select_recommendations`
- [ ] `counters.py` - `create_empty_counters`, `process_counters_from_cache`
- [ ] `config.py` - `adapt_config_for_media_type`, config loading utilities
- [ ] `__init__.py` - Re-export all public functions for `from utils import ...` compatibility

### 2. Base Cache Class
**Priority:** Medium
**Effort:** Medium
**Files:** `movie_recommender.py`, `tv_recommender.py`

Create a `BaseCache` class for `MovieCache` and `ShowCache`:
- [ ] `update_cache()` - similar structure, different media processing
- [ ] `_get_*_language()` - similar audio stream extraction

### 3. Base Recommender Class
**Priority:** Medium
**Effort:** Large
**Files:** `movie_recommender.py`, `tv_recommender.py`

Create a `BaseRecommender` class to eliminate remaining duplicate methods:
- [ ] `_get_managed_users_watched_data()` - 63 lines duplicated
- [ ] `_get_plex_watched_data()` / `_get_plex_watched_shows_data()` - similar patterns
- [ ] `_refresh_watched_data()` - 16-17 lines each
- [ ] `_get_watched_count()` - identical logic
- [ ] `get_recommendations()` - could share common structure
- [ ] `process_recommendations()` - could share display logic
- [ ] `main()` function - single user handling, user iteration, config loading
- [ ] `weights` loading - key name handling (was buggy in both files)

```python
# Proposed structure:
class BaseRecommender:
    def _get_managed_users_watched_data(self, media_type): ...
    def _refresh_watched_data(self): ...

class MovieRecommender(BaseRecommender):
    media_type = 'movie'

class TVRecommender(BaseRecommender):
    media_type = 'tv'
```

---

## Code Quality

### 4. Remove Unused Imports
**Priority:** Low
**Effort:** Small

After consolidation, some imports may no longer be needed:
- [ ] Check `copy` usage after `save_watched_cache` consolidation
- [ ] Check `json` usage after cache utilities
- [ ] Check `Counter` imports

### 5. Type Hints Consistency
**Priority:** Low
**Effort:** Medium

- [ ] Add type hints to all utility functions in `utils.py`
- [ ] Ensure consistent `Optional`, `List`, `Dict` usage

---

## Testing

### 6. Unit Tests for Utilities
**Priority:** High
**Effort:** Medium
**Files:** New `tests/` directory

- [ ] Test `extract_genres()`
- [ ] Test `extract_ids_from_guids()`
- [ ] Test `fetch_tmdb_with_retry()` with mocked responses
- [ ] Test `create_empty_counters()`
- [ ] Test `build_label_name()`
- [ ] Test `categorize_labeled_items()`
- [ ] Test `save_watched_cache()` / `load_media_cache()`

---

## Completed Previously

- [x] Consolidated TMDB API calls (`fetch_tmdb_with_retry`, `get_tmdb_id_for_item`, `get_tmdb_keywords`)
- [x] Consolidated GUID extraction (`extract_ids_from_guids`)
- [x] Consolidated genre extraction (`extract_genres`)
- [x] Consolidated config adapter (`adapt_config_for_media_type`)
- [x] Consolidated user selection (`user_select_recommendations`)
- [x] Consolidated rating extraction (`extract_rating`)
- [x] Consolidated output formatting (`format_media_output`)
- [x] Consolidated label management (`build_label_name`, `categorize_labeled_items`, `remove_labels_from_items`, `add_labels_to_items`)
- [x] Consolidated similarity breakdown (`print_similarity_breakdown`)
- [x] Consolidated library IMDB IDs (`get_library_imdb_ids`)
- [x] Consolidated cache I/O (`load_media_cache`, `save_media_cache`, `save_watched_cache`)
- [x] Consolidated counter initialization (`create_empty_counters`)
- [x] Fixed TMDB config case-insensitive access (`get_tmdb_config`)

**Lines saved:** ~400+ lines of duplicate code eliminated

## Completed 2026-01-02 (Scoring Overhaul)

- [x] Fixed case sensitivity in `normalize_genre()` - now returns lowercase
- [x] Fixed case sensitivity in director/actor matching - added lowercase lookups
- [x] Changed scoring from averaging to sum with diminishing returns
- [x] Added per-item weight redistribution for 0-scoring components
- [x] Removed language weight (data unreliable)
- [x] Reduced director weight from 15% to 5%
- [x] Increased keyword weight from 45% to 50%
- [x] Increased actor weight from 15% to 20%
- [x] Increased genre weight from 20% to 25%
- [x] Fixed external recommender to update cached item scores
- [x] Fixed collection smart sorting to replace lower-scoring items
- [x] Updated README.md with new weights and scoring explanation
- [x] Created CHANGELOG.md

**Result:** Scores now in 70-85% range instead of 20-50%
