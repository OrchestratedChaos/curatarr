"""
Similarity scoring utilities for Plex Recommender.
Handles content-to-profile similarity calculations.
"""

import math
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

# Genre normalization: Map various genre names to standard lowercase names
GENRE_NORMALIZATION = {
    'sci-fi': 'science fiction',
    'scifi': 'science fiction',
    'science-fiction': 'science fiction',
    'sci-fi & fantasy': 'science fiction',
    'action & adventure': 'action',
    'action/adventure': 'action',
    'war & politics': 'war',
    'tv movie': 'drama',
    'news': 'documentary',
    'talk': 'comedy',
    'reality': 'documentary',
    'soap': 'drama',
    'kids': 'family',
}


def normalize_genre(genre_name: str) -> str:
    """
    Normalize genre name to lowercase standard.

    Args:
        genre_name: Raw genre name from Plex or other source

    Returns:
        Normalized lowercase genre name
    """
    if not genre_name:
        return genre_name
    lower = genre_name.lower().strip()
    # Return lowercase version, with optional mapping for special cases
    mapped = GENRE_NORMALIZATION.get(lower)
    return mapped.lower() if mapped else lower


def fuzzy_keyword_match(keyword: str, user_keywords: Dict[str, int]) -> Tuple[float, Optional[str]]:
    """
    Check if keyword fuzzy-matches any user keyword.

    Args:
        keyword: Keyword to match
        user_keywords: Dict of user's keyword preferences with counts

    Returns:
        Tuple of (match_score, matched_keyword) based on best partial match
    """
    if not keyword or not user_keywords:
        return 0, None

    keyword_lower = keyword.lower()

    # Check for exact match first
    if keyword_lower in user_keywords:
        return user_keywords[keyword_lower], keyword_lower

    # Check for partial matches (keyword contains or is contained by user keyword)
    best_score = 0
    best_match = None

    for user_kw, count in user_keywords.items():
        user_kw_lower = user_kw.lower()

        # Check if one contains the other
        if keyword_lower in user_kw_lower or user_kw_lower in keyword_lower:
            # Score based on overlap ratio
            overlap = len(set(keyword_lower.split()) & set(user_kw_lower.split()))
            total = len(set(keyword_lower.split()) | set(user_kw_lower.split()))
            similarity = overlap / total if total > 0 else 0

            # Weight by the user's preference count and similarity
            match_score = count * (0.5 + 0.5 * similarity)  # At least 50% credit for partial match

            if match_score > best_score:
                best_score = match_score
                best_match = user_kw

    return best_score, best_match


def calculate_recency_multiplier(viewed_at, recency_config: dict) -> float:
    """
    Calculate recency decay multiplier based on when content was watched.

    Args:
        viewed_at: Timestamp of when content was viewed
        recency_config: Recency decay configuration from config

    Returns:
        Multiplier value (0.1 to 1.0)
    """
    # Check if recency decay is enabled
    if not recency_config.get('enabled', True):
        return 1.0

    # Calculate days since watched
    now = datetime.now(timezone.utc)
    viewed_date = datetime.fromtimestamp(int(viewed_at), tz=timezone.utc)
    days_ago = (now - viewed_date).days

    # Apply time-based decay weights
    if days_ago <= 30:
        multiplier = recency_config.get('days_0_30', 1.0)
    elif days_ago <= 90:
        multiplier = recency_config.get('days_31_90', 0.75)
    elif days_ago <= 180:
        multiplier = recency_config.get('days_91_180', 0.50)
    elif days_ago <= 365:
        multiplier = recency_config.get('days_181_365', 0.25)
    else:
        multiplier = recency_config.get('days_365_plus', 0.10)

    return multiplier


def calculate_rewatch_multiplier(view_count: int) -> float:
    """
    Calculate rewatch multiplier using logarithmic scaling.

    Rewatch scale (log2(views) + 1):
    - 1 view: 1.0x weight
    - 2 views: 2.0x weight
    - 4 views: 3.0x weight
    - 8 views: 4.0x weight
    - 16 views: 5.0x weight

    Args:
        view_count: Number of times content was viewed

    Returns:
        Multiplier value (1.0+)
    """
    if not view_count or view_count <= 1:
        return 1.0
    return math.log2(view_count) + 1


def _redistribute_weights(weights: Dict, user_profile: Dict, media_type: str = 'movie') -> Dict:
    """
    Redistribute weights from empty profile components to components with data.

    If user has no keywords in their profile, that weight would be wasted.
    This redistributes unused weight proportionally to components that have data.

    Args:
        weights: Original weight dict
        user_profile: User's profile data (counters for genres, actors, etc.)
        media_type: 'movie' or 'tv' - determines director vs studio

    Returns:
        Dict of effective weights with redistribution applied
    """
    # Check which components have data in user profile
    has_genres = bool(user_profile.get('genres', {}))
    has_directors = bool(user_profile.get('directors', {})) if media_type == 'movie' else False
    has_studios = bool(user_profile.get('studios', user_profile.get('studio', {}))) if media_type == 'tv' else False
    has_actors = bool(user_profile.get('actors', {}))
    has_languages = bool(user_profile.get('languages', {}))
    has_keywords = bool(user_profile.get('keywords', user_profile.get('tmdb_keywords', {})))

    # Get original weights
    genre_w = weights.get('genre', weights.get('genre_weight', 0.20))
    director_w = weights.get('director', weights.get('director_weight', 0.15)) if media_type == 'movie' else 0
    studio_w = weights.get('studio', weights.get('studio_weight', 0.15)) if media_type == 'tv' else 0
    actor_w = weights.get('actor', weights.get('actor_weight', 0.15))
    language_w = weights.get('language', weights.get('language_weight', 0.05))
    keyword_w = weights.get('keyword', weights.get('keyword_weight', 0.45))

    # Calculate used and unused weight
    used_weight = 0.0
    unused_weight = 0.0

    if has_genres:
        used_weight += genre_w
    else:
        unused_weight += genre_w

    if media_type == 'movie':
        if has_directors:
            used_weight += director_w
        else:
            unused_weight += director_w
    else:  # tv
        if has_studios:
            used_weight += studio_w
        else:
            unused_weight += studio_w

    if has_actors:
        used_weight += actor_w
    else:
        unused_weight += actor_w

    if has_languages:
        used_weight += language_w
    else:
        unused_weight += language_w

    if has_keywords:
        used_weight += keyword_w
    else:
        unused_weight += keyword_w

    # If no data at all, return original weights
    if used_weight == 0:
        return weights.copy()

    # Calculate redistribution multiplier
    multiplier = (used_weight + unused_weight) / used_weight

    # Build effective weights
    effective = {}
    effective['genre'] = (genre_w * multiplier) if has_genres else 0
    effective['director'] = (director_w * multiplier) if has_directors else 0
    effective['studio'] = (studio_w * multiplier) if has_studios else 0
    effective['actor'] = (actor_w * multiplier) if has_actors else 0
    effective['language'] = (language_w * multiplier) if has_languages else 0
    effective['keyword'] = (keyword_w * multiplier) if has_keywords else 0

    return effective


def calculate_similarity_score(
    content_info: Dict,
    user_profile: Dict,
    media_type: str = 'movie',
    weights: Optional[Dict] = None,
    normalize_counters: bool = True,
    use_fuzzy_keywords: bool = True
) -> Tuple[float, Dict]:
    """
    Calculate similarity score between content and user profile.

    Unified scoring function for movies, TV shows, and external recommendations.
    Uses weighted scoring across genres, directors/studios, actors, keywords, and language.

    Args:
        content_info: Dict with content metadata (genres, directors/studio, cast, keywords, language)
        user_profile: Dict with user's weighted preferences (Counter objects or dicts)
        media_type: 'movie' or 'tv' - determines director vs studio scoring
        weights: Optional custom weights dict. Defaults to standard weights if None.
        normalize_counters: If True, use sqrt normalization for diminishing returns
        use_fuzzy_keywords: If True, use fuzzy matching for keywords

    Returns:
        Tuple of (score 0-1, breakdown dict with component scores)
    """
    # Default weights (specificity-first approach)
    default_weights = {
        'genre': 0.25,
        'director': 0.05,
        'studio': 0.10,
        'actor': 0.20,
        'keyword': 0.50,
        'language': 0.0
    }
    weights = weights or default_weights

    effective_weights = _redistribute_weights(weights, user_profile, media_type)

    score_breakdown = {
        'genre_score': 0.0,
        'director_score': 0.0,
        'studio_score': 0.0,
        'actor_score': 0.0,
        'language_score': 0.0,
        'keyword_score': 0.0,
        'details': {
            'genres': [],
            'directors': [],
            'studio': None,
            'actors': [],
            'language': None,
            'keywords': []
        }
    }

    if not content_info or not user_profile:
        return 0.0, score_breakdown

    try:
        score = 0.0

        user_prefs = {
            'genres': Counter(user_profile.get('genres', {})),
            'directors': Counter(user_profile.get('directors', {})),
            'studios': Counter(user_profile.get('studios', user_profile.get('studio', {}))),
            'actors': Counter(user_profile.get('actors', {})),
            'languages': Counter(user_profile.get('languages', {})),
            'keywords': Counter(user_profile.get('keywords', user_profile.get('tmdb_keywords', {})))
        }

        max_counts = {
            'genres': max(user_prefs['genres'].values()) if user_prefs['genres'] else 1,
            'directors': max(user_prefs['directors'].values()) if user_prefs['directors'] else 1,
            'studios': max(user_prefs['studios'].values()) if user_prefs['studios'] else 1,
            'actors': max(user_prefs['actors'].values()) if user_prefs['actors'] else 1,
            'languages': max(user_prefs['languages'].values()) if user_prefs['languages'] else 1,
            'keywords': max(user_prefs['keywords'].values()) if user_prefs['keywords'] else 1
        }

        # Build normalized genre lookup
        normalized_user_genres = {}
        for genre, count in user_prefs['genres'].items():
            norm_genre = normalize_genre(genre)
            if norm_genre in normalized_user_genres:
                normalized_user_genres[norm_genre] = max(normalized_user_genres[norm_genre], count)
            else:
                normalized_user_genres[norm_genre] = count
        max_genre_count = max(normalized_user_genres.values()) if normalized_user_genres else 1

        # --- Genre Score ---
        content_genres = set(content_info.get('genres', []))
        if content_genres:
            genre_scores = []
            for genre in content_genres:
                norm_genre = normalize_genre(genre)
                genre_count = normalized_user_genres.get(norm_genre, 0)
                if genre_count == 0:
                    genre_count = user_prefs['genres'].get(genre, 0)
                if genre_count > 0:
                    if normalize_counters:
                        normalized_score = math.sqrt(genre_count / max_genre_count)
                    else:
                        normalized_score = min(genre_count / max_genre_count, 1.0)
                    genre_scores.append(normalized_score)
                    score_breakdown['details']['genres'].append(
                        f"{genre} (count: {genre_count}, norm: {round(normalized_score, 2)})"
                    )
            if genre_scores:
                genre_weight = effective_weights.get('genre', 0.20)
                genre_sum = sum(genre_scores)
                genre_ratio = 1 - (1 / (1 + genre_sum))
                genre_final = genre_ratio * genre_weight
                score += genre_final
                score_breakdown['genre_score'] = round(genre_final, 3)

        # --- Director Score (movies only) ---
        if media_type == 'movie':
            content_directors = content_info.get('directors', [])
            if content_directors:
                user_directors_lower = {k.lower(): v for k, v in user_prefs['directors'].items()}
                director_scores = []
                for director in content_directors:
                    director_lower = director.lower() if isinstance(director, str) else director
                    director_count = user_prefs['directors'].get(director, 0)
                    if director_count == 0:
                        director_count = user_directors_lower.get(director_lower, 0)
                    if director_count > 0:
                        if normalize_counters:
                            normalized_score = math.sqrt(director_count / max_counts['directors'])
                        else:
                            normalized_score = min(director_count / max_counts['directors'], 1.0)
                        director_scores.append(normalized_score)
                        score_breakdown['details']['directors'].append(
                            f"{director} (count: {director_count}, norm: {round(normalized_score, 2)})"
                        )
                if director_scores:
                    director_weight = effective_weights.get('director', 0.15)
                    director_final = (sum(director_scores) / len(director_scores)) * director_weight
                    score += director_final
                    score_breakdown['director_score'] = round(director_final, 3)

        # --- Studio Score (TV only) ---
        if media_type == 'tv':
            content_studio = content_info.get('studio', content_info.get('studios', []))
            if isinstance(content_studio, str):
                studios_to_check = [content_studio] if content_studio and content_studio != 'N/A' else []
            else:
                studios_to_check = content_studio or []

            if studios_to_check:
                studio_scores = []
                for studio in studios_to_check:
                    studio_lower = studio.lower() if isinstance(studio, str) else studio
                    studio_count = user_prefs['studios'].get(studio_lower, 0)
                    if studio_count == 0:
                        studio_count = user_prefs['studios'].get(studio, 0)
                    if studio_count > 0:
                        if normalize_counters:
                            normalized_score = math.sqrt(studio_count / max_counts['studios'])
                        else:
                            normalized_score = min(studio_count / max_counts['studios'], 1.0)
                        studio_scores.append(normalized_score)
                        score_breakdown['details']['studio'] = f"{studio} (count: {studio_count}, norm: {round(normalized_score, 2)})"
                if studio_scores:
                    studio_weight = effective_weights.get('studio', 0.15)
                    studio_final = (sum(studio_scores) / len(studio_scores)) * studio_weight
                    score += studio_final
                    score_breakdown['studio_score'] = round(studio_final, 3)

        # --- Actor Score ---
        content_cast = content_info.get('cast', [])
        if content_cast:
            user_actors_lower = {k.lower(): v for k, v in user_prefs['actors'].items()}
            actor_scores = []
            matched_actors = 0
            for actor in content_cast:
                actor_lower = actor.lower() if isinstance(actor, str) else actor
                actor_count = user_prefs['actors'].get(actor, 0)
                if actor_count == 0:
                    actor_count = user_actors_lower.get(actor_lower, 0)
                if actor_count > 0:
                    matched_actors += 1
                    if normalize_counters:
                        normalized_score = math.sqrt(actor_count / max_counts['actors'])
                    else:
                        normalized_score = min(actor_count / max_counts['actors'], 1.0)
                    actor_scores.append(normalized_score)
                    score_breakdown['details']['actors'].append(
                        f"{actor} (count: {actor_count}, norm: {round(normalized_score, 2)})"
                    )
            if matched_actors > 0:
                actor_sum = sum(actor_scores)
                actor_ratio = 1 - (1 / (1 + actor_sum))
                actor_weight = effective_weights.get('actor', 0.15)
                actor_final = actor_ratio * actor_weight
                score += actor_final
                score_breakdown['actor_score'] = round(actor_final, 3)

        # --- Language Score ---
        content_language = content_info.get('language', 'N/A')
        if content_language and content_language != 'N/A':
            lang_lower = content_language.lower()
            lang_count = user_prefs['languages'].get(lang_lower, 0)
            if lang_count > 0:
                if normalize_counters:
                    normalized_score = math.sqrt(lang_count / max_counts['languages'])
                else:
                    normalized_score = min(lang_count / max_counts['languages'], 1.0)
                language_weight = effective_weights.get('language', 0.05)
                lang_final = normalized_score * language_weight
                score += lang_final
                score_breakdown['language_score'] = round(lang_final, 3)
                score_breakdown['details']['language'] = f"{content_language} (count: {lang_count}, norm: {round(normalized_score, 2)})"

        # --- Keyword Score ---
        content_keywords = content_info.get('keywords', content_info.get('tmdb_keywords', []))
        if content_keywords:
            keyword_scores = []
            user_keywords_lower = {k.lower(): v for k, v in user_prefs['keywords'].items()}

            for kw in content_keywords:
                kw_lower = kw.lower() if isinstance(kw, str) else kw
                count = user_prefs['keywords'].get(kw, 0)
                if count == 0:
                    count = user_keywords_lower.get(kw_lower, 0)
                if count == 0 and use_fuzzy_keywords:
                    fuzzy_count, matched_kw = fuzzy_keyword_match(kw, user_keywords_lower)
                    count = fuzzy_count
                if count > 0:
                    if normalize_counters:
                        normalized_score = math.sqrt(count / max_counts['keywords'])
                    else:
                        normalized_score = min(count / max_counts['keywords'], 1.0)
                    keyword_scores.append(normalized_score)
                    score_breakdown['details']['keywords'].append(
                        f"{kw} (count: {int(count)}, norm: {round(normalized_score, 2)})"
                    )
            if keyword_scores:
                keyword_weight = effective_weights.get('keyword', 0.45)
                keyword_sum = sum(keyword_scores)
                keyword_ratio = 1 - (1 / (1 + keyword_sum))
                keyword_final = keyword_ratio * keyword_weight
                score += keyword_final
                score_breakdown['keyword_score'] = round(keyword_final, 3)

        # Per-item weight redistribution
        component_scores = {
            'genre': score_breakdown['genre_score'],
            'director': score_breakdown['director_score'],
            'studio': score_breakdown['studio_score'],
            'actor': score_breakdown['actor_score'],
            'language': score_breakdown['language_score'],
            'keyword': score_breakdown['keyword_score']
        }

        active_weights = {}
        lost_weight = 0.0

        for comp, comp_score in component_scores.items():
            weight = effective_weights.get(comp, 0)
            if weight > 0:
                if comp_score > 0:
                    active_weights[comp] = (weight, comp_score / weight if weight > 0 else 0)
                else:
                    lost_weight += weight

        if lost_weight > 0 and active_weights:
            total_active_weight = sum(w for w, _ in active_weights.values())
            if total_active_weight > 0:
                for comp, (weight, ratio) in active_weights.items():
                    extra_weight = lost_weight * (weight / total_active_weight)
                    extra_score = extra_weight * ratio
                    score += extra_score

        score = min(score, 1.0)
        return score, score_breakdown

    except Exception as e:
        logging.warning(f"Error calculating similarity score for {content_info.get('title', 'Unknown')}: {e}")
        return 0.0, score_breakdown
