"""
Counter utilities for Plex Recommender.
Handles preference counting and profile building.
"""

from collections import Counter
from typing import Dict

from .display import log_warning
from .scoring import calculate_recency_multiplier, calculate_rewatch_multiplier
from .config import get_rating_multipliers


def create_empty_counters(media_type: str = 'movie') -> Dict:
    """
    Create empty counter structure for tracking watched media preferences.

    Args:
        media_type: 'movie' or 'tv'

    Returns:
        Dictionary with Counter objects for each category
    """
    counters = {
        'genres': Counter(),
        'actors': Counter(),
        'languages': Counter(),
        'tmdb_keywords': Counter(),
        'tmdb_ids': set()
    }
    # Movies use directors, TV uses studio
    if media_type == 'movie':
        counters['directors'] = Counter()
    else:
        counters['studio'] = Counter()
    return counters


def process_counters_from_cache(
    media_info: Dict,
    counters: Dict,
    view_count: int = 1,
    viewed_at: int = None,
    rating: float = None,
    recency_config: dict = None,
    rating_multipliers: dict = None,
    media_type: str = 'movie'
) -> None:
    """
    Update counters from cached media information.

    Args:
        media_info: Dict with cached media metadata
        counters: Counter dict to update
        view_count: Number of times item was viewed
        viewed_at: Timestamp when viewed
        rating: User rating (0-10 scale)
        recency_config: Recency decay configuration
        rating_multipliers: Rating-to-weight mappings
        media_type: 'movie' or 'tv'
    """
    try:
        # Calculate weight multipliers
        recency_mult = 1.0
        if viewed_at and recency_config:
            recency_mult = calculate_recency_multiplier(viewed_at, recency_config)

        rewatch_mult = calculate_rewatch_multiplier(view_count)

        rating_mult = 1.0
        if rating is not None:
            multipliers = rating_multipliers or get_rating_multipliers()
            rating_int = int(round(rating))
            rating_mult = multipliers.get(rating_int, 1.0)

        total_weight = recency_mult * rewatch_mult * rating_mult

        # Update genre counters
        genres = media_info.get('genres', [])
        for genre in genres:
            if genre:
                counters['genres'][genre.lower()] += total_weight

        # Update actor counters
        actors = media_info.get('actors', media_info.get('cast', []))
        for actor in actors:
            if actor:
                counters['actors'][actor] += total_weight

        # Update director/studio counters
        if media_type == 'movie':
            directors = media_info.get('directors', [])
            if isinstance(directors, str):
                directors = [directors]
            for director in directors:
                if director:
                    counters['directors'][director] += total_weight
        else:
            studio = media_info.get('studio', '')
            if studio:
                if 'studio' not in counters:
                    counters['studio'] = Counter()
                counters['studio'][studio.lower()] += total_weight

        # Update language counters
        language = media_info.get('language', '')
        if language and language != 'N/A':
            counters['languages'][language.lower()] += total_weight

        # Update keyword counters
        keywords = media_info.get('tmdb_keywords', [])
        for kw in keywords:
            if kw:
                counters['tmdb_keywords'][kw.lower()] += total_weight

        # Track TMDB ID
        tmdb_id = media_info.get('tmdb_id')
        if tmdb_id:
            counters['tmdb_ids'].add(tmdb_id)

    except Exception as e:
        log_warning(f"Error processing counters for {media_info.get('title')}: {e}")
