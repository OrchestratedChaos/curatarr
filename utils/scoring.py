"""
Similarity scoring utilities for Curatarr.
Handles content-to-profile similarity calculations.
"""

import logging
import math
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from .config import (
    MAX_REDISTRIBUTION_MULTIPLIER,
    POPULARITY_DAMPENING_CAP,
    POPULARITY_DAMPENING_FACTOR,
    TFIDF_GENRE_PENALTY,
    TFIDF_KEYWORD_PENALTY,
    UNSEEN_GENRE_PENALTY,
    UNSEEN_KEYWORD_PENALTY,
)
from .corpus_idf import idf_weight


def normalize_user_profile(user_prefs: Dict, tfidf_penalty_threshold: float = 0.15) -> Dict:
    """
    Pre-normalize user profile by creating lowercase key versions of all preference dicts
    and pre-computing TF-IDF thresholds.
    Call this once before scoring multiple items to avoid rebuilding these dicts per item.

    Args:
        user_prefs: User preference dict with 'directors', 'actors', 'keywords', etc.
        tfidf_penalty_threshold: Threshold percentage for TF-IDF penalties (default 15%)

    Returns:
        Same dict with added '_lower' suffixed keys containing lowercase versions
        and pre-computed threshold values
    """
    # Only normalize if not already done
    if "_normalized" in user_prefs:
        return user_prefs

    # Create lowercase versions of all string-keyed preference dicts
    for key in ["directors", "actors", "keywords", "studios", "languages"]:
        if key in user_prefs and isinstance(user_prefs[key], (dict, Counter)):
            user_prefs[f"{key}_lower"] = {k.lower() if isinstance(k, str) else k: v for k, v in user_prefs[key].items()}

    # Initialize fuzzy keyword match cache (populated lazily during scoring)
    user_prefs["_fuzzy_cache"] = {}

    # Pre-compute max counts for normalization and TF-IDF
    def max_positive(d):
        if not d:
            return 1
        positive_vals = [v for v in d.values() if v > 0]
        return max(positive_vals) if positive_vals else 1

    max_counts = {
        "genres": max_positive(user_prefs.get("genres", {})),
        "directors": max_positive(user_prefs.get("directors", {})),
        "studios": max_positive(user_prefs.get("studios", {})),
        "actors": max_positive(user_prefs.get("actors", {})),
        "languages": max_positive(user_prefs.get("languages", {})),
        # #273 PR4: "keywords" is the ONLY key any real caller ever uses
        # here (recommenders/movie.py's/tv.py's/external.py's own
        # watched-data builders all translate their storage-layer
        # 'tmdb_keywords' key to 'keywords' before calling into this
        # module - see each builder's own _calculate_similarity_from_cache/
        # load_user_profile_from_cache/_build_profile_via_recommender).
        # The "or user_prefs.get('tmdb_keywords', ...)" fallback that used
        # to sit here was dead code no real caller ever exercised -
        # removed along with the other #273-related compat shims (see
        # CHANGELOG).
        "keywords": max_positive(user_prefs.get("keywords", {})),
    }
    user_prefs["_max_counts"] = max_counts

    # Pre-compute TF-IDF thresholds
    user_prefs["_tfidf_thresholds"] = {
        "genres": max_counts["genres"] * tfidf_penalty_threshold,
        "keywords": max_counts["keywords"] * tfidf_penalty_threshold,
    }

    user_prefs["_normalized"] = True
    return user_prefs


# Genre normalization: Map various genre names to standard lowercase names
GENRE_NORMALIZATION = {
    "sci-fi": "science fiction",
    "scifi": "science fiction",
    "science-fiction": "science fiction",
    "sci-fi & fantasy": "science fiction",
    "action & adventure": "action",
    "action/adventure": "action",
    "war & politics": "war",
    "tv movie": "drama",
    "news": "documentary",
    "talk": "comedy",
    "reality": "documentary",
    "soap": "drama",
    "kids": "family",
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


def fuzzy_keyword_match(
    keyword: str, user_keywords: Dict[str, int], cache: Optional[Dict[str, Tuple[float, Optional[str]]]] = None
) -> Tuple[float, Optional[str]]:
    """
    Check if keyword fuzzy-matches any user keyword.

    Args:
        keyword: Keyword to match
        user_keywords: Dict of user's keyword preferences with counts
        cache: Optional dict to cache results (pass user_prefs['_fuzzy_cache'])

    Returns:
        Tuple of (match_score, matched_keyword) based on best partial match
    """
    if not keyword or not user_keywords:
        return 0, None

    keyword_lower = keyword.lower()

    # Check cache first (same keyword always matches same user keyword for a given profile)
    if cache is not None and keyword_lower in cache:
        return cache[keyword_lower]

    # Check for exact match first
    if keyword_lower in user_keywords:
        result: Tuple[float, Optional[str]] = (user_keywords[keyword_lower], keyword_lower)
        if cache is not None:
            cache[keyword_lower] = result
        return result

    # Check for partial matches (keyword contains or is contained by user keyword)
    best_score: float = 0
    best_match: Optional[str] = None

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

    result = (best_score, best_match)
    if cache is not None:
        cache[keyword_lower] = result
    return result


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
    if not recency_config.get("enabled", True):
        return 1.0

    # Calculate days since watched
    now = datetime.now(timezone.utc)
    viewed_date = datetime.fromtimestamp(int(viewed_at), tz=timezone.utc)
    days_ago = (now - viewed_date).days

    # Apply time-based decay weights
    if days_ago <= 30:
        multiplier = recency_config.get("days_0_30", 1.0)
    elif days_ago <= 90:
        multiplier = recency_config.get("days_31_90", 0.75)
    elif days_ago <= 180:
        multiplier = recency_config.get("days_91_180", 0.50)
    elif days_ago <= 365:
        multiplier = recency_config.get("days_181_365", 0.25)
    else:
        multiplier = recency_config.get("days_365_plus", 0.10)

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


def _redistribute_weights(weights: Dict, user_profile: Dict, media_type: str = "movie") -> Dict:
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
    has_genres = bool(user_profile.get("genres", {}))
    has_directors = bool(user_profile.get("directors", {})) if media_type == "movie" else False
    has_studios = bool(user_profile.get("studios", {})) if media_type == "tv" else False
    has_actors = bool(user_profile.get("actors", {}))
    has_languages = bool(user_profile.get("languages", {}))
    has_keywords = bool(user_profile.get("keywords", {}))

    # Get original weights
    genre_w = weights.get("genre", weights.get("genre_weight", 0.20))
    director_w = weights.get("director", weights.get("director_weight", 0.15)) if media_type == "movie" else 0
    studio_w = weights.get("studio", weights.get("studio_weight", 0.15)) if media_type == "tv" else 0
    actor_w = weights.get("actor", weights.get("actor_weight", 0.15))
    language_w = weights.get("language", weights.get("language_weight", 0.05))
    keyword_w = weights.get("keyword", weights.get("keyword_weight", 0.45))

    # Calculate used and unused weight
    used_weight = 0.0
    unused_weight = 0.0

    if has_genres:
        used_weight += genre_w
    else:
        unused_weight += genre_w

    if media_type == "movie":
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
    effective["genre"] = (genre_w * multiplier) if has_genres else 0
    effective["director"] = (director_w * multiplier) if has_directors else 0
    effective["studio"] = (studio_w * multiplier) if has_studios else 0
    effective["actor"] = (actor_w * multiplier) if has_actors else 0
    effective["language"] = (language_w * multiplier) if has_languages else 0
    effective["keyword"] = (keyword_w * multiplier) if has_keywords else 0

    return effective


@dataclass(frozen=True)
class ScoringOptions:
    """
    Tuning flags/thresholds for calculate_similarity_score(), collapsed from
    five independent boolean/threshold parameters (plus popularity_threshold)
    into one frozen dataclass so new call sites don't have to reason about a
    5-argument boolean-flag signature. Defaults match
    calculate_similarity_score()'s pre-existing per-argument defaults
    exactly - see that function's docstring for what each option does.

    Backward compatibility: calculate_similarity_score() still accepts the
    original normalize_counters/use_fuzzy_keywords/use_tfidf/
    tfidf_penalty_threshold/use_popularity_dampening/popularity_threshold
    keyword arguments directly, unchanged, for every existing caller/test.
    Passing an explicit `options=` takes precedence over those when both
    are given.
    """

    normalize_counters: bool = True
    use_fuzzy_keywords: bool = True
    use_tfidf: bool = True
    tfidf_penalty_threshold: float = 0.15
    use_popularity_dampening: bool = True
    popularity_threshold: int = 50000
    # Corpus IDF weights (utils/corpus_idf.py), term -> multiplier in
    # [IDF_MIN_WEIGHT, 1.0]. None/empty means no corpus was supplied and
    # scoring behaves exactly as it did before this existed - which is
    # what keeps every pre-existing caller and test bit-for-bit
    # unchanged, since only the recommenders build and pass a corpus.
    genre_idf: Optional[Mapping[str, float]] = None
    keyword_idf: Optional[Mapping[str, float]] = None


def _tfidf_threshold(user_profile: Dict, key: str, max_count: float, options: ScoringOptions) -> float:
    """
    Resolve the TF-IDF rarity threshold count for `key` ("genres" or
    "keywords"): prefer normalize_user_profile()'s pre-computed
    `_tfidf_thresholds` entry, otherwise derive it from
    max_count * options.tfidf_penalty_threshold. Returns 0 when TF-IDF
    penalties are disabled, matching calculate_similarity_score()'s
    pre-existing behavior of never entering the rarity-penalty branch below
    in that case.
    """
    if not options.use_tfidf:
        return 0
    return user_profile.get("_tfidf_thresholds", {}).get(key, max_count * options.tfidf_penalty_threshold)


def _normalize_user_genre_counts(user_genre_counter: Counter) -> Tuple[Dict[str, float], float]:
    """
    Collapse the user's raw genre counts onto normalize_genre()'s canonical
    names (e.g. "sci-fi" and "science fiction" both map to "science
    fiction"), keeping the max count seen per canonical name.

    Returns (normalized_user_genres, max_genre_count) - max_genre_count is
    1 when the user has no genre data, matching
    calculate_similarity_score()'s pre-existing fallback.
    """
    normalized_user_genres: Dict[str, float] = {}
    for genre, count in user_genre_counter.items():
        norm_genre = normalize_genre(genre)
        if norm_genre in normalized_user_genres:
            normalized_user_genres[norm_genre] = max(normalized_user_genres[norm_genre], count)
        else:
            normalized_user_genres[norm_genre] = count
    max_genre_count = max(normalized_user_genres.values()) if normalized_user_genres else 1
    return normalized_user_genres, max_genre_count


def _score_genre_component(
    content_genres: set,
    user_genre_counter: Counter,
    normalized_user_genres: Dict[str, float],
    max_genre_count: float,
    genre_weight: float,
    tfidf_threshold_count: float,
    options: ScoringOptions,
) -> Tuple[float, float, List[str]]:
    """
    Score the genre dimension, including its embedded TF-IDF rarity
    penalty (a genre the content has but that's rare in the user's profile
    counts against it).

    Returns (weighted_genre_score, weighted_genre_penalty, detail_strings) -
    weighted_genre_score is already the final weight-adjusted contribution
    to add to the overall similarity score (calculate_similarity_score()'s
    pre-existing `genre_final`); weighted_genre_penalty is the
    weight-adjusted penalty (pre-existing `genre_penalty * genre_weight`,
    tracked but not otherwise consumed - see calculate_similarity_score()
    docstring note).
    """
    genre_scores = []
    genre_penalty = 0.0
    details = []

    for genre in content_genres:
        norm_genre = normalize_genre(genre)
        genre_count = normalized_user_genres.get(norm_genre, 0)
        if genre_count == 0:
            genre_count = user_genre_counter.get(genre, 0)

        if genre_count > 0:
            # TF-IDF: if genre is rare in user's profile, apply penalty
            if options.use_tfidf and genre_count < tfidf_threshold_count:
                # Genre exists but is rare - user likely avoids it
                # Penalty proportional to how rare it is
                rarity = 1 - (genre_count / tfidf_threshold_count)
                penalty = rarity * TFIDF_GENRE_PENALTY
                genre_penalty += penalty
                details.append(
                    f"{genre} (TF-IDF: count {genre_count:.1f} < threshold {tfidf_threshold_count:.1f}, "
                    f"penalty: {round(penalty, 2)})"
                )
            else:
                # Genre is common in user's profile - good match
                if options.normalize_counters:
                    normalized_score = math.sqrt(genre_count / max_genre_count)
                else:
                    normalized_score = min(genre_count / max_genre_count, 1.0)
                # Discount the match by how ubiquitous the genre is across
                # the library - matching on something 80% of the library
                # carries says far less than matching on something 3% does.
                # 1.0 (no change) whenever no corpus was supplied.
                idf = idf_weight(norm_genre, options.genre_idf)
                normalized_score *= idf
                genre_scores.append(normalized_score)
                idf_note = f", idf: {round(idf, 2)}" if idf != 1.0 else ""
                details.append(f"{genre} (count: {genre_count:.1f}, norm: {round(normalized_score, 2)}{idf_note})")
        elif genre_count < 0:
            # Explicit negative signal: penalize this genre
            penalty = abs(genre_count) / max_genre_count * 0.5  # Cap penalty contribution
            genre_penalty += penalty
            details.append(f"{genre} (NEGATIVE: {genre_count}, penalty: {round(penalty, 2)})")
        elif options.use_tfidf and genre_count == 0:
            # User has never watched this genre - mild penalty
            genre_penalty += UNSEEN_GENRE_PENALTY
            details.append(f"{genre} (TF-IDF: unseen genre, penalty: {UNSEEN_GENRE_PENALTY})")

    genre_sum = sum(genre_scores)
    genre_ratio = 1 - (1 / (1 + genre_sum))
    genre_final = max(0, genre_ratio - genre_penalty) * genre_weight
    return genre_final, genre_penalty * genre_weight, details


def _score_director_component(
    content_directors: List[str],
    user_prefs: Dict,
    max_counts: Dict[str, float],
    director_weight: float,
    options: ScoringOptions,
) -> Tuple[float, float, List[str]]:
    """
    Score the director dimension (movies only - see calculate_similarity_score).

    Returns (weighted_director_score, weighted_director_penalty, detail_strings).
    """
    if not content_directors:
        return 0.0, 0.0, []

    # Use pre-normalized if available, otherwise build inline
    user_directors_lower = user_prefs.get("directors_lower") or {
        k.lower(): v for k, v in user_prefs["directors"].items()
    }
    director_scores = []
    director_penalty = 0.0
    details = []
    for director in content_directors:
        director_lower = director.lower() if isinstance(director, str) else director
        director_count = user_prefs["directors"].get(director, 0)
        if director_count == 0:
            director_count = user_directors_lower.get(director_lower, 0)
        if director_count > 0:
            if options.normalize_counters:
                normalized_score = math.sqrt(director_count / max_counts["directors"])
            else:
                normalized_score = min(director_count / max_counts["directors"], 1.0)
            director_scores.append(normalized_score)
            details.append(f"{director} (count: {director_count}, norm: {round(normalized_score, 2)})")
        elif director_count < 0:
            penalty = abs(director_count) / max_counts["directors"] * 0.5
            director_penalty += penalty
            details.append(f"{director} (NEGATIVE: {director_count}, penalty: {round(penalty, 2)})")

    avg_score = (sum(director_scores) / len(director_scores)) if director_scores else 0
    director_final = max(0, avg_score - director_penalty) * director_weight
    return director_final, director_penalty * director_weight, details


def _score_studio_component(
    content_studio,
    user_prefs: Dict,
    max_counts: Dict[str, float],
    studio_weight: float,
    options: ScoringOptions,
) -> Tuple[float, float, Optional[str]]:
    """
    Score the studio dimension (TV only - see calculate_similarity_score).

    Returns (weighted_studio_score, weighted_studio_penalty, detail_string) -
    detail_string is the last studio processed's detail line (matching
    calculate_similarity_score()'s pre-existing assignment - not append -
    into score_breakdown['details']['studio'], which is a single string,
    not a list, unlike the other dimensions), or None if no studios were
    checked.
    """
    if isinstance(content_studio, str):
        studios_to_check = [content_studio] if content_studio and content_studio != "N/A" else []
    else:
        studios_to_check = content_studio or []

    if not studios_to_check:
        return 0.0, 0.0, None

    studio_scores = []
    studio_penalty = 0.0
    detail = None
    for studio in studios_to_check:
        studio_lower = studio.lower() if isinstance(studio, str) else studio
        studio_count = user_prefs["studios"].get(studio_lower, 0)
        if studio_count == 0:
            studio_count = user_prefs["studios"].get(studio, 0)
        if studio_count > 0:
            if options.normalize_counters:
                normalized_score = math.sqrt(studio_count / max_counts["studios"])
            else:
                normalized_score = min(studio_count / max_counts["studios"], 1.0)
            studio_scores.append(normalized_score)
            detail = f"{studio} (count: {studio_count}, norm: {round(normalized_score, 2)})"
        elif studio_count < 0:
            penalty = abs(studio_count) / max_counts["studios"] * 0.5
            studio_penalty += penalty
            detail = f"{studio} (NEGATIVE: {studio_count}, penalty: {round(penalty, 2)})"

    avg_score = (sum(studio_scores) / len(studio_scores)) if studio_scores else 0
    studio_final = max(0, avg_score - studio_penalty) * studio_weight
    return studio_final, studio_penalty * studio_weight, detail


def _score_actor_component(
    content_cast: List[str],
    user_prefs: Dict,
    max_counts: Dict[str, float],
    actor_weight: float,
    options: ScoringOptions,
) -> Tuple[float, float, List[str]]:
    """
    Score the cast/actor dimension.

    Returns (weighted_actor_score, weighted_actor_penalty, detail_strings).
    """
    if not content_cast:
        return 0.0, 0.0, []

    # Use pre-normalized if available, otherwise build inline
    user_actors_lower = user_prefs.get("actors_lower") or {k.lower(): v for k, v in user_prefs["actors"].items()}
    actor_scores = []
    actor_penalty = 0.0
    details = []
    for actor in content_cast:
        actor_lower = actor.lower() if isinstance(actor, str) else actor
        actor_count = user_prefs["actors"].get(actor, 0)
        if actor_count == 0:
            actor_count = user_actors_lower.get(actor_lower, 0)
        if actor_count > 0:
            if options.normalize_counters:
                normalized_score = math.sqrt(actor_count / max_counts["actors"])
            else:
                normalized_score = min(actor_count / max_counts["actors"], 1.0)
            actor_scores.append(normalized_score)
            details.append(f"{actor} (count: {actor_count}, norm: {round(normalized_score, 2)})")
        elif actor_count < 0:
            penalty = abs(actor_count) / max_counts["actors"] * 0.5
            actor_penalty += penalty
            details.append(f"{actor} (NEGATIVE: {actor_count}, penalty: {round(penalty, 2)})")

    actor_sum = sum(actor_scores)
    actor_ratio = 1 - (1 / (1 + actor_sum))
    actor_final = max(0, actor_ratio - actor_penalty) * actor_weight
    return actor_final, actor_penalty * actor_weight, details


def _score_language_component(
    content_language,
    user_prefs: Dict,
    max_counts: Dict[str, float],
    language_weight: float,
    options: ScoringOptions,
) -> Tuple[float, Optional[str]]:
    """
    Score the language dimension. Unlike the other dimensions, language has
    no negative-signal/TF-IDF penalty in calculate_similarity_score() - it
    only ever contributes a non-negative score or nothing.

    Returns (weighted_language_score, detail_string_or_None).
    """
    if not content_language or content_language == "N/A":
        return 0.0, None

    lang_lower = content_language.lower()
    lang_count = user_prefs["languages"].get(lang_lower, 0)
    if lang_count <= 0:
        return 0.0, None

    if options.normalize_counters:
        normalized_score = math.sqrt(lang_count / max_counts["languages"])
    else:
        normalized_score = min(lang_count / max_counts["languages"], 1.0)
    lang_final = normalized_score * language_weight
    detail = f"{content_language} (count: {lang_count}, norm: {round(normalized_score, 2)})"
    return lang_final, detail


def _score_keyword_component(
    content_keywords: List[str],
    user_prefs: Dict,
    max_counts: Dict[str, float],
    keyword_weight: float,
    tfidf_kw_threshold: float,
    options: ScoringOptions,
) -> Tuple[float, float, List[str]]:
    """
    Score the keyword dimension, including its embedded TF-IDF rarity
    penalty and fuzzy-keyword matching fallback.

    Returns (weighted_keyword_score, weighted_keyword_penalty, detail_strings).
    """
    if not content_keywords:
        return 0.0, 0.0, []

    keyword_scores = []
    keyword_penalty = 0.0
    details = []
    # Use pre-normalized if available, otherwise build inline
    user_keywords_lower = user_prefs.get("keywords_lower") or {k.lower(): v for k, v in user_prefs["keywords"].items()}

    for kw in content_keywords:
        kw_lower = kw.lower() if isinstance(kw, str) else kw
        count = user_prefs["keywords"].get(kw, 0)
        if count == 0:
            count = user_keywords_lower.get(kw_lower, 0)
        if count == 0 and options.use_fuzzy_keywords:
            fuzzy_cache = user_prefs.get("_fuzzy_cache")
            fuzzy_count, _matched_kw = fuzzy_keyword_match(kw, user_keywords_lower, fuzzy_cache)
            count = fuzzy_count
        if count > 0:
            # TF-IDF: if keyword is rare in user's profile, apply penalty
            if options.use_tfidf and count < tfidf_kw_threshold:
                # Keyword exists but is rare - user likely doesn't prioritize it
                rarity = 1 - (count / tfidf_kw_threshold)
                penalty = rarity * TFIDF_KEYWORD_PENALTY
                keyword_penalty += penalty
                details.append(
                    f"{kw} (TF-IDF: count {count:.1f} < threshold {tfidf_kw_threshold:.1f}, "
                    f"penalty: {round(penalty, 2)})"
                )
            else:
                # Keyword is common in user's profile - good match
                if options.normalize_counters:
                    normalized_score = math.sqrt(count / max_counts["keywords"])
                else:
                    normalized_score = min(count / max_counts["keywords"], 1.0)
                # The dimension this matters most for: "sequel",
                # "aftercreditsstinger" and friends are frequent in almost
                # any profile precisely because they are frequent
                # everywhere, and carry nearly no signal about taste.
                idf = idf_weight(kw_lower, options.keyword_idf)
                normalized_score *= idf
                keyword_scores.append(normalized_score)
                idf_note = f", idf: {round(idf, 2)}" if idf != 1.0 else ""
                details.append(f"{kw} (count: {int(count)}, norm: {round(normalized_score, 2)}{idf_note})")
        elif count < 0:
            penalty = abs(count) / max_counts["keywords"] * 0.5
            keyword_penalty += penalty
            details.append(f"{kw} (NEGATIVE: {int(count)}, penalty: {round(penalty, 2)})")
        elif options.use_tfidf and count == 0:
            # User has never seen content with this keyword - very mild penalty
            # Keywords are more numerous and specific than genres, so smaller penalty
            keyword_penalty += UNSEEN_KEYWORD_PENALTY
            details.append(f"{kw} (TF-IDF: unseen keyword, penalty: {UNSEEN_KEYWORD_PENALTY})")

    keyword_sum = sum(keyword_scores)
    keyword_ratio = 1 - (1 / (1 + keyword_sum))
    keyword_final = max(0, keyword_ratio - keyword_penalty) * keyword_weight
    return keyword_final, keyword_penalty * keyword_weight, details


def _apply_active_weight_redistribution(
    score: float,
    component_scores: Dict[str, float],
    effective_weights: Dict,
    content_has_data: Optional[Dict[str, bool]] = None,
) -> float:
    """
    Per-item weight redistribution: for dimensions this item carries no
    data for at all, move their weight onto the dimensions that did
    score (proportionally by each active dimension's own effective
    weight), so an item isn't penalized just for lacking, say, a
    language field.

    Distinct from _redistribute_weights(), which redistributes at the
    profile level for dimensions the user has no preference data for at
    all - this operates per-item.

    IMPORTANT - "absent" is not "didn't match". This function used to
    redistribute on `comp_score == 0` alone, which conflated two very
    different cases:

      - the item has no data for that dimension (no keywords, no cast
        listed) - genuinely not the item's fault, redistribute; and
      - the item HAS that data and it simply scored zero against this
        user's profile (its keywords aren't ones they watch, or the
        TF-IDF penalty clamped the component to 0) - a real, meaningful
        zero.

    Treating the second case as redistributable inverted the ranking:
    the fewer dimensions an item matched on, the more weight piled onto
    whichever one did, so an item matching on nothing but generic genres
    had its genre ratio scaled across the entire weight budget. Measured
    on a real 225-movie profile before this fix: mean final score 0.545
    for items with 1 scoring dimension vs 0.384 for items with 3, and 9
    of the top 10 recommendations matched on genre alone - with 81 of
    117 items holding keyword data that scored 0 and being rewarded for
    it. Sparse or poorly-matching items outranked richly-described ones
    that matched on several dimensions imperfectly.

    `content_has_data` maps component name -> whether this item carries
    any data for that dimension. Omitted (None) preserves the old
    score-based behavior, so existing callers/tests that don't supply it
    are unaffected.

    component_scores must be the *rounded* score_breakdown values (e.g.
    score_breakdown["genre_score"]), not the raw per-dimension floats -
    matching calculate_similarity_score()'s pre-existing behavior.
    """
    active_weights = {}
    lost_weight = 0.0

    for comp, comp_score in component_scores.items():
        weight = effective_weights.get(comp, 0)
        if weight <= 0:
            continue

        if comp_score > 0:
            active_weights[comp] = (weight, comp_score / weight)
            continue

        # Scored zero. Only give its weight away if the item had nothing
        # to score in the first place; a zero against present data is a
        # real signal and must keep costing the item its weight.
        if content_has_data is None or not content_has_data.get(comp, True):
            lost_weight += weight

    if lost_weight > 0 and active_weights:
        total_active_weight = sum(w for w, _ in active_weights.values())
        if total_active_weight > 0:
            # Cap how far redistribution may amplify a score.
            #
            # Forgiving a missing dimension is right when the dimension is
            # SMALL - an item with no language field (weight 0.05) should
            # not be marked down for it. It is wrong when the dimension is
            # large, because then "we know nothing about this item" turns
            # into "everything we do know counts several times over".
            #
            # Measured on a real 186-candidate library: one title had no
            # tmdb_keywords at all, and keyword weight was 0.5 - half the
            # entire budget. Its whole 0.5 moved onto genre+language
            # (0.30 active), multiplying its score by (0.30+0.50)/0.30 =
            # 2.67x, from a raw 0.247 to 0.658. That took it from a true
            # rank of #54 to #1, ahead of every richly-described title
            # that matched on several dimensions. Every other item in the
            # top twelve was being scaled by 1.00-1.07x.
            #
            # This is the other half of the fix in 2.10.85, which stopped
            # rewarding items whose PRESENT data scored zero. That
            # correctly pushed those items down - and thereby floated the
            # one item with genuinely ABSENT data to the top, since its
            # redistribution was left untouched.
            multiplier = (total_active_weight + lost_weight) / total_active_weight
            if multiplier > MAX_REDISTRIBUTION_MULTIPLIER:
                # Keep only as much lost weight as the cap allows, and
                # let the remainder stay lost: an item we know less about
                # genuinely has less evidence of matching.
                lost_weight = (MAX_REDISTRIBUTION_MULTIPLIER - 1.0) * total_active_weight

            for _comp, (weight, ratio) in active_weights.items():
                extra_weight = lost_weight * (weight / total_active_weight)
                extra_score = extra_weight * ratio
                score += extra_score

    return score


def _apply_popularity_dampening(
    score: float, content_info: Dict, options: ScoringOptions
) -> Tuple[float, Optional[float]]:
    """
    Apply logarithmic popularity dampening for very popular content
    (content_info['vote_count'] above options.popularity_threshold), so
    blockbusters don't dominate purely on having more metadata.

    ~3% penalty per order of magnitude above threshold (50k votes: no
    penalty, 500k votes: ~3% penalty, 5M votes: ~6% penalty), capped at
    POPULARITY_DAMPENING_CAP.

    Returns (dampened_score, dampening_factor_rounded_or_None) - the second
    value is what calculate_similarity_score() records into
    score_breakdown['popularity_dampening'] when dampening applied, or None
    when it didn't (vote_count at/below threshold, or dampening disabled).
    """
    if not options.use_popularity_dampening:
        return score, None

    vote_count = content_info.get("vote_count", 0) or 0
    if vote_count <= options.popularity_threshold:
        return score, None

    excess_ratio = vote_count / options.popularity_threshold
    dampening = 1 - (math.log10(excess_ratio) * POPULARITY_DAMPENING_FACTOR)
    dampening = max(POPULARITY_DAMPENING_CAP, dampening)
    return score * dampening, round(dampening, 3)


def calculate_similarity_score(
    content_info: Dict,
    user_profile: Dict,
    media_type: str = "movie",
    weights: Optional[Dict] = None,
    normalize_counters: bool = True,
    use_fuzzy_keywords: bool = True,
    use_tfidf: bool = True,
    tfidf_penalty_threshold: float = 0.15,
    use_popularity_dampening: bool = True,
    popularity_threshold: int = 50000,
    genre_idf: Optional[Mapping[str, float]] = None,
    keyword_idf: Optional[Mapping[str, float]] = None,
    options: Optional[ScoringOptions] = None,
) -> Tuple[float, Dict]:
    """
    Calculate similarity score between content and user profile.

    Unified scoring function for movies, TV shows, and external recommendations.
    Uses weighted scoring across genres, directors/studios, actors, keywords, and language.
    Each dimension's scoring lives in its own _score_*_component() helper
    (see utils/scoring.py) so it's independently testable; this function is
    the orchestrator that combines them in a fixed order (float addition
    isn't associative, so that order is preserved deliberately - see
    tests/harness.py).

    Args:
        content_info: Dict with content metadata (genres, directors/studio, cast, keywords, language)
        user_profile: Dict with user's weighted preferences (Counter objects or dicts)
        media_type: 'movie' or 'tv' - determines director vs studio scoring
        weights: Optional custom weights dict. Defaults to standard weights if None.
        normalize_counters: If True, use sqrt normalization for diminishing returns
        use_fuzzy_keywords: If True, use fuzzy matching for keywords
        use_tfidf: If True, penalize content with genres/keywords rare in user's profile
        tfidf_penalty_threshold: Genres below this % of max count trigger penalty (default 15%)
        use_popularity_dampening: If True, slightly penalize very popular content (default True)
        popularity_threshold: Vote count above which dampening applies (default 50000)
        options: Optional ScoringOptions bundling the six tuning args above.
            New callers should prefer this; existing callers passing the
            individual keyword arguments above continue to work unchanged
            (see ScoringOptions' docstring). When both are given, `options`
            takes precedence.

    Returns:
        Tuple of (score 0-1, breakdown dict with component scores)
    """
    if options is None:
        options = ScoringOptions(
            normalize_counters=normalize_counters,
            use_fuzzy_keywords=use_fuzzy_keywords,
            use_tfidf=use_tfidf,
            tfidf_penalty_threshold=tfidf_penalty_threshold,
            use_popularity_dampening=use_popularity_dampening,
            popularity_threshold=popularity_threshold,
            genre_idf=genre_idf,
            keyword_idf=keyword_idf,
        )

    # Default weights (specificity-first approach)
    default_weights = {"genre": 0.25, "director": 0.05, "studio": 0.10, "actor": 0.20, "keyword": 0.50, "language": 0.0}
    weights = weights or default_weights

    effective_weights = _redistribute_weights(weights, user_profile, media_type)

    # Any, not a narrower per-key type - this is a heterogeneous
    # reporting structure (float component scores alongside a nested
    # "details" dict), not a uniformly-typed mapping.
    score_breakdown: Dict[str, Any] = {
        "genre_score": 0.0,
        "director_score": 0.0,
        "studio_score": 0.0,
        "actor_score": 0.0,
        "language_score": 0.0,
        "keyword_score": 0.0,
        "details": {"genres": [], "directors": [], "studio": None, "actors": [], "language": None, "keywords": []},
    }

    if not content_info or not user_profile:
        return 0.0, score_breakdown

    try:
        score = 0.0

        user_prefs = {
            "genres": Counter(user_profile.get("genres", {})),
            "directors": Counter(user_profile.get("directors", {})),
            "studios": Counter(user_profile.get("studios", {})),
            "actors": Counter(user_profile.get("actors", {})),
            "languages": Counter(user_profile.get("languages", {})),
            "keywords": Counter(user_profile.get("keywords", {})),
        }

        # Use pre-computed max counts if available (from normalize_user_profile)
        # Otherwise calculate them (for backwards compatibility)
        if "_max_counts" in user_profile:
            max_counts = user_profile["_max_counts"]
        else:

            def max_positive(counter):
                positive_vals = [v for v in counter.values() if v > 0]
                return max(positive_vals) if positive_vals else 1

            max_counts = {
                "genres": max_positive(user_prefs["genres"]),
                "directors": max_positive(user_prefs["directors"]),
                "studios": max_positive(user_prefs["studios"]),
                "actors": max_positive(user_prefs["actors"]),
                "languages": max_positive(user_prefs["languages"]),
                "keywords": max_positive(user_prefs["keywords"]),
            }

        # --- Genre Score (with embedded TF-IDF penalty) ---
        content_genres = set(content_info.get("genres", []))
        normalized_user_genres, max_genre_count = _normalize_user_genre_counts(user_prefs["genres"])
        genre_tfidf_threshold = _tfidf_threshold(user_profile, "genres", max_genre_count, options)
        genre_weight = effective_weights.get("genre", 0.20)
        genre_final, _genre_penalty_w, genre_details = _score_genre_component(
            content_genres,
            user_prefs["genres"],
            normalized_user_genres,
            max_genre_count,
            genre_weight,
            genre_tfidf_threshold,
            options,
        )
        score += genre_final
        score_breakdown["genre_score"] = round(genre_final, 3)
        score_breakdown["details"]["genres"] = genre_details

        # --- Director Score (movies only) ---
        director_final = 0.0
        content_directors: List = []
        if media_type == "movie":
            content_directors = content_info.get("directors", [])
            director_weight = effective_weights.get("director", 0.15)
            director_final, _director_penalty_w, director_details = _score_director_component(
                content_directors, user_prefs, max_counts, director_weight, options
            )
            score_breakdown["details"]["directors"] = director_details
        score += director_final
        score_breakdown["director_score"] = round(director_final, 3)

        # --- Studio Score (TV only) ---
        studio_final = 0.0
        studio_detail = None
        content_studio: Any = []
        if media_type == "tv":
            content_studio = content_info.get("studio", content_info.get("studios", []))
            studio_weight = effective_weights.get("studio", 0.15)
            studio_final, _studio_penalty_w, studio_detail = _score_studio_component(
                content_studio, user_prefs, max_counts, studio_weight, options
            )
        score += studio_final
        score_breakdown["studio_score"] = round(studio_final, 3)
        score_breakdown["details"]["studio"] = studio_detail

        # --- Actor Score ---
        content_cast = content_info.get("cast", [])
        actor_weight = effective_weights.get("actor", 0.15)
        actor_final, _actor_penalty_w, actor_details = _score_actor_component(
            content_cast, user_prefs, max_counts, actor_weight, options
        )
        score += actor_final
        score_breakdown["actor_score"] = round(actor_final, 3)
        score_breakdown["details"]["actors"] = actor_details

        # --- Language Score ---
        content_language = content_info.get("language", "N/A")
        language_weight = effective_weights.get("language", 0.05)
        lang_final, lang_detail = _score_language_component(
            content_language, user_prefs, max_counts, language_weight, options
        )
        score += lang_final
        score_breakdown["language_score"] = round(lang_final, 3)
        score_breakdown["details"]["language"] = lang_detail

        # --- Keyword Score (with embedded TF-IDF penalty) ---
        content_keywords = content_info.get("keywords", [])
        keyword_weight = effective_weights.get("keyword", 0.45)
        keyword_tfidf_threshold = _tfidf_threshold(user_profile, "keywords", max_counts["keywords"], options)
        keyword_final, _keyword_penalty_w, keyword_details = _score_keyword_component(
            content_keywords, user_prefs, max_counts, keyword_weight, keyword_tfidf_threshold, options
        )
        score += keyword_final
        score_breakdown["keyword_score"] = round(keyword_final, 3)
        score_breakdown["details"]["keywords"] = keyword_details

        # Per-item weight redistribution
        component_scores = {
            "genre": score_breakdown["genre_score"],
            "director": score_breakdown["director_score"],
            "studio": score_breakdown["studio_score"],
            "actor": score_breakdown["actor_score"],
            "language": score_breakdown["language_score"],
            "keyword": score_breakdown["keyword_score"],
        }
        # Whether this item carries any data per dimension, so a zero
        # scored against data the item HAS keeps costing it its weight -
        # only genuinely absent data is redistributed. See
        # _apply_active_weight_redistribution's docstring.
        #
        # director/studio are media-type gated above (director is movies
        # only, studio TV only); the dimension that doesn't apply reports
        # False here, which is the "absent, redistribute" case and
        # matches how it was already treated.
        content_has_data = {
            "genre": bool(content_genres),
            "director": bool(content_directors) if media_type == "movie" else False,
            "studio": bool(content_studio) if media_type == "tv" else False,
            "actor": bool(content_cast),
            "language": bool(content_language) and content_language != "N/A",
            "keyword": bool(content_keywords),
        }
        score = _apply_active_weight_redistribution(score, component_scores, effective_weights, content_has_data)

        score = min(score, 1.0)

        # Apply popularity dampening for very popular content
        # This prevents blockbusters from dominating just because they have more metadata
        score, dampening = _apply_popularity_dampening(score, content_info, options)
        if dampening is not None:
            score_breakdown["popularity_dampening"] = dampening

        return score, score_breakdown

    except Exception as e:
        logging.warning(f"Error calculating similarity score for {content_info.get('title', 'Unknown')}: {e}")
        return 0.0, score_breakdown


class _RandomLike(Protocol):
    """Structural type for select_tiered_recommendations()'s `rng` param.

    Matches both a real random.Random instance AND the `random` module
    itself - the `rng or random` fallback below intentionally accepts
    either (the module's top-level sample() is a bound method of the
    same underlying Random class the module maintains internally), and
    .sample() is the only method this function ever calls on it.
    """

    def sample(self, population: Sequence[Any], k: int) -> List[Any]: ...


def select_tiered_recommendations(
    scored_items: List[Dict],
    limit: int,
    safe_percent: float = 0.6,
    diverse_percent: float = 0.3,
    wildcard_percent: float = 0.1,
    rng: Optional[_RandomLike] = None,
) -> List[Dict]:
    """
    Select recommendations using a tiered approach for variety.

    Tiers:
    - Safe (60%): Top-scored items, high confidence picks
    - Diverse (30%): Mid-tier items, introduces variety
    - Wildcard (10%): Lower-scored discoveries

    Args:
        scored_items: List of items sorted by score (highest first)
        limit: Total number of recommendations to return
        safe_percent: Percentage of limit for safe picks (default 0.6)
        diverse_percent: Percentage of limit for diverse picks (default 0.3)
        wildcard_percent: Percentage of limit for wildcard picks (default 0.1)
        rng: Optional random.Random instance to draw the diverse/wildcard
            picks from. Additive/backward-compatible - defaults to None,
            which preserves the existing behavior of drawing from the
            module-level `random` (and therefore the process-global seed,
            unseeded by default). Pass a seeded `random.Random(seed)` for
            reproducible selection (see tests/harness.py).

    Returns:
        List of selected items with tier diversity
    """
    if not scored_items:
        return []

    rng = rng or random

    total = len(scored_items)

    # Calculate counts per tier (ensure at least 1 for each tier if possible)
    safe_count = max(1, int(limit * safe_percent))
    diverse_count = max(1, int(limit * diverse_percent))
    wildcard_count = max(1, int(limit * wildcard_percent))

    # Adjust if we have fewer items than requested
    if limit > total:
        return scored_items[:]

    # Define tier boundaries based on percentile of available items
    # Top 20% = safe pool, 20-60% = diverse pool, 60-100% = wildcard pool
    safe_boundary = max(1, int(total * 0.20))
    diverse_boundary = max(safe_boundary + 1, int(total * 0.60))

    safe_pool = scored_items[:safe_boundary]
    diverse_pool = scored_items[safe_boundary:diverse_boundary]
    wildcard_pool = scored_items[diverse_boundary:]

    selected = []

    # Select safe picks (top tier, highest scores)
    safe_picks = safe_pool[: min(safe_count, len(safe_pool))]
    selected.extend(safe_picks)

    # Select diverse picks (mid tier, some randomization for variety)
    if diverse_pool and diverse_count > 0:
        available_diverse = min(diverse_count, len(diverse_pool))
        if len(diverse_pool) > available_diverse:
            diverse_picks = rng.sample(diverse_pool, available_diverse)
        else:
            diverse_picks = diverse_pool[:]
        selected.extend(diverse_picks)

    # Select wildcard picks (lower tier, random for discovery)
    if wildcard_pool and wildcard_count > 0:
        available_wildcard = min(wildcard_count, len(wildcard_pool))
        if len(wildcard_pool) > available_wildcard:
            wildcard_picks = rng.sample(wildcard_pool, available_wildcard)
        else:
            wildcard_picks = wildcard_pool[:]
        selected.extend(wildcard_picks)

    # Fill remaining slots from safe pool if needed
    remaining = limit - len(selected)
    if remaining > 0 and len(safe_pool) > safe_count:
        extra = safe_pool[safe_count : safe_count + remaining]
        selected.extend(extra)

    # Sort final selection by score for consistent output - same
    # (score, rating, vote_count) tiebreaker as
    # BaseRecommender.get_recommendations()'s own scored_items.sort()
    # (#291): scored_items is already tiebroken this way by the time it
    # reaches this function (see that sort's own comment), but this
    # final re-sort is itself the last word on display order (the
    # safe/diverse/wildcard tiers above are assembled by slicing plus
    # rng.sample(), which does not preserve input order for the
    # diverse/wildcard picks) - without the same tiebreaker here, an
    # all-(or partially-)tied-score set (the cold-start case) would
    # collapse back to whatever incidental order the tier
    # slicing/sampling above left them in, rather than best-rated-first.
    selected.sort(
        key=lambda x: (
            x.get("similarity_score", x.get("score", 0)),
            x.get("rating") or 0.0,
            x.get("vote_count") or 0,
        ),
        reverse=True,
    )

    return selected[:limit]
