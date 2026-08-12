# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Fully-synthetic Plex fixture for tests/test_e2e_pipeline.py.

Builds a small-but-realistic movie/TV catalog, a synthetic per-user watch
history, and a duck-typed plexapi object tree (FakePlexServer et al.) so the
end-to-end pipeline test can drive the REAL recommendation pipeline
(recommenders/movie.py, recommenders/tv.py, recommenders/base.py,
utils/scoring.py, utils/plex.py's watch-history/account-id business logic)
without ever opening a real socket to a Plex server.

Design choices (see tests/test_e2e_pipeline.py's module docstring for the
full rationale):

  - The movie/show metadata cache (cache/all_movies_cache.json /
    all_shows_cache.json) is pre-seeded with this module's catalog, with
    `library_count` and item keys matching the FakePlexServer's catalog
    exactly. That makes recommenders/base.py's BaseCache.update_cache()
    take its real "cache is up to date" branch (current_count ==
    cache['library_count'], current_ids == existing_ids) - a genuine
    production code path - so no TMDB HTTP calls are needed to populate
    genres/cast/directors/keywords/ratings. This mirrors tests/harness.py's
    own established convention of feeding the scorer a cache-shaped
    fixture rather than re-deriving it from Plex+TMDB on every test run.

  - The only Plex surface actually exercised for real is the watch-history/
    account-id HTTP layer in utils/plex.py (get_plex_account_ids,
    get_watched_movie_count/get_watched_show_count,
    fetch_plex_watch_history_movies/shows) - these are real, unmocked
    business logic (XML parsing, per-account matching, recency timestamps)
    running against synthetic-but-real-shaped XML served by
    make_fake_capped_get() below, plus library.section(...).all() serving
    this module's catalog as duck-typed Plex items.
"""

from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from plexapi.exceptions import NotFound

from utils.config import CACHE_VERSION

# ---------------------------------------------------------------------------
# Account identities - shared between the fake /accounts XML and the fake
# MyPlexAccount.users() list, so utils/plex.py's two independent account-id
# resolution paths (get_plex_account_ids's raw-XML lookup and
# _resolve_myplex_account_ids's MyPlexAccount-object lookup) agree.
# ---------------------------------------------------------------------------
ACCOUNT_IDS = {"alice": "101", "bob": "102"}


# ---------------------------------------------------------------------------
# Movie catalog
# ---------------------------------------------------------------------------
def _m(rating_key, title, year, genres, director, cast, keywords, rating, vote_count) -> Dict:
    return {
        "rating_key": str(rating_key),
        "title": title,
        "year": year,
        "genres": genres,
        "directors": [director],
        "cast": cast,
        "summary": f"Synthetic fixture summary for {title}.",
        "language": "English",
        "tmdb_keywords": keywords,
        "tmdb_id": rating_key,
        "imdb_id": f"tt{rating_key}",
        "rating": rating,
        "vote_count": vote_count,
        "collection_id": None,
        "collection_name": None,
        "ratings": {"audience_rating": rating} if rating else {},
    }


# Watched by alice (action/sci-fi leaning profile).
MOVIE_CATALOG: List[Dict] = [
    _m(
        101,
        "Skyfire Protocol",
        2019,
        ["action", "sci-fi"],
        "Ridley Stone",
        ["Jordan Vance", "Casey Rhodes", "Alex Monroe"],
        ["heist", "space-station"],
        8.2,
        900,
    ),
    _m(
        102,
        "Iron Horizon",
        2018,
        ["action"],
        "Ridley Stone",
        ["Jordan Vance", "Sam Kestrel"],
        ["revenge", "survival"],
        7.9,
        700,
    ),
    _m(
        103,
        "Quantum Drift",
        2020,
        ["sci-fi"],
        "Marcus Webb",
        ["Alex Monroe", "Robin Alvarez"],
        ["time-travel", "space-station"],
        8.5,
        1200,
    ),
    _m(
        104,
        "Night Convoy",
        2017,
        ["action"],
        "Nora Lin",
        ["Casey Rhodes", "Taylor Nguyen"],
        ["road-trip", "undercover"],
        7.4,
        500,
    ),
    _m(
        105,
        "Starless Engine",
        2021,
        ["sci-fi"],
        "Marcus Webb",
        ["Jordan Vance", "Robin Alvarez"],
        ["survival", "space-station"],
        8.0,
        650,
    ),
    _m(
        106,
        "Redline Pursuit",
        2016,
        ["action"],
        "Priya Anand",
        ["Sam Kestrel", "Alex Monroe"],
        ["heist", "undercover"],
        7.6,
        400,
    ),
    # Watched by bob (comedy/romance leaning profile).
    _m(
        107,
        "The Wedding Detour",
        2019,
        ["comedy", "romance"],
        "Nora Lin",
        ["Robin Alvarez", "Taylor Nguyen"],
        ["wedding", "road-trip"],
        7.0,
        300,
    ),
    _m(
        108,
        "Laugh Track",
        2015,
        ["comedy"],
        "Priya Anand",
        ["Casey Rhodes", "Sam Kestrel"],
        ["coming-of-age"],
        6.8,
        250,
    ),
    _m(109, "Second Chances", 2018, ["romance"], "Nora Lin", ["Alex Monroe", "Taylor Nguyen"], ["wedding"], 7.2, 350),
    _m(
        110,
        "Office Antics",
        2020,
        ["comedy"],
        "Marcus Webb",
        ["Jordan Vance", "Sam Kestrel"],
        ["coming-of-age"],
        6.5,
        200,
    ),
    # Horror - excluded for everyone via general.exclude_genre.
    _m(111, "Bloodmoon Manor", 2014, ["horror"], "Priya Anand", ["Robin Alvarez"], ["survival"], 6.9, 300),
    _m(112, "The Hollow Woods", 2013, ["horror"], "Ridley Stone", ["Sam Kestrel"], ["survival"], 7.1, 400),
    _m(113, "Static Screams", 2019, ["horror"], "Marcus Webb", ["Casey Rhodes"], ["undercover"], 6.4, 280),
    _m(114, "Widow's Peak Asylum", 2012, ["horror"], "Nora Lin", ["Alex Monroe"], ["survival"], 7.3, 320),
    # Below the movies.quality_filters threshold (min_rating 5.0 / min_vote_count 50).
    _m(115, "Forgettable Flick One", 2011, ["drama"], "Priya Anand", ["Taylor Nguyen"], ["coming-of-age"], 3.5, 20),
    _m(116, "Forgettable Flick Two", 2010, ["action"], "Ridley Stone", ["Jordan Vance"], ["heist"], 4.0, 15),
    _m(117, "Obscure Indie Three", 2022, ["comedy"], "Marcus Webb", ["Sam Kestrel"], ["road-trip"], 8.0, 10),
    _m(118, "Straight to Video Four", 2009, ["sci-fi"], "Nora Lin", ["Robin Alvarez"], ["space-station"], 2.9, 500),
    # Romance - excluded only for bob (per-user users.preferences.bob.exclude_genres).
    _m(119, "Autumn Letters", 2017, ["romance"], "Nora Lin", ["Taylor Nguyen", "Alex Monroe"], ["wedding"], 7.8, 600),
    _m(
        120,
        "Two If By Sea",
        2016,
        ["romance"],
        "Priya Anand",
        ["Robin Alvarez", "Casey Rhodes"],
        ["road-trip"],
        7.5,
        550,
    ),
    # Unwatched by both, above quality threshold - the real candidate pool.
    _m(
        121,
        "Vertical Descent",
        2021,
        ["action", "sci-fi"],
        "Ridley Stone",
        ["Jordan Vance", "Alex Monroe"],
        ["heist", "space-station"],
        8.1,
        800,
    ),
    _m(
        122,
        "The Last Convoy",
        2020,
        ["action"],
        "Marcus Webb",
        ["Casey Rhodes", "Sam Kestrel"],
        ["road-trip", "revenge"],
        7.7,
        650,
    ),
    _m(
        123,
        "Orbital Decay",
        2022,
        ["sci-fi"],
        "Nora Lin",
        ["Robin Alvarez", "Jordan Vance"],
        ["time-travel", "survival"],
        8.3,
        900,
    ),
    _m(
        124,
        "Comic Timing",
        2019,
        ["comedy"],
        "Priya Anand",
        ["Taylor Nguyen", "Sam Kestrel"],
        ["coming-of-age"],
        7.0,
        400,
    ),
    _m(
        125,
        "The Understudy",
        2018,
        ["drama"],
        "Marcus Webb",
        ["Alex Monroe", "Casey Rhodes"],
        ["coming-of-age"],
        7.4,
        380,
    ),
    _m(
        126,
        "Deep Cover Protocol",
        2021,
        ["action"],
        "Ridley Stone",
        ["Sam Kestrel", "Robin Alvarez"],
        ["undercover", "heist"],
        7.9,
        700,
    ),
    _m(
        127,
        "Signal Loss",
        2020,
        ["sci-fi"],
        "Nora Lin",
        ["Jordan Vance", "Taylor Nguyen"],
        ["space-station", "survival"],
        8.0,
        750,
    ),
    _m(128, "The Punchline", 2017, ["comedy"], "Priya Anand", ["Casey Rhodes", "Alex Monroe"], ["road-trip"], 6.9, 310),
    _m(
        129, "Backroad Requiem", 2016, ["drama"], "Marcus Webb", ["Robin Alvarez", "Sam Kestrel"], ["revenge"], 7.6, 420
    ),
    _m(
        130,
        "Chrome Getaway",
        2023,
        ["action"],
        "Ridley Stone",
        ["Taylor Nguyen", "Jordan Vance"],
        ["heist", "road-trip"],
        8.4,
        950,
    ),
]

MOVIE_WATCHED_BY = {
    "alice": ["101", "102", "103", "104", "105", "106"],
    "bob": ["107", "108", "109", "110"],
}

MOVIE_HORROR_IDS = {"111", "112", "113", "114"}
MOVIE_QUALITY_EXCLUDED_IDS = {"115", "116", "117", "118"}
MOVIE_ROMANCE_IDS = {"119", "120"}


# ---------------------------------------------------------------------------
# Per-user library state (#273) - viewCount/userRating are PER-ACCOUNT Plex
# state: the admin's own token can only ever see the admin's own values for
# these two attributes on a library item, never another user's (see
# recommenders/base.py's _get_all_library_items_for_user and this repo's
# CHANGELOG for the verified real-library finding this fixture reproduces).
# alice and bob below get DELIBERATELY DIFFERENT, nonzero values on movies
# they've each actually watched (see MOVIE_WATCHED_BY above) - a builder
# that reads this state through the wrong (shared admin-token) connection
# can never tell them apart (build_fake_plex_server() below always reports
# 0/None for every item, admin included); one that switches to each user's
# OWN connection first (build_fake_plex_server_for_user() below) can.
# ---------------------------------------------------------------------------
MOVIE_PER_USER_LIBRARY_STATE: Dict[str, Dict[int, Dict]] = {
    "alice": {
        101: {"view_count": 3, "user_rating": 9.0},  # loved it, rewatched
        102: {"view_count": 1, "user_rating": 2.0},  # hated it - negative signal once sourced correctly
        # Remaining alice-watched movies (see MOVIE_WATCHED_BY) get a plain
        # baseline view_count=1/no rating - just enough for
        # FakeSection.search(unwatched=False) (the managed-users path) to
        # correctly report them as watched, without the 101/102 overrides'
        # differentiated signal.
        103: {"view_count": 1, "user_rating": None},
        104: {"view_count": 1, "user_rating": None},
        105: {"view_count": 1, "user_rating": None},
        106: {"view_count": 1, "user_rating": None},
    },
    "bob": {
        107: {"view_count": 2, "user_rating": 8.0},
        108: {"view_count": 1, "user_rating": None},
        109: {"view_count": 1, "user_rating": None},
        110: {"view_count": 1, "user_rating": None},
    },
}


# ---------------------------------------------------------------------------
# TV catalog
# ---------------------------------------------------------------------------
def _s(rating_key, title, year, genres, studio, cast, keywords) -> Dict:
    return {
        "rating_key": str(rating_key),
        "title": title,
        "year": year,
        "genres": genres,
        "studio": studio,
        "cast": cast,
        "summary": f"Synthetic fixture summary for {title}.",
        "language": "English",
        "tmdb_keywords": keywords,
        "tmdb_id": rating_key,
        "imdb_id": f"tt{rating_key}",
        "production_company_ids": [],
    }


SHOW_CATALOG: List[Dict] = [
    # Watched by alice (sci-fi/crime leaning profile).
    _s(
        201,
        "Frontier Signal",
        2019,
        ["sci-fi"],
        "Northgate Studios",
        ["Mira Solano", "Derek Cho"],
        ["first-contact", "exploration"],
    ),
    _s(
        202,
        "Cold Case Vienna",
        2018,
        ["crime"],
        "Bluecrest Media",
        ["Elena Frost", "Theo Baptiste"],
        ["investigation", "noir"],
    ),
    _s(
        203,
        "Deep Range",
        2020,
        ["sci-fi"],
        "Ferrous Pictures",
        ["Nadia Okafor", "Lucas Wren"],
        ["exploration", "survival"],
    ),
    _s(
        204,
        "The Ledger",
        2017,
        ["crime"],
        "Northgate Studios",
        ["Derek Cho", "Mira Solano"],
        ["heist", "investigation"],
    ),
    # Watched by bob (comedy leaning profile).
    _s(
        205,
        "Sitcom Heights",
        2016,
        ["comedy"],
        "Lumen Works",
        ["Theo Baptiste", "Elena Frost"],
        ["roommates", "workplace"],
    ),
    _s(206, "Roommate Chaos", 2015, ["comedy"], "Bluecrest Media", ["Lucas Wren", "Nadia Okafor"], ["roommates"]),
    _s(207, "The Office Pool", 2020, ["comedy"], "Lumen Works", ["Mira Solano", "Theo Baptiste"], ["workplace"]),
    # Horror - excluded for everyone via general.exclude_genre.
    _s(208, "Night Terrors Manor", 2014, ["horror"], "Ferrous Pictures", ["Derek Cho"], ["haunting"]),
    _s(209, "The Beckoning", 2013, ["horror"], "Northgate Studios", ["Elena Frost"], ["haunting"]),
    _s(210, "Static Hour", 2019, ["horror"], "Bluecrest Media", ["Nadia Okafor"], ["haunting"]),
    # Unwatched by both - the real candidate pool.
    _s(
        211,
        "Nova Drift",
        2021,
        ["sci-fi"],
        "Ferrous Pictures",
        ["Lucas Wren", "Mira Solano"],
        ["exploration", "first-contact"],
    ),
    _s(212, "Precinct 44", 2020, ["crime"], "Northgate Studios", ["Theo Baptiste", "Derek Cho"], ["investigation"]),
    _s(213, "Laugh Riot", 2022, ["comedy"], "Lumen Works", ["Elena Frost", "Nadia Okafor"], ["workplace"]),
    _s(214, "Family Ties Reimagined", 2019, ["drama"], "Bluecrest Media", ["Mira Solano", "Lucas Wren"], ["family"]),
    _s(215, "Enchanted Vale", 2021, ["fantasy"], "Ferrous Pictures", ["Derek Cho", "Elena Frost"], ["magic", "quest"]),
    _s(
        216,
        "The Long Con",
        2018,
        ["crime"],
        "Northgate Studios",
        ["Nadia Okafor", "Theo Baptiste"],
        ["heist", "investigation"],
    ),
    _s(
        217,
        "Orbit Station Zero",
        2020,
        ["sci-fi"],
        "Lumen Works",
        ["Mira Solano", "Derek Cho"],
        ["exploration", "survival"],
    ),
    _s(218, "Sketch Night Live", 2017, ["comedy"], "Bluecrest Media", ["Lucas Wren", "Elena Frost"], ["workplace"]),
    _s(219, "The Quiet Ward", 2016, ["drama"], "Ferrous Pictures", ["Theo Baptiste", "Nadia Okafor"], ["family"]),
    _s(
        220, "Dragon's Hollow", 2022, ["fantasy"], "Northgate Studios", ["Derek Cho", "Mira Solano"], ["magic", "quest"]
    ),
]

SHOW_WATCHED_BY = {
    "alice": ["201", "202", "203", "204"],
    "bob": ["205", "206", "207"],
}

SHOW_HORROR_IDS = {"208", "209", "210"}

SHOW_PER_USER_LIBRARY_STATE: Dict[str, Dict[int, Dict]] = {
    "alice": {
        201: {"view_count": 2, "user_rating": 10.0},
        202: {"view_count": 1, "user_rating": 1.0},  # hated it
        203: {"view_count": 1, "user_rating": None},
        204: {"view_count": 1, "user_rating": None},
    },
    "bob": {
        205: {"view_count": 3, "user_rating": 7.0},
        206: {"view_count": 1, "user_rating": None},
        207: {"view_count": 1, "user_rating": None},
    },
}


# ---------------------------------------------------------------------------
# Pre-seeded media caches - see module docstring for why this replaces the
# TMDB-enrichment stage with a fixture, mirroring tests/harness.py.
# ---------------------------------------------------------------------------
def build_movies_cache_payload() -> Dict:
    movies = {m["rating_key"]: {k: v for k, v in m.items() if k != "rating_key"} for m in MOVIE_CATALOG}
    return {
        "movies": movies,
        "last_updated": "2026-01-01T00:00:00",
        "library_count": len(MOVIE_CATALOG),
        "cache_version": CACHE_VERSION,
    }


def build_shows_cache_payload() -> Dict:
    shows = {s["rating_key"]: {k: v for k, v in s.items() if k != "rating_key"} for s in SHOW_CATALOG}
    return {
        "shows": shows,
        "last_updated": "2026-01-01T00:00:00",
        "library_count": len(SHOW_CATALOG),
        "cache_version": CACHE_VERSION,
    }


# ---------------------------------------------------------------------------
# Duck-typed plexapi object tree
# ---------------------------------------------------------------------------
class FakeGuid:
    def __init__(self, guid_id: str):
        self.id = guid_id


class FakeMediaItem:
    """Duck-typed stand-in for a plexapi Movie/Show object.

    Originally minimal (ratingKey/title/year/guids/viewCount only - see
    git history), extended for #273's profile-builder harness
    (tests/harness.py's run_profile_builders()) to also carry a settable
    userRating (not just a hardcoded None) plus the handful of extra
    attributes recommenders/external.py's build_user_profile() reads
    directly off a library item (isWatched, genres/directors/roles as
    empty-by-default lists, lastViewedAt, audienceRating) - that
    function is the one #273 builder that reads a RICHER item shape
    than the other three, since it (still) does its own from-scratch
    Plex scan rather than going through the pre-seeded movie/show cache
    the other three consume via self._get_media_cache().
    BaseCache._process_item() itself still never runs here (the cache
    stays pre-seeded/up-to-date - see module docstring), so
    genres/directors/roles default to empty rather than modeling this
    fixture's real catalog data.
    """

    def __init__(
        self,
        rating_key: str,
        title: str,
        year: Optional[int],
        view_count: int = 0,
        user_rating: Optional[float] = None,
    ):
        self.ratingKey = int(rating_key)
        self.title = title
        self.year = year
        self.guids = [FakeGuid(f"imdb://tt{rating_key}"), FakeGuid(f"tmdb://{rating_key}")]
        self.viewCount = view_count
        self.userRating = user_rating
        # Real plexapi computes isWatched from viewCount>0 server-side;
        # mirrored here (rather than hardcoded True/False) so a fixture
        # item with a nonzero view_count (per-user overrides below) is
        # correctly "watched" for build_user_profile()'s own isWatched gate.
        self.isWatched = view_count > 0
        self.lastViewedAt = None
        self.audienceRating = None
        self.genres: list = []
        self.directors: list = []
        self.roles: list = []

    def reload(self):
        """No-op - real code only calls this on items about to be
        label/collection-written, a stage this test never reaches for
        real (see FakeSection.search/FakePlexServer.fetchItem below)."""


class FakeSection:
    def __init__(self, key: str, items: List[FakeMediaItem]):
        self.key = key
        self._items = items

    def all(self) -> List[FakeMediaItem]:
        return list(self._items)

    def search(self, **kwargs):
        # #273: recommenders/base.py's _get_managed_users_watched_data()
        # calls section.search(unwatched=False) to fetch a (switchUser-
        # scoped) user's own watched items - a real, legitimate caller,
        # not the label/collection-writing stage the AssertionError below
        # guards against. Narrow, exact-kwarg-shape check so any OTHER
        # (unexpected) search() call still raises as before.
        if set(kwargs.keys()) == {"unwatched"}:
            want_unwatched = kwargs["unwatched"]
            return [item for item in self._items if (item.viewCount > 0) != want_unwatched]
        raise AssertionError(
            "FakeSection.search() was called - the label/collection-writing stage "
            "must stay mocked in this test (patch PlexMovieRecommender/"
            "PlexTVRecommender.manage_plex_labels instead of letting it reach here)."
        )


class FakeLibrary:
    def __init__(self, sections: Dict[str, FakeSection]):
        self._sections = sections

    def section(self, name: str) -> FakeSection:
        return self._sections[name]


class FakePlexServer:
    def __init__(self, sections: Dict[str, FakeSection]):
        self.library = FakeLibrary(sections)
        # #273: machineIdentifier is what real plexapi's switchUser()
        # passes to MyPlexUser.get_token() - not used by this fake's own
        # switchUser() below (which skips the token/HTTP hop entirely and
        # returns a per-user server directly), but set for parity with
        # the real attribute in case future code reads it directly.
        self.machineIdentifier = "fake-machine-id"
        # None = this server represents the shared admin-token view
        # (build_fake_plex_server()); set to a username by
        # build_fake_plex_server_for_user() / switchUser() below.
        self.current_user: Optional[str] = None

    def fetchItem(self, rating_key):
        raise AssertionError(
            "FakePlexServer.fetchItem() was called - the label/collection-writing stage must stay mocked in this test."
        )

    def switchUser(self, user):
        """Duck-typed stand-in for plexapi.server.PlexServer.switchUser()
        (#273): real plexapi resolves `user.get_token(machineIdentifier)`
        then reconnects with that token; this fake skips the token/HTTP
        round trip and returns a fully independent per-user server
        directly (see build_fake_plex_server_for_user) - same end result
        (a PlexServer scoped to that user's own account state), narrower
        fake. Keyed off the user's own `.title` (mirrors
        FakeMyPlexAccount.user()'s title-keyed lookup below).
        """
        username = getattr(user, "title", str(user))
        return build_fake_plex_server_for_user(username)


def build_fake_plex_server() -> FakePlexServer:
    movies = [FakeMediaItem(m["rating_key"], m["title"], m["year"]) for m in MOVIE_CATALOG]
    shows = [FakeMediaItem(s["rating_key"], s["title"], s["year"]) for s in SHOW_CATALOG]
    sections = {
        "Movies": FakeSection("1", movies),
        "TV Shows": FakeSection("2", shows),
    }
    return FakePlexServer(sections)


def build_fake_plex_server_for_user(username: str) -> FakePlexServer:
    """A second, independent FakePlexServer scoped to `username`'s own
    Plex account (#273) - what FakePlexServer.switchUser() above returns,
    modeling what a builder correctly using that user's own connection
    (instead of the shared admin-token build_fake_plex_server() above)
    would see: MOVIE_PER_USER_LIBRARY_STATE/SHOW_PER_USER_LIBRARY_STATE's
    view_count/user_rating overrides instead of the admin-view defaults
    (0/None) every item gets otherwise.
    """
    movie_overrides = MOVIE_PER_USER_LIBRARY_STATE.get(username, {})
    show_overrides = SHOW_PER_USER_LIBRARY_STATE.get(username, {})
    movies = [
        FakeMediaItem(
            m["rating_key"],
            m["title"],
            m["year"],
            view_count=movie_overrides.get(int(m["rating_key"]), {}).get("view_count", 0),
            user_rating=movie_overrides.get(int(m["rating_key"]), {}).get("user_rating"),
        )
        for m in MOVIE_CATALOG
    ]
    shows = [
        FakeMediaItem(
            s["rating_key"],
            s["title"],
            s["year"],
            view_count=show_overrides.get(int(s["rating_key"]), {}).get("view_count", 0),
            user_rating=show_overrides.get(int(s["rating_key"]), {}).get("user_rating"),
        )
        for s in SHOW_CATALOG
    ]
    sections = {
        "Movies": FakeSection("1", movies),
        "TV Shows": FakeSection("2", shows),
    }
    server = FakePlexServer(sections)
    server.current_user = username
    return server


class FakeUser:
    def __init__(self, title: str, user_id: int):
        self.title = title
        self.id = user_id


class FakeMyPlexAccount:
    """Stand-in for plexapi.myplex.MyPlexAccount - only the surface
    utils/plex.py's get_configured_users()/_resolve_myplex_account_ids()/
    fetch_plex_watch_history_movies() actually call (.username, .id,
    .users()), plus .user() (#273: recommenders/base.py's
    _get_managed_users_watched_data() and the new
    _get_all_library_items_for_user() both call account.user(username)
    to resolve a MyPlexUser before switchUser())."""

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.username = "owner"
        self.id = 1

    def users(self):
        return [FakeUser(name, int(account_id)) for name, account_id in ACCOUNT_IDS.items()]

    def user(self, username: str) -> FakeUser:
        """Mirrors plexapi.myplex.MyPlexAccount.user()'s title-keyed
        lookup (case-insensitive) and its NotFound-on-miss contract, so
        callers' existing `except plexapi.exceptions.PlexApiException`
        handling is exercised for real on a miss, not just the happy
        path."""
        for name, account_id in ACCOUNT_IDS.items():
            if name.lower() == str(username).lower():
                return FakeUser(name, int(account_id))
        raise NotFound(f"Unable to find user {username}")


# ---------------------------------------------------------------------------
# Fake HTTP layer for utils.plex._capped_get - the raw XML watch-history/
# account-id endpoints bypass plexapi objects entirely in production (see
# utils/plex.py's get_plex_account_ids/get_watched_movie_count/
# fetch_plex_watch_history_movies), so this is the single, narrow seam
# patched to keep that real business logic (XML parsing, per-account
# matching, timestamp tracking) running for real without a socket.
# ---------------------------------------------------------------------------
class FakeXMLResponse:
    def __init__(self, xml_bytes: bytes):
        self.content = xml_bytes
        self.headers: Dict = {}

    def raise_for_status(self):
        return None


def _accounts_xml() -> bytes:
    accounts = "".join(f'<Account id="{account_id}" name="{name}"/>' for name, account_id in ACCOUNT_IDS.items())
    return f"<MediaContainer>{accounts}</MediaContainer>".encode("utf-8")


def _history_xml_for_account(account_id: str) -> bytes:
    username = next((name for name, aid in ACCOUNT_IDS.items() if aid == str(account_id)), None)
    videos = []
    if username:
        for i, rating_key in enumerate(MOVIE_WATCHED_BY.get(username, [])):
            viewed_at = 1700000000 + i * 3600
            videos.append(f'<Video type="movie" ratingKey="{rating_key}" viewedAt="{viewed_at}"/>')
        for i, rating_key in enumerate(SHOW_WATCHED_BY.get(username, [])):
            viewed_at = 1700000000 + i * 3600
            videos.append(
                f'<Video type="episode" grandparentKey="/library/metadata/{rating_key}" '
                f'grandparentRatingKey="{rating_key}" viewedAt="{viewed_at}"/>'
            )
    return f"<MediaContainer>{''.join(videos)}</MediaContainer>".encode("utf-8")


def make_fake_capped_get():
    """Returns a drop-in replacement for utils.plex._capped_get that serves
    synthetic-but-real-shaped Plex XML instead of making a network call."""

    def _fake_capped_get(url, **kwargs):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        params = kwargs.get("params") or {}
        account_id = params.get("accountID") or query.get("accountID", [None])[0]

        if parsed.path.endswith("/accounts"):
            return FakeXMLResponse(_accounts_xml())
        if parsed.path.endswith("/status/sessions/history/all"):
            return FakeXMLResponse(_history_xml_for_account(str(account_id)))

        raise AssertionError(f"Unexpected fake Plex HTTP call: {url} params={params or dict(query)}")

    return _fake_capped_get
