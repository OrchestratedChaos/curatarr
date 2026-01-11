"""
Trakt Discovery Module

Fetches and caches discovery data from Trakt:
- Trending (most watched right now)
- Popular (most watched all time)
- Anticipated (most anticipated upcoming)
- Recommendations (personalized based on ratings)

Discovery data is cached to reduce API calls (content doesn't change hourly).
"""

import os
import json
import time
import logging
from typing import Dict, List, Optional, Set, Any

from utils.trakt import TraktClient, get_authenticated_trakt_client, TraktAPIError

logger = logging.getLogger('curatarr')

# Cache duration in seconds (6 hours - trending/popular don't change rapidly)
DISCOVERY_CACHE_TTL = 6 * 60 * 60

# Cache version - bump to invalidate stale cache format
TRAKT_DISCOVERY_CACHE_VERSION = 1

# Default limits for discovery sources
DEFAULT_TRENDING_LIMIT = 50
DEFAULT_POPULAR_LIMIT = 50
DEFAULT_ANTICIPATED_LIMIT = 30
DEFAULT_RECOMMENDATIONS_LIMIT = 30


def _get_cache_path(cache_dir: str, source: str, media_type: str) -> str:
    """Get path for discovery cache file."""
    return os.path.join(cache_dir, f'trakt_{source}_{media_type}.json')


def _load_discovery_cache(cache_dir: str, source: str, media_type: str) -> Optional[Dict]:
    """
    Load discovery cache if it exists, is fresh, and has correct version.

    Returns:
        Cache data dict or None if cache is missing/stale/outdated
    """
    cache_path = _get_cache_path(cache_dir, source, media_type)
    if not os.path.exists(cache_path):
        return None

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache = json.load(f)

        # Check cache version
        cache_version = cache.get('version', 0)
        if cache_version < TRAKT_DISCOVERY_CACHE_VERSION:
            logger.debug(f"Trakt {source} cache outdated (v{cache_version}), invalidating")
            return None

        # Check if cache is still fresh
        cached_at = cache.get('cached_at', 0)
        if time.time() - cached_at > DISCOVERY_CACHE_TTL:
            logger.debug(f"Trakt {source} cache expired for {media_type}")
            return None

        return cache
    except Exception as e:
        logger.warning(f"Failed to load Trakt {source} cache: {e}")
        return None


def _save_discovery_cache(cache_dir: str, source: str, media_type: str, items: List[Dict]):
    """Save discovery results to cache with version."""
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = _get_cache_path(cache_dir, source, media_type)

    try:
        cache = {
            'version': TRAKT_DISCOVERY_CACHE_VERSION,
            'cached_at': time.time(),
            'items': items
        }
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save Trakt {source} cache: {e}")


def _extract_item_ids(item: Dict, media_type: str) -> Dict[str, Any]:
    """
    Extract IDs and basic info from a Trakt API response item.

    Trakt responses vary by endpoint:
    - trending: {watchers: N, movie/show: {...}}
    - popular: {movie/show: {...}} or just {...} directly
    - recommendations: {...} directly
    - anticipated: {list_count: N, movie/show: {...}}

    Returns:
        Dict with tmdb_id, imdb_id, trakt_id, title, year
    """
    media_key = 'movie' if media_type == 'movies' else 'show'

    # Handle nested vs direct format
    if media_key in item:
        media = item[media_key]
    else:
        media = item

    ids = media.get('ids', {})

    return {
        'tmdb_id': ids.get('tmdb'),
        'imdb_id': ids.get('imdb'),
        'trakt_id': ids.get('trakt'),
        'title': media.get('title'),
        'year': media.get('year'),
        # Include extra metadata if available
        'rating': media.get('rating'),
        'votes': media.get('votes'),
        'watchers': item.get('watchers'),  # From trending
        'list_count': item.get('list_count'),  # From anticipated
    }


def get_trending_items(
    client: TraktClient,
    media_type: str,
    cache_dir: str,
    limit: int = DEFAULT_TRENDING_LIMIT,
    force_refresh: bool = False
) -> List[Dict]:
    """
    Get trending movies or shows from Trakt.

    Args:
        client: TraktClient instance
        media_type: 'movies' or 'shows'
        cache_dir: Directory for cache files
        limit: Max items to return
        force_refresh: Bypass cache

    Returns:
        List of items with tmdb_id, imdb_id, title, year, watchers
    """
    if not force_refresh:
        cache = _load_discovery_cache(cache_dir, 'trending', media_type)
        if cache:
            logger.debug(f"Using cached Trakt trending {media_type}")
            return cache['items'][:limit]

    logger.info(f"Fetching Trakt trending {media_type}...")
    try:
        raw_items = client.get_trending(media_type, limit=limit)
        items = [_extract_item_ids(item, media_type) for item in raw_items]
        # Filter out items without TMDB ID (needed for scoring)
        items = [i for i in items if i.get('tmdb_id')]
        _save_discovery_cache(cache_dir, 'trending', media_type, items)
        return items
    except TraktAPIError as e:
        logger.warning(f"Failed to fetch Trakt trending: {e}")
        return []


def get_popular_items(
    client: TraktClient,
    media_type: str,
    cache_dir: str,
    limit: int = DEFAULT_POPULAR_LIMIT,
    force_refresh: bool = False
) -> List[Dict]:
    """
    Get popular movies or shows from Trakt (most watched all time).

    Args:
        client: TraktClient instance
        media_type: 'movies' or 'shows'
        cache_dir: Directory for cache files
        limit: Max items to return
        force_refresh: Bypass cache

    Returns:
        List of items with tmdb_id, imdb_id, title, year
    """
    if not force_refresh:
        cache = _load_discovery_cache(cache_dir, 'popular', media_type)
        if cache:
            logger.debug(f"Using cached Trakt popular {media_type}")
            return cache['items'][:limit]

    logger.info(f"Fetching Trakt popular {media_type}...")
    try:
        raw_items = client.get_popular(media_type, limit=limit)
        items = [_extract_item_ids(item, media_type) for item in raw_items]
        items = [i for i in items if i.get('tmdb_id')]
        _save_discovery_cache(cache_dir, 'popular', media_type, items)
        return items
    except TraktAPIError as e:
        logger.warning(f"Failed to fetch Trakt popular: {e}")
        return []


def get_anticipated_items(
    client: TraktClient,
    media_type: str,
    cache_dir: str,
    limit: int = DEFAULT_ANTICIPATED_LIMIT,
    force_refresh: bool = False
) -> List[Dict]:
    """
    Get most anticipated upcoming movies or shows from Trakt.

    Args:
        client: TraktClient instance
        media_type: 'movies' or 'shows'
        cache_dir: Directory for cache files
        limit: Max items to return
        force_refresh: Bypass cache

    Returns:
        List of items with tmdb_id, imdb_id, title, year, list_count
    """
    if not force_refresh:
        cache = _load_discovery_cache(cache_dir, 'anticipated', media_type)
        if cache:
            logger.debug(f"Using cached Trakt anticipated {media_type}")
            return cache['items'][:limit]

    logger.info(f"Fetching Trakt anticipated {media_type}...")
    try:
        raw_items = client.get_anticipated(media_type, limit=limit)
        items = [_extract_item_ids(item, media_type) for item in raw_items]
        items = [i for i in items if i.get('tmdb_id')]
        _save_discovery_cache(cache_dir, 'anticipated', media_type, items)
        return items
    except TraktAPIError as e:
        logger.warning(f"Failed to fetch Trakt anticipated: {e}")
        return []


def get_recommended_items(
    client: TraktClient,
    media_type: str,
    cache_dir: str,
    limit: int = DEFAULT_RECOMMENDATIONS_LIMIT,
    force_refresh: bool = False
) -> List[Dict]:
    """
    Get personalized recommendations from Trakt.

    Requires authentication. Returns items Trakt thinks the user would like
    based on their watch history and ratings.

    Args:
        client: Authenticated TraktClient instance
        media_type: 'movies' or 'shows'
        cache_dir: Directory for cache files
        limit: Max items to return
        force_refresh: Bypass cache

    Returns:
        List of items with tmdb_id, imdb_id, title, year
    """
    if not client.is_authenticated:
        logger.warning("Cannot get Trakt recommendations: not authenticated")
        return []

    if not force_refresh:
        cache = _load_discovery_cache(cache_dir, 'recommendations', media_type)
        if cache:
            logger.debug(f"Using cached Trakt recommendations {media_type}")
            return cache['items'][:limit]

    logger.info(f"Fetching Trakt personalized recommendations for {media_type}...")
    try:
        raw_items = client.get_recommendations(media_type, limit=limit)
        items = [_extract_item_ids(item, media_type) for item in raw_items]
        items = [i for i in items if i.get('tmdb_id')]
        _save_discovery_cache(cache_dir, 'recommendations', media_type, items)
        return items
    except TraktAPIError as e:
        logger.warning(f"Failed to fetch Trakt recommendations: {e}")
        return []


def discover_from_trakt(
    config: Dict,
    media_type: str,
    cache_dir: str,
    exclude_tmdb_ids: Optional[Set[int]] = None,
    exclude_imdb_ids: Optional[Set[str]] = None
) -> Dict[str, List[Dict]]:
    """
    Fetch all enabled discovery sources from Trakt.

    This is the main entry point for Trakt discovery. It checks config for
    which sources are enabled and fetches them.

    Args:
        config: Full application config
        media_type: 'movie' or 'tv' (will be converted to Trakt format)
        cache_dir: Directory for cache files
        exclude_tmdb_ids: Set of TMDB IDs to exclude (e.g., already in library)
        exclude_imdb_ids: Set of IMDB IDs to exclude (e.g., Trakt watchlist)

    Returns:
        Dict with keys 'trending', 'popular', 'anticipated', 'recommendations'
        Each value is a list of items (may be empty if source disabled/failed)
    """
    exclude_tmdb_ids = exclude_tmdb_ids or set()
    exclude_imdb_ids = exclude_imdb_ids or set()

    trakt_config = config.get('trakt', {})
    discovery_config = trakt_config.get('discovery', {})

    # Check if Trakt and discovery are enabled
    if not trakt_config.get('enabled', False):
        return {'trending': [], 'popular': [], 'anticipated': [], 'recommendations': []}

    if not discovery_config.get('enabled', True):
        return {'trending': [], 'popular': [], 'anticipated': [], 'recommendations': []}

    # Get Trakt client
    client = get_authenticated_trakt_client(config)
    if not client:
        # Try unauthenticated client for public endpoints (trending, popular)
        from utils.trakt import create_trakt_client
        client = create_trakt_client(config)
        if not client:
            return {'trending': [], 'popular': [], 'anticipated': [], 'recommendations': []}

    # Convert media_type to Trakt format
    trakt_media_type = 'movies' if media_type == 'movie' else 'shows'

    # Get limits from config
    trending_limit = discovery_config.get('trending_limit', DEFAULT_TRENDING_LIMIT)
    popular_limit = discovery_config.get('popular_limit', DEFAULT_POPULAR_LIMIT)
    anticipated_limit = discovery_config.get('anticipated_limit', DEFAULT_ANTICIPATED_LIMIT)
    recommendations_limit = discovery_config.get('recommendations_limit', DEFAULT_RECOMMENDATIONS_LIMIT)

    results = {
        'trending': [],
        'popular': [],
        'anticipated': [],
        'recommendations': []
    }

    def filter_items(items: List[Dict]) -> List[Dict]:
        """Remove excluded items."""
        filtered = []
        for item in items:
            tmdb_id = item.get('tmdb_id')
            imdb_id = item.get('imdb_id')
            if tmdb_id and tmdb_id in exclude_tmdb_ids:
                continue
            if imdb_id and imdb_id in exclude_imdb_ids:
                continue
            filtered.append(item)
        return filtered

    # Fetch enabled sources
    if discovery_config.get('use_trending', True):
        items = get_trending_items(client, trakt_media_type, cache_dir, trending_limit)
        results['trending'] = filter_items(items)

    if discovery_config.get('use_popular', False):
        items = get_popular_items(client, trakt_media_type, cache_dir, popular_limit)
        results['popular'] = filter_items(items)

    if discovery_config.get('use_anticipated', False):
        items = get_anticipated_items(client, trakt_media_type, cache_dir, anticipated_limit)
        results['anticipated'] = filter_items(items)

    if discovery_config.get('use_recommendations', False) and client.is_authenticated:
        items = get_recommended_items(client, trakt_media_type, cache_dir, recommendations_limit)
        results['recommendations'] = filter_items(items)

    return results


# Source tier priorities: higher number = higher quality source
# Recommendations are personalized, so they're highest priority
# Trending is lowest as it's just current hype
SOURCE_TIER_PRIORITY = {
    'recommendations': 4,  # Personalized based on user ratings
    'anticipated': 3,      # Upcoming with buzz
    'popular': 2,          # All-time popular
    'trending': 1,         # Currently popular (fleeting)
}


def get_trakt_discovery_candidates(
    config: Dict,
    media_type: str,
    cache_dir: str,
    library_tmdb_ids: Set[int],
    exclude_imdb_ids: Optional[Set[str]] = None
) -> Dict[int, Dict]:
    """
    Get discovery candidates from Trakt for scoring.

    Converts Trakt discovery items into the candidate format expected by
    the external recommender's scoring pipeline.

    When the same tmdb_id appears in multiple sources, keeps the higher
    tier entry (recommendations > anticipated > popular > trending).

    Args:
        config: Full application config
        media_type: 'movie' or 'tv'
        cache_dir: Directory for cache files
        library_tmdb_ids: TMDB IDs already in Plex library
        exclude_imdb_ids: IMDB IDs to exclude (e.g., Trakt watchlist)

    Returns:
        Dict mapping tmdb_id -> {tmdb_id, title, year, source, source_tier, ...}
    """
    discovery = discover_from_trakt(
        config,
        media_type,
        cache_dir,
        exclude_tmdb_ids=library_tmdb_ids,
        exclude_imdb_ids=exclude_imdb_ids
    )

    candidates = {}

    # Process each source with source attribution and tier priority
    for source, items in discovery.items():
        source_tier = SOURCE_TIER_PRIORITY.get(source, 0)

        for item in items:
            tmdb_id = item.get('tmdb_id')
            if not tmdb_id:
                continue

            # If already have this candidate, only replace if new source is higher tier
            if tmdb_id in candidates:
                existing_tier = candidates[tmdb_id].get('source_tier', 0)
                if source_tier <= existing_tier:
                    continue

            candidates[tmdb_id] = {
                'tmdb_id': tmdb_id,
                'title': item.get('title'),
                'year': item.get('year'),
                'source': f'trakt_{source}',
                'source_tier': source_tier,
                'watchers': item.get('watchers'),
                'list_count': item.get('list_count'),
            }

    return candidates
