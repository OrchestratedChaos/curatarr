"""
Golden-output equivalence harness for recommenders/external.py's watchlist
generation pipeline (PR2, external.py architecture decomposition - see
CHANGELOG's [2.10.4x] "external.py decomposition" entries).

Exercises, against fixed synthetic inputs (no real Plex/TMDB data, no real
network - only recommenders.external.requests.get and Plex library/section
objects are mocked; see tests/conftest.py's `_block_non_loopback_sockets`
suite-wide safety net for what would catch an accidental live call anyway):

  - find_missing_sequels()             (Sequel Huntarr - PR2 step 1 extraction target)
  - find_horizon_movies()              (Horizon Huntarr - PR2 step 2 extraction target)
  - categorize_by_streaming_service()  (PR2 step 3 extraction target)
  - generate_markdown() / generate_combined_html() (recommenders/external_render.py -
    NOT an extraction target itself, but the final artifact the three
    functions above feed into; byte-identical HTML/MD across a PR is the
    actual regression signal this harness exists to catch)

Deliberately NOT exercised here: find_similar_content_with_profile()'s
iterative TMDB-Discover candidate loop, and build_user_profile()'s Plex
watch-history scan. Neither is a PR2 extraction/relocation target in this
sequence (build_user_profile is only being evaluated for a possible
call-shared-code reconciliation, not moved) - see PR2's report for the
build_user_profile finding, and tests/test_external.py's own
TestFindSimilarContentThinProfile/build_user_profile tests for that
narrower, already-existing regression coverage. Faithfully mocking TMDB
Discover's paginated candidate stream here would add substantial new
fixture surface for uncertain marginal protection value on functions this
PR sequence isn't touching.

Fixture shape (all synthetic, invented titles/ids - never real watch
history):
  - One movie library with 2 items: a standalone movie (tmdb_id=1, no
    collection) and a collected movie (tmdb_id=2, owns 1 of 3 parts of the
    "Rogue Chronicles" collection). The collection's other 2 parts are a
    released-but-missing sequel (tmdb_id=3 - feeds Sequel Huntarr) and an
    unreleased one (tmdb_id=4, no release year yet - feeds Horizon
    Huntarr). tv_library_name is deliberately "" (Sequel Huntarr's
    TV-special cross-check path is skipped - already covered by
    tests/test_external.py's own unit tests; out of scope for this
    harness, see PR2's report).
  - 3 standalone "recommended" movies (tmdb_id=10/11/12) run through
    categorize_by_streaming_service to exercise all three buckets
    (user_services / other_services / acquire), plus 1 TV show
    (tmdb_id=20) for the show-categorization path.
  - Both Sequel/Horizon Huntarr results AND the categorized
    movies/shows feed generate_markdown() (per-user .md) and
    generate_combined_html() (combined .html) for real, so a behavior
    change in any of the four functions above shows up as a byte-level
    diff in the rendered artifacts - not just in an intermediate dict.

No PYTHONHASHSEED pinning needed (unlike tests/harness.py): nothing in
this call graph sums floats in an order driven by string-hash-randomized
dict/set iteration - every dict/set with output-visible ordering here is
keyed by int TMDB ids (CPython's int hash is its own value, never
randomized) or built by iterating a fixed-order list.

Usage (module, not script - see tests/harness.py's identical convention):

    python -m tests.golden_external_harness              # print JSON diff-summary to stdout
    python -m tests.golden_external_harness --write       # (re)write golden fixture files

Two invocations must be byte-identical (see tests/test_golden_external_harness.py
for the assertions this backs) - datetime.now() is pinned to a fixed value
for the duration of the run (both generate_markdown/generate_combined_html
embed a live "generated at" timestamp via datetime.now() - see
recommenders/external_render.py - so without pinning it, no two runs on
different days could ever match) via a datetime subclass patched in for
recommenders.external_render.datetime.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recommenders.external import (  # noqa: E402
    categorize_by_streaming_service,
    find_horizon_movies,
    find_missing_sequels,
)
from recommenders.external_render import generate_combined_html, generate_markdown  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "external_golden")

# Low-entropy placeholder (matches tests/test_external.py's own
# tmdb_api_key="test_key" convention) - never a real key, but also
# deliberately not a long random-looking string, which would otherwise
# trip gitleaks' generic-high-entropy-secret rule despite the .gitleaks.toml
# tmdb-api-key rule itself (looking for a real 32-hex-char key) not matching.
TMDB_API_KEY = "test_key"

# Pinned "now" - see module docstring. Arbitrary, fixed value; not
# meaningful beyond "always the same across runs".
_FIXED_NOW = datetime(2026, 1, 15, 12, 0, 0)

# Fixed added_date for every synthetic "recommended" item below, so
# generate_markdown/generate_combined_html's "days on list" column is
# deterministic against _FIXED_NOW.
_FIXED_ADDED_DATE = datetime(2026, 1, 1, 9, 0, 0).isoformat()


class _FixedDateTime(datetime):
    """Patched in for recommenders.external_render.datetime so its
    datetime.now() calls return a pinned value - see module docstring."""

    @classmethod
    def now(cls, tz=None):
        return _FIXED_NOW


def _guid(tmdb_id):
    guid = Mock()
    guid.id = f"tmdb://{tmdb_id}"
    return guid


def _library_item(tmdb_id):
    item = Mock()
    item.guids = [_guid(tmdb_id)]
    return item


def _plex_with_movies(movie_items, library_name="Movies"):
    """Fake PlexServer: plex.library.section(library_name) returns a
    section wrapping movie_items; any other library name raises (Sequel
    Huntarr's TV-specials check is skipped via tv_library_name="", so this
    should never be asked for one - see module docstring)."""
    section = Mock()
    section.all.return_value = movie_items

    def _section(name):
        if name == library_name:
            return section
        raise AssertionError(f"golden harness fixture has no library configured for: {name!r}")

    plex = Mock()
    plex.library.section.side_effect = _section
    return plex


def _tmdb_response(data):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = data
    return resp


def _strict_get_dispatcher(url_map):
    """requests.get side_effect that routes by exact URL, raising loudly
    on anything not in url_map instead of silently fabricating data -
    mirrors tests/test_external.py's identical helper."""

    def _fake_get(url, params=None, timeout=None):
        if url not in url_map:
            raise AssertionError(f"Unexpected TMDB URL requested by golden harness: {url}")
        return url_map[url]

    return _fake_get


def _build_url_map():
    """Fixed TMDB responses covering the whole harness run: the shared
    "Rogue Chronicles" collection (movies 1/2/3/4) for Sequel + Horizon
    Huntarr, and 3 movies + 1 show's watch/providers for
    categorize_by_streaming_service."""
    collection_parts = [
        {"id": 2, "title": "Rogue Chronicles: Origins", "release_date": "2015-03-01", "genre_ids": [18]},
        {"id": 3, "title": "Rogue Chronicles: Ascension", "release_date": "2018-07-04", "genre_ids": [28]},
        # No release_date/year yet - unreleased, feeds Horizon Huntarr,
        # excluded from Sequel Huntarr's released_movies filter.
        {"id": 4, "title": "Rogue Chronicles: Horizon", "release_date": "", "genre_ids": [12]},
    ]
    return {
        # Sequel/Horizon Huntarr library scan (movie 1: no collection; movie 2: in collection 100)
        "https://api.themoviedb.org/3/movie/1": _tmdb_response({}),
        "https://api.themoviedb.org/3/movie/2": _tmdb_response({"belongs_to_collection": {"id": 100}}),
        "https://api.themoviedb.org/3/collection/100": _tmdb_response(
            {"id": 100, "name": "Rogue Chronicles Collection", "parts": collection_parts}
        ),
        # Sequel Huntarr's watch-providers lookup for the missing sequel (movie 3)
        "https://api.themoviedb.org/3/movie/3/watch/providers": _tmdb_response(
            {"results": {"US": {"flatrate": [{"provider_id": 8}]}}}  # netflix
        ),
        # Horizon Huntarr's live status re-check for the unreleased movie (movie 4)
        "https://api.themoviedb.org/3/movie/4": _tmdb_response(
            {"status": "In Production", "release_date": "2027-06-01"}
        ),
        # categorize_by_streaming_service's watch-providers lookups
        "https://api.themoviedb.org/3/movie/10/watch/providers": _tmdb_response(
            {"results": {"US": {"flatrate": [{"provider_id": 8}]}}}  # netflix - user's service
        ),
        "https://api.themoviedb.org/3/movie/11/watch/providers": _tmdb_response(
            {"results": {"US": {"flatrate": [{"provider_id": 384}]}}}  # max - other service
        ),
        "https://api.themoviedb.org/3/movie/12/watch/providers": _tmdb_response(
            {"results": {"US": {}}}  # nothing - acquire bucket
        ),
        "https://api.themoviedb.org/3/tv/20/watch/providers": _tmdb_response(
            {"results": {"US": {"flatrate": [{"provider_id": 8}]}}}  # netflix
        ),
    }


def _recommended_movie(tmdb_id, title, year, rating, score):
    return {
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year,
        "rating": rating,
        "score": score,
        "vote_count": 500,
        "original_language": "en",
        "added_date": _FIXED_ADDED_DATE,
    }


def _recommended_show(tmdb_id, title, year, rating, score):
    # Same shape as a recommended movie - categorize_by_streaming_service/
    # generate_markdown/generate_combined_html treat movies and shows
    # identically past this point.
    return _recommended_movie(tmdb_id, title, year, rating, score)


def _get_imdb_id_stub(api_key, tmdb_id, media_type):
    """Deterministic fake IMDB id lookup - never reaches TMDB for real."""
    return f"tt{int(tmdb_id):07d}"


def run() -> dict:
    """Run the full fixed pipeline and return a JSON-serializable dict of
    every intermediate result plus the rendered .md/.html file contents."""
    from recommenders import external as external_module

    external_module._watch_provider_cache.clear()

    tmp_root = tempfile.mkdtemp(prefix="curatarr_golden_external_")
    try:
        url_map = _build_url_map()
        plex = _plex_with_movies([_library_item(1), _library_item(2)])

        with (
            # find_missing_sequels() looks up get_project_root() via its own
            # module (recommenders.huntarr)'s namespace since the PR2 Huntarr
            # extraction - patching recommenders.external.get_project_root
            # alone no longer reaches it (unlike requests.get, which is a
            # shared module singleton regardless of which file imports it).
            # find_horizon_movies() (still in recommenders.external) still
            # needs its own patch target too.
            patch("recommenders.external.get_project_root", return_value=tmp_root),
            patch("recommenders.huntarr.get_project_root", return_value=tmp_root),
            patch("recommenders.external.requests.get", side_effect=_strict_get_dispatcher(url_map)),
        ):
            missing_sequels = find_missing_sequels(TMDB_API_KEY, plex, "Movies", "", ["netflix"])
            horizon_movies = find_horizon_movies(TMDB_API_KEY, plex, "Movies")

            recommended_movies = [
                _recommended_movie(10, "Signal Drift", "2024", 7.2, 0.81),
                _recommended_movie(11, "Glass Horizon", "2023", 6.8, 0.77),
                _recommended_movie(12, "Quiet Static", "2022", 6.1, 0.69),
            ]
            recommended_shows = [
                _recommended_show(20, "Nightfall Station", "2021", 8.0, 0.85),
            ]

            movies_categorized = categorize_by_streaming_service(recommended_movies, TMDB_API_KEY, ["netflix"], "movie")
            shows_categorized = categorize_by_streaming_service(recommended_shows, TMDB_API_KEY, ["netflix"], "tv")

        output_dir = os.path.join(tmp_root, "output")

        with patch("recommenders.external_render.datetime", _FixedDateTime):
            md_path = generate_markdown("alice", "Alice", movies_categorized, shows_categorized, output_dir)

            all_users_data = [
                {
                    "username": "alice",
                    "display_name": "Alice",
                    "user_services": ["netflix"],
                    "movies_categorized": movies_categorized,
                    "shows_categorized": shows_categorized,
                }
            ]
            html_path = generate_combined_html(
                all_users_data,
                output_dir,
                TMDB_API_KEY,
                _get_imdb_id_stub,
                movie_counts={},
                show_counts={},
                total_users=1,
                missing_sequels=missing_sequels,
                horizon_movies=horizon_movies,
            )

        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        return {
            "missing_sequels": missing_sequels,
            "horizon_movies": horizon_movies,
            "movies_categorized": movies_categorized,
            "shows_categorized": shows_categorized,
            "watchlist_md": md_content,
            "watchlist_html": html_content,
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _golden_path(name: str) -> str:
    return os.path.join(GOLDEN_DIR, name)


def write_golden(result: dict) -> None:
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    with open(_golden_path("golden_watchlist.md"), "w", encoding="utf-8", newline="") as f:
        f.write(result["watchlist_md"])
    with open(_golden_path("golden_watchlist.html"), "w", encoding="utf-8", newline="") as f:
        f.write(result["watchlist_html"])
    with open(_golden_path("golden_huntarr.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(result["missing_sequels"], f, indent=2, sort_keys=True)
        f.write("\n")
    with open(_golden_path("golden_horizon.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(result["horizon_movies"], f, indent=2, sort_keys=True)
        f.write("\n")
    with open(_golden_path("golden_categorized.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(
            {"movies": result["movies_categorized"], "shows": result["shows_categorized"]},
            f,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")


def load_golden() -> dict:
    with open(_golden_path("golden_watchlist.md"), "r", encoding="utf-8") as f:
        watchlist_md = f.read()
    with open(_golden_path("golden_watchlist.html"), "r", encoding="utf-8") as f:
        watchlist_html = f.read()
    with open(_golden_path("golden_huntarr.json"), "r", encoding="utf-8") as f:
        missing_sequels = json.load(f)
    with open(_golden_path("golden_horizon.json"), "r", encoding="utf-8") as f:
        horizon_movies = json.load(f)
    with open(_golden_path("golden_categorized.json"), "r", encoding="utf-8") as f:
        categorized = json.load(f)
    return {
        "missing_sequels": missing_sequels,
        "horizon_movies": horizon_movies,
        "movies_categorized": categorized["movies"],
        "shows_categorized": categorized["shows"],
        "watchlist_md": watchlist_md,
        "watchlist_html": watchlist_html,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="(Re)write golden fixture files from current code")
    args = parser.parse_args()

    result = run()
    if args.write:
        write_golden(result)
        print(f"Wrote golden fixtures to {GOLDEN_DIR}")
    else:
        summary = {
            "num_missing_sequels": len(result["missing_sequels"]),
            "num_horizon_movies": len(result["horizon_movies"]),
            "num_movies_categorized_all_items": len(result["movies_categorized"]["all_items"]),
            "num_shows_categorized_all_items": len(result["shows_categorized"]["all_items"]),
            "watchlist_md_len": len(result["watchlist_md"]),
            "watchlist_html_len": len(result["watchlist_html"]),
        }
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
