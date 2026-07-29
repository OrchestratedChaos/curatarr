"""
Counter utilities for Curatarr.
Handles preference counting and profile building.
"""

from collections import Counter
from typing import Any, Dict, Optional

from .config import DEFAULT_NEGATIVE_THRESHOLD, get_negative_multiplier, get_rating_multipliers
from .display import log_warning
from .scoring import calculate_recency_multiplier, calculate_rewatch_multiplier


def create_empty_counters(media_type: str = "movie") -> Dict:
    """
    Create empty counter structure for tracking watched media preferences.

    Args:
        media_type: 'movie' or 'tv'

    Returns:
        Dictionary with Counter objects for each category
    """
    counters: Dict[str, Any] = {
        "genres": Counter(),
        "actors": Counter(),
        "languages": Counter(),
        "tmdb_keywords": Counter(),
        "tmdb_ids": set(),
    }
    # Movies use directors and collections, TV uses studios
    if media_type == "movie":
        counters["directors"] = Counter()
        counters["collections"] = Counter()  # Track TMDB collection IDs for sequel bonus
    else:
        counters["studios"] = Counter()
    return counters


def build_profile_from_counters(watched_data_counters: Optional[Dict]) -> Dict:
    """
    Turn stored `watched_data_counters` into the scoring-profile dict.

    #317: THE single translation between the storage shape (what
    create_empty_counters() above builds and the watched-cache files
    persist) and the profile shape every scoring consumer expects. It
    replaces four separate implementations that had drifted apart:

      - recommenders/movie.py's `_calculate_similarity_from_cache`
        (genres/directors/actors/languages/keywords, plain dicts)
      - recommenders/tv.py's `_calculate_similarity_from_cache`
        (same, but studios instead of directors)
      - recommenders/external.py's `load_user_profile_from_cache`
        (Counter-valued, both directors AND studios, plus tmdb_ids)
      - recommenders/external.py's `_build_profile_via_recommender`
        (byte-identical to the one above it)

    The one rename that has to happen here and is easy to get wrong:
    storage calls it **`tmdb_keywords`**, scoring calls it
    **`keywords`**. Every one of the four builders did that rename by
    hand; doing it once removes the chance of a fifth caller forgetting
    and silently scoring with an empty keyword dimension.

    Emits the full key set regardless of media type - deliberately.
    `utils.scoring._redistribute_weights()` gates `has_directors` on
    `media_type == "movie"` and `has_studios` on `media_type == "tv"`,
    so the irrelevant one is never read and carrying it costs nothing;
    gating here instead would mean every caller has to pass a media_type
    it otherwise doesn't need. Values are Counters (matching the two
    external.py builders); `calculate_similarity_score()` re-wraps
    whatever it gets in `Counter()` anyway, so the plain-dict callers
    are unaffected by the change.

    Args:
        watched_data_counters: The stored counters dict, or None

    Returns:
        Profile dict keyed as scoring expects
    """
    wdc = watched_data_counters or {}
    return {
        "genres": Counter(wdc.get("genres", {})),
        "directors": Counter(wdc.get("directors", {})),
        "studios": Counter(wdc.get("studios", {})),
        "actors": Counter(wdc.get("actors", {})),
        "keywords": Counter(wdc.get("tmdb_keywords", {})),
        "languages": Counter(wdc.get("languages", {})),
        "tmdb_ids": set(wdc.get("tmdb_ids", [])),
    }


def _apply_capped_weight(counter: Dict[str, float], key: str, weight: float, cap_penalty: float = 0.5) -> None:
    """
    Apply weight to counter with optional capping for negative values.

    Prevents negative signals from completely destroying a preference.
    Example: If user loves "action" (+10) and hates one action movie,
    the negative signal (-1) shouldn't reduce action below cap_penalty * max_positive.

    Args:
        counter: Counter object to update (typed Dict[str, float] - a
            real Counter satisfies this structurally, and its values are
            floats in practice, not the int typeshed's Counter stub assumes)
        key: Key to update in counter
        weight: Weight to add (can be negative)
        cap_penalty: For negative weights, don't reduce below this fraction of current value
    """
    if weight >= 0:
        # Positive weight - just add it
        counter[key] += weight
    else:
        # Negative weight - apply with capping
        current = counter[key]
        if current > 0:
            # Don't let negative push below cap * current positive
            floor = current * cap_penalty
            new_val = current + weight  # weight is negative
            counter[key] = max(new_val, floor)
        else:
            # Already zero or negative, just add
            counter[key] += weight


def process_counters_from_cache(
    media_info: Dict,
    counters: Dict,
    view_count: int = 1,
    viewed_at: Optional[int] = None,
    rating: Optional[float] = None,
    recency_config: Optional[dict] = None,
    rating_multipliers: Optional[dict] = None,
    media_type: str = "movie",
    negative_signals_config: Optional[dict] = None,
    weight: Optional[float] = None,
    cap_penalty: float = 0.5,
) -> bool:
    """
    Update counters from cached media information.

    Supports negative signals: low-rated content (ratings <= threshold) will
    subtract from preference counters instead of adding to them.

    Args:
        media_info: Dict with cached media metadata
        counters: Counter dict to update
        view_count: Number of times item was viewed
        viewed_at: Timestamp when viewed
        rating: User rating (0-10 scale)
        recency_config: Recency decay configuration
        rating_multipliers: Rating-to-weight mappings
        media_type: 'movie' or 'tv'
        negative_signals_config: Config for negative signals (threshold, cap_penalty, enabled)
        weight: Pre-calculated weight (if provided, skips internal weight calculation)
        cap_penalty: For negative weights, don't reduce below this fraction of current value

    Returns:
        True if processed as negative signal, False otherwise
    """
    is_negative = False

    try:
        # Use pre-calculated weight if provided
        if weight is not None:
            total_weight = weight
            is_negative = weight < 0
        else:
            # Calculate weight multipliers
            recency_mult = 1.0
            if viewed_at and recency_config:
                recency_mult = calculate_recency_multiplier(viewed_at, recency_config)

            rewatch_mult = calculate_rewatch_multiplier(view_count)

            rating_mult = 1.0

            if rating is not None:
                rating_int = int(round(rating))

                # Check if this should be a negative signal
                ns_config = negative_signals_config or {}
                bad_ratings_config = ns_config.get("bad_ratings", {})
                ns_enabled = ns_config.get("enabled", True) and bad_ratings_config.get("enabled", True)
                threshold = bad_ratings_config.get("threshold", DEFAULT_NEGATIVE_THRESHOLD)
                cap_penalty = bad_ratings_config.get("cap_penalty", 0.5)

                if ns_enabled and rating_int <= threshold:
                    # Use negative multiplier
                    rating_mult = get_negative_multiplier(rating_int)
                    is_negative = True
                else:
                    # Use positive multiplier
                    multipliers = rating_multipliers or get_rating_multipliers()
                    rating_mult = multipliers.get(rating_int, 1.0)

            # Calculate total weight (always positive magnitude, sign determined by rating_mult)
            total_weight = recency_mult * rewatch_mult * rating_mult

        # Update genre counters
        genres = media_info.get("genres", [])
        for genre in genres:
            if genre:
                _apply_capped_weight(counters["genres"], genre.lower(), total_weight, cap_penalty)

        # Update actor counters
        actors = media_info.get("actors", media_info.get("cast", []))
        for actor in actors:
            if actor:
                _apply_capped_weight(counters["actors"], actor, total_weight, cap_penalty)

        # Update director/studio counters
        if media_type == "movie":
            directors = media_info.get("directors", [])
            if isinstance(directors, str):
                directors = [directors]
            for director in directors:
                if director:
                    _apply_capped_weight(counters["directors"], director, total_weight, cap_penalty)

            # Track movie collections (for sequel bonus)
            collection_id = media_info.get("collection_id")
            if collection_id and "collections" in counters:
                _apply_capped_weight(counters["collections"], collection_id, total_weight, cap_penalty)
        else:
            studio = media_info.get("studio", "")
            if studio:
                if "studios" not in counters:
                    counters["studios"] = Counter()
                _apply_capped_weight(counters["studios"], studio.lower(), total_weight, cap_penalty)

        # Update language counters
        language = media_info.get("language", "")
        if language and language != "N/A":
            _apply_capped_weight(counters["languages"], language.lower(), total_weight, cap_penalty)

        # Update keyword counters
        keywords = media_info.get("tmdb_keywords", [])
        for kw in keywords:
            if kw:
                _apply_capped_weight(counters["tmdb_keywords"], kw.lower(), total_weight, cap_penalty)

        # Track TMDB ID (not affected by negative signals - we still want to exclude from recs)
        tmdb_id = media_info.get("tmdb_id")
        if tmdb_id:
            counters["tmdb_ids"].add(tmdb_id)

    except Exception as e:
        log_warning(f"Error processing counters for {media_info.get('title')}: {e}")

    return is_negative
