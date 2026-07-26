"""
Deterministic scoring/pipeline harness.

A reusable, committed harness for verifying that a scoring/pipeline
refactor doesn't silently change output. It:

  1. Loads pinned, fully-synthetic fixtures (tests/fixtures/scoring_harness/
     movies_cache_fixture.json, user_profile_fixture.json) - shaped exactly
     like the real cache/all_movies_cache.json and the user_profile dict
     recommenders/movie.py's _calculate_similarity_from_cache builds from
     cache/watched_cache_plex_<user>.json, but with invented titles,
     cast/director names, and keywords. No real Plex data, usernames, or
     watch history - see this module's own git history / CHANGELOG for
     why that's a hard requirement here.
  2. Recomputes similarity scores from scratch via
     utils.scoring.calculate_similarity_score for every fixture movie -
     always a cache-miss recompute, never a profile_hash cache-hit
     shortcut (recommenders/base.py's get_recommendations() normally
     takes the cache-hit path in production, which would mask exactly
     the kind of nondeterminism this harness exists to catch).
  3. Seeds utils.scoring.select_tiered_recommendations's RNG explicitly
     (see that function's `rng` parameter).
  4. Is meant to be run as a subprocess with PYTHONHASHSEED pinned
     (belt-and-braces on top of the explicit seed above - see
     tests/test_harness.py) since Python's string-hash randomization seed
     is fixed at interpreter startup and can't be changed mid-process.

Usage (module, not a script - keeps `python -m` import semantics so
`from tests...` imports resolve the same way pytest collects them):

    PYTHONHASHSEED=0 python -m tests.harness

Prints a JSON document to stdout with per-title exact score bit patterns
(float.hex(), not a rounded display value - a display rounding would mask
the exact float non-associativity this harness's Task 4 use case is
checking for) and the final tiered-selection title order. Two invocations
with the same PYTHONHASHSEED and seed must be byte-identical; see
tests/test_harness.py for the assertions this backs.
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.scoring import calculate_similarity_score, select_tiered_recommendations  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "scoring_harness")

# Arbitrary, pinned - not meaningful beyond "always the same value" so
# repeated harness runs are comparable.
DEFAULT_SEED = 20260726
DEFAULT_LIMIT = 15


def load_fixtures():
    """Load the pinned synthetic fixtures. Returns (movies_cache, user_profile)."""
    with open(os.path.join(FIXTURES_DIR, "movies_cache_fixture.json"), encoding="utf-8") as f:
        movies_cache = json.load(f)
    with open(os.path.join(FIXTURES_DIR, "user_profile_fixture.json"), encoding="utf-8") as f:
        user_profile = json.load(f)
    return movies_cache, user_profile


def run(seed: int = DEFAULT_SEED, limit: int = DEFAULT_LIMIT) -> dict:
    """Recompute scores for every fixture movie against the fixture user
    profile (always a from-scratch recompute) and run the tiered/random
    selection with an explicitly seeded RNG.

    Returns a JSON-serializable dict - see module docstring for why
    `score_hex` (float.hex(), the exact bit pattern) is reported alongside
    the plain float.
    """
    movies_cache, user_profile = load_fixtures()
    movies = movies_cache["movies"]

    scored = []
    for rating_key, item in sorted(movies.items()):
        content_info = {
            "genres": item.get("genres", []),
            "directors": item.get("directors", []),
            "cast": item.get("cast", []),
            "language": item.get("language", "N/A"),
            "keywords": item.get("tmdb_keywords", []),
            "vote_count": item.get("vote_count", 0),
            "collection_id": item.get("collection_id"),
        }
        score, _breakdown = calculate_similarity_score(
            content_info=content_info,
            user_profile=user_profile,
            media_type="movie",
        )
        scored.append(
            {
                "rating_key": rating_key,
                "title": item["title"],
                "score": score,
                "score_hex": float(score).hex(),
                "similarity_score": score,
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)

    rng = random.Random(seed)
    selected = select_tiered_recommendations(scored, limit, rng=rng)

    return {
        "seed": seed,
        "limit": limit,
        "num_fixture_movies": len(movies),
        "all_scores": [{"title": s["title"], "score_hex": s["score_hex"]} for s in scored],
        "selected_titles": [s["title"] for s in selected],
    }


def main():
    result = run()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
