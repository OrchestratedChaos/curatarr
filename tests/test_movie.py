"""
Tests for recommenders/movie.py - Movie recommendation system.
"""

import copy
from collections import Counter
from unittest.mock import MagicMock, Mock, patch

import pytest

from recommenders.movie import (
    MovieCache,
    PlexMovieRecommender,
    format_movie_output,
    main,
    process_recommendations,
)


@pytest.fixture(autouse=True)
def _no_real_plex_watched_lookups(monkeypatch):
    """File-wide safety net: PlexMovieRecommender.__init__ eagerly gathers
    watched data on every construction (there is no "skip watched data"
    mode), which means _get_watched_count() -> get_watched_movie_count()
    and, since none of these tests have a real watched-cache file on
    disk, _get_plex_watched_data() -> get_plex_account_ids() also run for
    real on almost every test in this file. Both are real utils/plex.py
    functions that make outbound network calls (get_watched_movie_count
    constructs a real plexapi MyPlexAccount(token=...), which talks to
    plex.tv; get_plex_account_ids does a real requests.get() against
    config['plex']['url'], which in these tests is "http://localhost")
    unless a test explicitly re-patches them - most tests here don't,
    because they're not testing this behavior.

    Discovered via instrumenting socket.socket.connect: without this,
    most tests in this file open real TCP connections to plex.tv and to
    localhost:80, which is a token/network leak at best and a
    multi-minute CI hang at worst if either connect stalls instead of
    failing fast.

    Defaults both to their "no reachable Plex" return values (0 / [])
    so every test here is network-safe by default. A test that cares
    about the specific watched-count/account-id behavior patches
    recommenders.movie.get_watched_movie_count /
    recommenders.movie.get_plex_account_ids itself (see
    TestPlexMovieRecommenderWatchedCount, TestGetPlexWatchedDataMovie
    below) - that per-test patch is applied after this fixture and so
    overrides it for the life of the test, same layering conftest.py's
    suite-wide safety nets already use.
    """
    monkeypatch.setattr("recommenders.movie.get_watched_movie_count", lambda *a, **kw: 0)
    monkeypatch.setattr("recommenders.movie.get_plex_account_ids", lambda *a, **kw: [])


class TestMovieCache:
    """Tests for MovieCache class."""

    @patch("recommenders.base.load_media_cache")
    def test_movie_cache_attributes(self, mock_load):
        """Test that MovieCache has correct attributes."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = MovieCache("/tmp/cache")

        assert cache.media_type == "movie"
        assert cache.media_key == "movies"
        assert cache.cache_filename == "all_movies_cache.json"

    @patch("recommenders.base.load_media_cache")
    def test_process_item_extracts_directors(self, mock_load):
        """Test that _process_item extracts directors."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = MovieCache("/tmp/cache")

        mock_director = Mock()
        mock_director.tag = "Steven Spielberg"
        mock_movie = Mock()
        mock_movie.title = "Test Movie"
        mock_movie.year = 2024
        mock_movie.summary = "A test movie"
        mock_movie.directors = [mock_director]
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = 7.5
        mock_movie.media = []

        result = cache._process_item(mock_movie, None)

        assert result is not None
        assert "Steven Spielberg" in result["directors"]

    @patch("recommenders.base.load_media_cache")
    def test_process_item_extracts_genres(self, mock_load):
        """Test that _process_item extracts genres."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = MovieCache("/tmp/cache")

        mock_genre = Mock()
        mock_genre.tag = "Action"
        mock_movie = Mock()
        mock_movie.title = "Test Movie"
        mock_movie.year = 2024
        mock_movie.summary = "A test movie"
        mock_movie.directors = []
        mock_movie.genres = [mock_genre]
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = None
        mock_movie.media = []

        result = cache._process_item(mock_movie, None)

        assert "action" in result["genres"]

    @patch("recommenders.base.load_media_cache")
    def test_process_item_extracts_cast(self, mock_load):
        """Test that _process_item extracts cast members."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = MovieCache("/tmp/cache")

        mock_actor = Mock()
        mock_actor.tag = "Tom Hanks"
        mock_movie = Mock()
        mock_movie.title = "Test Movie"
        mock_movie.year = 2024
        mock_movie.summary = "A test movie"
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = [mock_actor]
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = None
        mock_movie.media = []

        result = cache._process_item(mock_movie, None)

        assert "Tom Hanks" in result["cast"]


class TestPlexMovieRecommenderInit:
    """Tests for PlexMovieRecommender initialization."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_init_creates_movie_cache(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that PlexMovieRecommender creates a MovieCache."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "keyword": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        PlexMovieRecommender("/path/to/config.yml")

        mock_cache.assert_called_once()


class TestPlexMovieRecommenderLibraryParam:
    """Tests for PlexMovieRecommender library threading (#157 Phase 3)."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_library_forwarded_to_base(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that the library param reaches BaseRecommender and sets
        library_id/library_title from the library dict, not the legacy
        movie_library config key."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc", "movie_library": "Movies"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "keyword": 0.2},
            "libraries": [
                {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
                {"id": "movies-4k", "name": "Movies 4K", "section": "Movies 4K", "media_type": "movie"},
            ],
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        library = {"id": "movies-4k", "name": "Movies 4K", "section": "Movies 4K", "media_type": "movie"}
        recommender = PlexMovieRecommender("/path/to/config.yml", library=library)

        assert recommender.library_id == "movies-4k"
        assert recommender.library_title == "Movies 4K"

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_no_library_keeps_legacy_title(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        """library=None (default) resolves library_title from the legacy
        movie_library config key, unchanged from before Phase 3."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc", "movie_library": "Movies"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "keyword": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")

        assert recommender.library_id is None
        assert recommender.library_title == "Movies"


class TestPlexMovieRecommenderWeights:
    """Tests for PlexMovieRecommender weight loading."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_loads_weights_from_config(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that weights are loaded from config."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.35, "director": 0.15, "actor": 0.20, "language": 0.10, "tmdb_keywords": 0.20},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")

        assert recommender.weights["genre"] == 0.35
        assert recommender.weights["director"] == 0.15
        assert recommender.weights["actor"] == 0.20

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_uses_default_weights_when_missing(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        """Test that default weights are used when not in config."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {},  # Empty weights
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")

        # Should have default weights
        assert "genre" in recommender.weights
        assert "director" in recommender.weights
        assert "actor" in recommender.weights

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_movies_section_weights_override_legacy_root_weights(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        """movies.weights (documented location) must win over the
        legacy root-level 'weights' key some back-compat configs still have."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.9, "actor": 0.1},
            "movies": {"weights": {"genre": 0.15, "actor": 0.15, "director": 0.05, "keyword": 0.65}},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")

        assert recommender.weights["genre"] == 0.15
        assert recommender.weights["actor"] == 0.15


class TestPlexMovieRecommenderShowDirector:
    """Tests for movies.show_director (config/tuning.example.yml's
    documented location) must be honored, not silently ignored in favor of
    the legacy general.show_director key."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_movies_section_show_director_overrides_general_default(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "movies": {"show_director": True},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "keyword": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")

        assert recommender.show_director is True

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_general_level_show_director_still_honored_when_no_movies_section(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {"show_director": True},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "keyword": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")

        assert recommender.show_director is True


class TestPlexMovieRecommenderLibraryMethods:
    """Tests for PlexMovieRecommender library methods."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_library_movies_set(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test _get_library_movies_set returns movie IDs."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc", "movie_library": "Movies"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}

        mock_movie = Mock()
        mock_movie.ratingKey = 123
        mock_section = Mock()
        mock_section.all.return_value = [mock_movie]
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")
        result = recommender._get_library_movies_set()

        assert 123 in result


class TestPlexMovieRecommenderSimilarity:
    """Tests for PlexMovieRecommender similarity calculation."""

    @patch("recommenders.movie.calculate_similarity_score")
    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_calculate_similarity_from_cache(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_calc
    ):
        """Test _calculate_similarity_from_cache uses cached data."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})
        mock_calc.return_value = (0.75, {"genre": 0.25, "director": 0.15})

        recommender = PlexMovieRecommender("/path/to/config.yml")
        recommender.watched_data_counters = {
            "genres": Counter({"action": 5}),
            "directors": Counter({"spielberg": 3}),
            "actors": Counter(),
            "languages": Counter(),
            "tmdb_keywords": Counter(),
        }

        movie_info = {
            "title": "Test Movie",
            "genres": ["action"],
            "directors": ["spielberg"],
            "cast": [],
            "language": "english",
            "tmdb_keywords": [],
        }

        score, breakdown = recommender._calculate_similarity_from_cache(movie_info)

        assert score == 0.75
        mock_calc.assert_called_once()


class TestPlexMovieRecommenderWatchedCache:
    """Tests for PlexMovieRecommender watched cache methods."""

    @patch("recommenders.base.save_watched_cache")
    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_save_watched_cache(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_save
    ):
        """Test _save_watched_cache saves data correctly."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")
        recommender.watched_data_counters = {"genres": Counter({"action": 5})}
        recommender.watched_movie_ids = {1, 2, 3}
        recommender.cached_watched_count = 10

        mock_save.reset_mock()  # Reset after init
        recommender._save_watched_cache()

        mock_save.assert_called_once()


class TestFormatMovieOutput:
    """Tests for format_movie_output function."""

    def test_format_basic_movie(self):
        """Test basic movie formatting."""
        movie = {"title": "Test Movie", "year": 2024, "similarity_score": 0.85}

        result = format_movie_output(movie, 1)

        assert "Test Movie" in result
        assert "2024" in result
        assert "85" in result

    def test_format_with_genres(self):
        """Test movie formatting with genres."""
        movie = {"title": "Test Movie", "year": 2024, "similarity_score": 0.75, "genres": ["action", "comedy"]}

        result = format_movie_output(movie, 1, show_genres=True)

        assert "action" in result
        assert "comedy" in result

    def test_format_with_cast(self):
        """Test movie formatting with cast."""
        movie = {"title": "Test Movie", "year": 2024, "similarity_score": 0.75, "cast": ["Tom Hanks", "Meg Ryan"]}

        result = format_movie_output(movie, 1, show_cast=True)

        assert "Tom Hanks" in result

    def test_format_with_imdb_link(self):
        """Test movie formatting with IMDB link."""
        movie = {"title": "Test Movie", "year": 2024, "similarity_score": 0.75, "imdb_id": "tt1234567"}

        result = format_movie_output(movie, 1, show_imdb_link=True)

        assert "imdb.com" in result
        assert "tt1234567" in result


class TestProcessRecommendationsLibraryParam:
    """Tests for process_recommendations library forwarding (#157 Phase 3)."""

    @patch("recommenders.movie.record_run_status")
    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_forwards_library_to_recommender(
        self, mock_setup_log, mock_teardown, mock_recommender_cls, mock_record_status
    ):
        """process_recommendations passes library through to
        PlexMovieRecommender's constructor unchanged.

        record_run_status is mocked here for the same reason
        setup_log_file/teardown_log_file already are - this test only
        cares about library forwarding, and conftest.py's
        _isolated_recommender_cache_dir already isolates
        recommenders.movie.get_project_root (log_dir) from the real repo
        as a suite-wide safety net; this adds an explicit, test-local
        guarantee independent of that fixture."""
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {"plex_recommendations": []}
        mock_instance.config = {"general": {}}
        mock_recommender_cls.return_value = mock_instance

        library = {"id": "movies-4k", "name": "Movies 4K", "section": "Movies 4K", "media_type": "movie"}
        process_recommendations({"general": {}}, "/path/to/config.yml", 0, single_user="alice", library=library)

        mock_recommender_cls.assert_called_once_with(
            "/path/to/config.yml", single_user="alice", library=library, library_items_cache=None
        )

    @patch("recommenders.movie.record_run_status")
    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_defaults_library_to_none(self, mock_setup_log, mock_teardown, mock_recommender_cls, mock_record_status):
        """process_recommendations defaults library=None (legacy callers)."""
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {"plex_recommendations": []}
        mock_instance.config = {"general": {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({"general": {}}, "/path/to/config.yml", 0, single_user="alice")

        mock_recommender_cls.assert_called_once_with(
            "/path/to/config.yml", single_user="alice", library=None, library_items_cache=None
        )


class TestMainMediaTypeKey:
    """Tests for main() passing media_type_key (#157 Phase 3)."""

    @patch("recommenders.movie.run_recommender_main")
    def test_main_passes_movie_media_type_key(self, mock_run_main):
        main()

        assert mock_run_main.call_count == 1
        _, kwargs = mock_run_main.call_args
        assert kwargs["media_type_key"] == "movie"
        assert kwargs["process_func"] is process_recommendations


class TestPlexMovieRecommenderWatchedCount:
    """Tests for PlexMovieRecommender._get_watched_count method."""

    @patch("recommenders.movie.get_watched_movie_count")
    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_watched_count_calls_utility(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_count
    ):
        """Test that _get_watched_count uses utility function."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})
        mock_count.return_value = 42

        recommender = PlexMovieRecommender("/path/to/config.yml")
        result = recommender._get_watched_count()

        assert result == 42


class TestPlexMovieRecommenderTmdbMethods:
    """Tests for PlexMovieRecommender TMDB-related methods."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_plex_item_tmdb_id_from_cache(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        """Test _get_plex_item_tmdb_id returns from cache."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")
        recommender.plex_tmdb_cache = {"123": 456}

        mock_movie = Mock()
        mock_movie.ratingKey = 123

        result = recommender._get_plex_item_tmdb_id(mock_movie)

        assert result == 456

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_plex_item_imdb_id_from_guids(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        """Test _get_plex_item_imdb_id extracts from guids."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")

        mock_guid = Mock()
        mock_guid.id = "imdb://tt1234567"
        mock_movie = Mock()
        mock_movie.guids = [mock_guid]

        result = recommender._get_plex_item_imdb_id(mock_movie)

        assert result == "tt1234567"


class TestPlexMovieRecommenderRefreshWatchedData:
    """Tests for PlexMovieRecommender._refresh_watched_data method."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_refresh_clears_data(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test _refresh_watched_data clears existing data."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "keyword": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")
        recommender.watched_ids = {1, 2, 3}
        recommender.watched_data_counters = {"genres": Counter({"action": 5})}

        # Mock the methods called during refresh (base class calls _get_watched_data)
        recommender._get_watched_data = Mock(return_value={"genres": Counter()})
        recommender._save_watched_cache = Mock()

        recommender._refresh_watched_data()

        assert len(recommender.watched_ids) == 0


class TestPlexMovieRecommenderGetRecommendations:
    """Tests for PlexMovieRecommender.get_recommendations method."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_recommendations_returns_dict(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        """Test get_recommendations returns a dict with plex_recommendations."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {"limit_plex_results": 10},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")
        recommender.watched_movie_ids = set()
        recommender.cached_watched_count = 0
        recommender.watched_data = {
            "genres": Counter(),
            "directors": Counter(),
            "actors": Counter(),
            "languages": Counter(),
            "tmdb_keywords": Counter(),
            "collections": Counter(),
        }

        result = recommender.get_recommendations()

        assert isinstance(result, dict)
        assert "plex_recommendations" in result

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_recommendations_excludes_watched(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        """Test get_recommendations excludes watched movies."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {"limit_plex_results": 10},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "keyword": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst

        mock_cache_inst = Mock()
        mock_cache_inst.cache = {
            "movies": {
                "1": {
                    "title": "Watched Movie",
                    "genres": [],
                    "directors": [],
                    "cast": [],
                    "language": "",
                    "tmdb_keywords": [],
                },
                "2": {
                    "title": "Unwatched Movie",
                    "genres": [],
                    "directors": [],
                    "cast": [],
                    "language": "",
                    "tmdb_keywords": [],
                },
            }
        }
        mock_cache_inst._save_cache = Mock()
        mock_cache.return_value = mock_cache_inst

        recommender = PlexMovieRecommender("/path/to/config.yml")
        recommender.watched_ids = {1}
        recommender.cached_watched_count = 1
        recommender.watched_data = {
            "genres": Counter(),
            "directors": Counter(),
            "actors": Counter(),
            "languages": Counter(),
            "tmdb_keywords": Counter(),
            "collections": Counter(),
        }
        recommender._calculate_similarity_from_cache = Mock(return_value=(0.5, {}))

        result = recommender.get_recommendations()

        rec_titles = [r["title"] for r in result["plex_recommendations"]]
        assert "Watched Movie" not in rec_titles


class TestPlexMovieRecommenderCollectionBonus:
    """Tests for collection bonus in similarity calculation."""

    @patch("recommenders.movie.calculate_similarity_score")
    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_collection_bonus_applied(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_calc
    ):
        """Test that collection bonus is applied for movies in watched collections."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_cache.return_value = Mock(cache={"movies": {}})
        mock_calc.return_value = (0.70, {"genre": 0.20, "director": 0.10, "details": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")
        recommender.watched_data = {
            "genres": Counter({"action": 5}),
            "directors": Counter(),
            "actors": Counter(),
            "languages": Counter(),
            "tmdb_keywords": Counter(),
            "collections": Counter({789: 3.0}),  # User watched 3 movies from collection 789
        }

        movie_info = {
            "title": "Sequel Movie",
            "genres": ["action"],
            "directors": [],
            "cast": [],
            "language": "english",
            "tmdb_keywords": [],
            "collection_id": 789,
            "collection_name": "Action Franchise",
        }

        score, breakdown = recommender._calculate_similarity_from_cache(movie_info)

        # Score should be boosted due to collection bonus
        assert score > 0.70
        assert "collection_bonus" in breakdown


class TestMovieCacheRatingExtraction:
    """Tests for MovieCache rating extraction."""

    @patch("recommenders.base.load_media_cache")
    def test_extracts_user_rating(self, mock_load):
        """Test that userRating is extracted."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = MovieCache("/tmp/cache")

        mock_movie = Mock()
        mock_movie.title = "Test Movie"
        mock_movie.year = 2024
        mock_movie.summary = ""
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = 8.5
        mock_movie.audienceRating = None

        result = cache._process_item(mock_movie, None)

        assert result["ratings"]["audience_rating"] == 8.5

    @patch("recommenders.base.load_media_cache")
    def test_extracts_audience_rating_as_fallback(self, mock_load):
        """Test that audienceRating is used as fallback."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = MovieCache("/tmp/cache")

        mock_movie = Mock()
        mock_movie.title = "Test Movie"
        mock_movie.year = 2024
        mock_movie.summary = ""
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = 7.0

        result = cache._process_item(mock_movie, None)

        assert result["ratings"]["audience_rating"] == 7.0

    @patch("recommenders.base.load_media_cache")
    def test_handles_no_rating(self, mock_load):
        """Test handling when no rating is available."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = MovieCache("/tmp/cache")

        mock_movie = Mock()
        mock_movie.title = "Test Movie"
        mock_movie.year = 2024
        mock_movie.summary = ""
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = None

        result = cache._process_item(mock_movie, None)

        assert result["ratings"] == {}


class TestPlexMovieRecommenderExcludedGenres:
    """Tests for genre exclusion in recommendations."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_excludes_configured_genres(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache):
        """Test that configured excluded genres are filtered."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {"exclude_genre": "horror,documentary", "limit_plex_results": 10},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst

        mock_cache_inst = Mock()
        mock_cache_inst.cache = {
            "movies": {
                "1": {
                    "title": "Horror Movie",
                    "genres": ["horror"],
                    "directors": [],
                    "cast": [],
                    "language": "",
                    "tmdb_keywords": [],
                },
                "2": {
                    "title": "Drama Movie",
                    "genres": ["drama"],
                    "directors": [],
                    "cast": [],
                    "language": "",
                    "tmdb_keywords": [],
                },
            }
        }
        mock_cache_inst._save_cache = Mock()
        mock_cache.return_value = mock_cache_inst

        recommender = PlexMovieRecommender("/path/to/config.yml")
        recommender.watched_movie_ids = set()
        recommender.watched_ids = {101, 102}  # #291: non-empty, so the zero-watch-history gate never fires
        recommender.cached_watched_count = 0
        recommender.watched_data = {
            "genres": Counter(),
            "directors": Counter(),
            "actors": Counter(),
            "languages": Counter(),
            "tmdb_keywords": Counter(),
            "collections": Counter(),
        }
        recommender._calculate_similarity_from_cache = Mock(return_value=(0.5, {}))

        result = recommender.get_recommendations()

        rec_titles = [r["title"] for r in result["plex_recommendations"]]
        assert "Horror Movie" not in rec_titles
        assert "Drama Movie" in rec_titles


class TestMovieCacheCollectionData:
    """Tests for MovieCache collection data handling."""

    @patch("recommenders.base.load_media_cache")
    def test_extracts_collection_id(self, mock_load):
        """Test that collection_id is stored in cache."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = MovieCache("/tmp/cache")

        mock_movie = Mock()
        mock_movie.title = "Test Movie"
        mock_movie.year = 2024
        mock_movie.summary = ""
        mock_movie.directors = []
        mock_movie.genres = []
        mock_movie.roles = []
        mock_movie.guids = []
        mock_movie.userRating = None
        mock_movie.audienceRating = None

        # Mock _get_tmdb_data to return collection info
        cache._get_tmdb_data = Mock(
            return_value={
                "tmdb_id": 123,
                "imdb_id": "tt1234567",
                "keywords": [],
                "rating": 7.5,
                "vote_count": 1000,
                "collection_id": 789,
                "collection_name": "Test Collection",
            }
        )

        result = cache._process_item(mock_movie, "api_key")

        assert result["collection_id"] == 789
        assert result["collection_name"] == "Test Collection"


class TestPlexMovieRecommenderManageLabels:
    """Tests for PlexMovieRecommender.manage_plex_labels method."""

    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_manage_labels_skips_when_disabled(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache
    ):
        """Test manage_plex_labels does nothing when disabled."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
            "collections": {"add_label": False},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={"movies": {}})

        recommender = PlexMovieRecommender("/path/to/config.yml")

        # Should not raise and should not call library methods
        recommender.manage_plex_labels([{"title": "Test", "year": 2020}])


class TestFormatMovieOutputExtended:
    """Extended tests for format_movie_output function."""

    def test_format_with_summary(self):
        """Test movie formatting with summary."""
        movie = {
            "title": "Test Movie",
            "year": 2024,
            "similarity_score": 0.85,
            "summary": "This is a great action movie about heroes.",
        }

        result = format_movie_output(movie, show_summary=True, index=1)

        assert "action" in result.lower() or "heroes" in result.lower()

    def test_format_with_language(self):
        """Test movie formatting with language."""
        movie = {"title": "Test Movie", "year": 2024, "similarity_score": 0.85, "language": "English"}

        result = format_movie_output(movie, index=1, show_language=True)

        assert "English" in result

    def test_format_with_rating(self):
        """Test movie formatting with rating."""
        movie = {"title": "Test Movie", "year": 2024, "similarity_score": 0.85, "ratings": {"audience_rating": 8.5}}

        result = format_movie_output(movie, index=1, show_rating=True)

        assert "8.5" in result or "8" in result


class TestPlexMovieRecommenderLibraryImdbIds:
    """Tests for PlexMovieRecommender._get_library_imdb_ids method (inherited from BaseRecommender)."""

    @patch("recommenders.base.get_library_imdb_ids_from_items")
    @patch("recommenders.movie.MovieCache")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_library_imdb_ids_calls_utility(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_cache, mock_get_ids
    ):
        """Test that _get_library_imdb_ids uses utility function."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "tmdb_keywords": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_section = Mock()
        mock_section.all.return_value = []
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst
        mock_cache.return_value = Mock(cache={"movies": {}})
        mock_get_ids.return_value = {"tt1234567", "tt7654321"}

        recommender = PlexMovieRecommender("/path/to/config.yml")
        result = recommender._get_library_imdb_ids()

        assert "tt1234567" in result
        mock_get_ids.assert_called()


# ------------------------------------------------------------------------
# Core recommendation-engine coverage: rating-tier weighting, watched-history
# collection, movie detail extraction, and the per-user/per-library
# process_recommendations orchestration entry point (#157).
# ------------------------------------------------------------------------

MOVIE_TEST_CONFIG = {
    "plex": {"url": "http://localhost", "token": "abc"},
    "general": {},
    "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "keyword": 0.2},
}


def _make_movie_recommender(config=None, users=None, movie_cache_data=None, config_path="/path/to/config.yml"):
    """Build a fully-initialized PlexMovieRecommender with all I/O mocked out.

    Note: __init__ eagerly gathers watched data (real _get_plex_watched_data /
    _get_managed_users_watched_data call) and touches the library, so the Plex
    client is a MagicMock (safe default iteration -> []) and the media cache
    is likewise a MagicMock. Tests that care about the specifics of the
    watched-history gathering should patch the relevant
    recommenders.movie.* utility functions *before* calling this helper so
    construction picks them up.
    """
    config = copy.deepcopy(config if config is not None else MOVIE_TEST_CONFIG)
    users = users or {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
    movie_cache = MagicMock()
    movie_cache.cache = {"movies": movie_cache_data or {}}
    with (
        patch("recommenders.movie.MovieCache", return_value=movie_cache),
        patch("recommenders.base.init_plex", return_value=MagicMock()),
        patch("recommenders.base.get_configured_users", return_value=users),
        patch("recommenders.base.get_tmdb_config", return_value={"use_keywords": True, "api_key": "key"}),
        patch("recommenders.base.load_config", return_value=config),
        patch("os.makedirs"),
    ):
        recommender = PlexMovieRecommender(config_path)
    return recommender


class TestCalculateRatingMultiplier:
    """Tests for PlexMovieRecommender._calculate_rating_multiplier (rating-tier weighting)."""

    def test_none_rating_returns_unrated_default(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(None) == 0.6

    def test_zero_rating_returns_unrated_default(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(0) == 0.6

    def test_five_star_rating_returns_full_weight(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(9.5) == 1.0

    def test_four_star_rating(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(7.5) == 0.75

    def test_three_star_rating(self):
        recommender = _make_movie_recommender()
        assert recommender._calculate_rating_multiplier(5.5) == 0.5

    def test_two_star_rating_above_threshold(self):
        recommender = _make_movie_recommender()
        # threshold defaults to 3, so rating 4 is above threshold - positive tier path
        assert recommender._calculate_rating_multiplier(4) == 0.25

    def test_low_rating_with_negative_signals_enabled_is_negative(self):
        recommender = _make_movie_recommender()
        result = recommender._calculate_rating_multiplier(1.0)
        assert result < 0

    def test_low_rating_with_negative_signals_globally_disabled(self):
        recommender = _make_movie_recommender()
        recommender.config["negative_signals"] = {"enabled": False}
        result = recommender._calculate_rating_multiplier(1.0)
        assert result == 0.25

    def test_low_rating_with_bad_ratings_disabled(self):
        recommender = _make_movie_recommender()
        recommender.config["negative_signals"] = {"enabled": True, "bad_ratings": {"enabled": False}}
        result = recommender._calculate_rating_multiplier(1.0)
        assert result == 0.25


class TestGetPlexWatchedDataMovie:
    """Tests for PlexMovieRecommender._get_plex_watched_data.

    __init__ eagerly calls this (plex_users installs gather watched data at
    construction time), so these tests patch the network-touching utilities
    *before* constructing the recommender and assert on the resulting state.
    """

    @patch("os.path.exists", return_value=False)
    @patch("recommenders.movie.get_plex_account_ids", return_value=[])
    @patch("recommenders.movie.get_watched_movie_count", return_value=0)
    def test_returns_cached_counters_when_not_single_user_and_recalled(self, mock_count, mock_account_ids, mock_exists):
        recommender = _make_movie_recommender()
        # __init__ already populated watched_data_counters via a real call;
        # a direct re-call (not single_user) must short-circuit to the cache.
        cached = recommender.watched_data_counters
        assert cached

        result = recommender._get_plex_watched_data()

        assert result is cached

    @patch("os.path.exists", return_value=False)
    @patch("recommenders.movie.process_counters_from_cache")
    @patch("recommenders.movie.calculate_rewatch_multiplier", return_value=1.0)
    @patch("recommenders.movie.calculate_recency_multiplier", return_value=1.0)
    @patch("recommenders.movie.fetch_plex_watch_history_movies")
    @patch("recommenders.movie.get_plex_account_ids")
    @patch("recommenders.movie.get_watched_movie_count", return_value=1)
    def test_processes_watch_history_and_updates_counters(
        self, mock_count, mock_account_ids, mock_history, mock_recency, mock_rewatch, mock_process_counters, mock_exists
    ):
        mock_account_ids.return_value = ["acct1"]
        history_item = Mock(ratingKey=42, viewedAt=None, userRating=None)
        mock_history.return_value = ([history_item], {})

        recommender = _make_movie_recommender(movie_cache_data={"42": {"title": "Watched Movie", "tmdb_id": 999}})

        assert 42 in recommender.watched_ids
        mock_process_counters.assert_called_once()
        assert 999 in recommender.watched_data_counters["tmdb_ids"]

    @patch("os.path.exists", return_value=False)
    @patch("recommenders.movie.log_error")
    @patch("recommenders.movie.get_plex_account_ids", return_value=[])
    @patch("recommenders.movie.get_watched_movie_count", return_value=1)
    def test_no_account_ids_logs_error(self, mock_count, mock_account_ids, mock_log_error, mock_exists):
        _make_movie_recommender()

        mock_log_error.assert_called()

    @patch("os.path.exists", return_value=False)
    @patch("recommenders.movie.process_counters_from_cache")
    @patch("recommenders.movie.calculate_rewatch_multiplier", return_value=1.0)
    @patch("recommenders.movie.calculate_recency_multiplier", return_value=1.0)
    @patch("recommenders.movie.merge_movie_history")
    @patch("recommenders.movie.fetch_tautulli_movie_history")
    @patch("recommenders.movie.fetch_plex_watch_history_movies")
    @patch("recommenders.movie.get_plex_account_ids")
    @patch("recommenders.movie.get_watched_movie_count", return_value=1)
    def test_merges_tautulli_history_when_enabled(
        self,
        mock_count,
        mock_account_ids,
        mock_history,
        mock_tautulli,
        mock_merge,
        mock_recency,
        mock_rewatch,
        mock_process_counters,
        mock_exists,
    ):
        mock_account_ids.return_value = ["acct1"]
        mock_history.return_value = ([], {})
        mock_tautulli.return_value = [Mock(ratingKey=7)]
        mock_merge.return_value = [Mock(ratingKey=7, viewedAt=None, userRating=None)]

        config = copy.deepcopy(MOVIE_TEST_CONFIG)
        config["tautulli"] = {"enabled": True}
        _make_movie_recommender(config=config, movie_cache_data={})

        mock_merge.assert_called_once()


class TestGetPlexWatchedDataMovieAccurateMode:
    """Tests for the profile_accuracy.enabled path (#273) in
    PlexMovieRecommender._get_plex_watched_data - per-user library-
    sourced ratings/view counts instead of the legacy shared admin-token
    snapshot's view counts and Plex-history-sourced (verified: never
    actually populated in practice) ratings.

    Default flipped False -> True in v2.10.82 (a defect fix, not a
    preference - see CHANGELOG): a config that has never touched this
    key now gets accurate mode, proven by this class's own
    test_absent_config_key_defaults_to_accurate_mode. The explicit
    opt-out (enabled: false) that keeps the pre-v2.10.82 legacy
    behavior byte-identical (covered by TestGetPlexWatchedDataMovie
    above) is proven by test_explicit_disabled_never_calls_per_user_fetch.
    """

    @patch("os.path.exists", return_value=False)
    @patch("recommenders.base.BaseRecommender._get_all_library_items_for_user")
    @patch("recommenders.movie.process_counters_from_cache")
    @patch("recommenders.movie.calculate_rewatch_multiplier", return_value=1.0)
    @patch("recommenders.movie.calculate_recency_multiplier", return_value=1.0)
    @patch("recommenders.movie.fetch_plex_watch_history_movies")
    @patch("recommenders.movie.get_plex_account_ids")
    @patch("recommenders.movie.get_watched_movie_count", return_value=1)
    def test_accurate_mode_sources_rating_from_per_user_library_item(
        self,
        mock_count,
        mock_account_ids,
        mock_history,
        mock_recency,
        mock_rewatch,
        mock_process_counters,
        mock_per_user_items,
        mock_exists,
    ):
        mock_account_ids.return_value = ["acct1"]
        # Real Plex history NEVER carries userRating (verified against
        # 2,475 real history entries - see CHANGELOG) - mirrored here too.
        history_item = Mock(ratingKey=42, viewedAt=None, userRating=None)
        mock_history.return_value = ([history_item], {})
        # This user's OWN library item DOES carry a real (low) rating.
        library_item = Mock(ratingKey=42, viewCount=3, userRating=2.0)
        mock_per_user_items.return_value = [library_item]

        config = copy.deepcopy(MOVIE_TEST_CONFIG)
        config["profile_accuracy"] = {"enabled": True}
        _make_movie_recommender(
            config=config,
            users={"plex_users": ["alice"], "managed_users": [], "admin_user": "admin"},
            movie_cache_data={"42": {"title": "Disliked Movie", "genres": ["horror"], "tmdb_id": 999}},
        )

        mock_per_user_items.assert_called_once_with("alice")
        mock_process_counters.assert_called_once()
        _, kwargs = mock_process_counters.call_args
        # The per-user library rating (2.0, a real dislike, <=
        # DEFAULT_NEGATIVE_THRESHOLD) reached the scoring layer as a
        # NEGATIVE weight - the whole point of #273's fix. The legacy
        # (flag-off) path could never produce this: history never
        # carries userRating, so this movie would always be treated as
        # unrated (positive 0.6x weight) instead.
        assert kwargs["weight"] < 0

    @patch("os.path.exists", return_value=False)
    @patch("recommenders.base.BaseRecommender._get_all_library_items_for_user")
    @patch("recommenders.movie.process_counters_from_cache")
    @patch("recommenders.movie.fetch_plex_watch_history_movies")
    @patch("recommenders.movie.get_plex_account_ids")
    @patch("recommenders.movie.get_watched_movie_count", return_value=1)
    def test_accurate_mode_merges_across_multiple_users_by_taking_max(
        self,
        mock_count,
        mock_account_ids,
        mock_history,
        mock_process_counters,
        mock_per_user_items,
        mock_exists,
    ):
        from utils.scoring import calculate_rewatch_multiplier as real_rewatch_multiplier

        mock_account_ids.return_value = ["acct1", "acct2"]
        history_item = Mock(ratingKey=42, viewedAt=None, userRating=None)
        mock_history.return_value = ([history_item], {})
        alice_item = Mock(ratingKey=42, viewCount=1, userRating=4.0)
        bob_item = Mock(ratingKey=42, viewCount=5, userRating=9.0)
        # users_to_match iterates plex_users in configured order
        # (["alice", "bob"] below) - alice's own snapshot fetched first.
        mock_per_user_items.side_effect = [[alice_item], [bob_item]]

        config = copy.deepcopy(MOVIE_TEST_CONFIG)
        config["profile_accuracy"] = {"enabled": True}
        recommender = _make_movie_recommender(
            config=config,
            users={"plex_users": ["alice", "bob"], "managed_users": [], "admin_user": "admin"},
            movie_cache_data={"42": {"title": "Movie", "genres": ["action"], "tmdb_id": 999}},
        )

        assert mock_per_user_items.call_count == 2
        _, kwargs = mock_process_counters.call_args
        # Higher of the two ratings (9.0, bob's) and higher of the two
        # view counts (5, bob's) both win - same max-merge convention
        # this function already used for history-derived ratings before
        # #273, just applied per-user now instead of per-history-entry.
        expected_weight = recommender._calculate_rating_multiplier(9.0) * real_rewatch_multiplier(5)
        assert kwargs["weight"] == pytest.approx(expected_weight)

    @patch("os.path.exists", return_value=False)
    @patch("recommenders.base.BaseRecommender._get_all_library_items_for_user")
    @patch("recommenders.movie.process_counters_from_cache")
    @patch("recommenders.movie.calculate_rewatch_multiplier", return_value=1.0)
    @patch("recommenders.movie.calculate_recency_multiplier", return_value=1.0)
    @patch("recommenders.movie.fetch_plex_watch_history_movies")
    @patch("recommenders.movie.get_plex_account_ids")
    @patch("recommenders.movie.get_watched_movie_count", return_value=1)
    def test_absent_config_key_defaults_to_accurate_mode(
        self,
        mock_count,
        mock_account_ids,
        mock_history,
        mock_recency,
        mock_rewatch,
        mock_process_counters,
        mock_per_user_items,
        mock_exists,
    ):
        """profile_accuracy genuinely absent from config (an install
        that has never touched this setting) now defaults to accurate
        mode (v2.10.82) - _get_all_library_items_for_user IS called,
        proving the flipped default actually takes effect for a real
        config that never sets the key at all."""
        mock_account_ids.return_value = ["acct1"]
        history_item = Mock(ratingKey=42, viewedAt=None, userRating=None)
        mock_history.return_value = ([history_item], {})
        mock_per_user_items.return_value = []

        config = copy.deepcopy(MOVIE_TEST_CONFIG)
        assert "profile_accuracy" not in config
        _make_movie_recommender(
            config=config,
            users={"plex_users": ["alice"], "managed_users": [], "admin_user": "admin"},
            movie_cache_data={"42": {"title": "Movie", "genres": ["action"], "tmdb_id": 999}},
        )

        mock_per_user_items.assert_called_once_with("alice")

    @patch("os.path.exists", return_value=False)
    @patch("recommenders.base.BaseRecommender._get_all_library_items_for_user")
    @patch("recommenders.movie.process_counters_from_cache")
    @patch("recommenders.movie.calculate_rewatch_multiplier", return_value=1.0)
    @patch("recommenders.movie.calculate_recency_multiplier", return_value=1.0)
    @patch("recommenders.movie.fetch_plex_watch_history_movies")
    @patch("recommenders.movie.get_plex_account_ids")
    @patch("recommenders.movie.get_watched_movie_count", return_value=1)
    def test_explicit_disabled_never_calls_per_user_fetch(
        self,
        mock_count,
        mock_account_ids,
        mock_history,
        mock_recency,
        mock_rewatch,
        mock_process_counters,
        mock_per_user_items,
        mock_exists,
    ):
        """profile_accuracy explicitly set to enabled: false (the
        opt-out for anyone who wants the pre-v2.10.82 output unchanged
        for a release) - _get_all_library_items_for_user must never
        even be called, proving the legacy path stays byte-identical
        for anyone who opts back out."""
        mock_account_ids.return_value = ["acct1"]
        history_item = Mock(ratingKey=42, viewedAt=None, userRating=None)
        mock_history.return_value = ([history_item], {})

        config = copy.deepcopy(MOVIE_TEST_CONFIG)
        config["profile_accuracy"] = {"enabled": False}
        _make_movie_recommender(
            config=config,
            users={"plex_users": ["alice"], "managed_users": [], "admin_user": "admin"},
            movie_cache_data={"42": {"title": "Movie", "genres": ["action"], "tmdb_id": 999}},
        )

        mock_per_user_items.assert_not_called()


class TestGetMovieDetails:
    """Tests for PlexMovieRecommender.get_movie_details."""

    @patch("recommenders.movie.extract_genres", return_value=["Action"])
    @patch("recommenders.movie.extract_rating", return_value=8.0)
    @patch("recommenders.movie.extract_ids_from_guids", return_value={"imdb_id": "tt1", "tmdb_id": 1})
    def test_extracts_full_details(self, mock_ids, mock_rating, mock_genres):
        recommender = _make_movie_recommender()
        recommender.show_rating = True
        recommender.show_cast = True
        recommender.use_tmdb_keywords = False
        director = Mock(tag="Director A")
        actor = Mock(tag="Actor A")
        movie = Mock(title="Movie A", year=2020, summary="Summary", directors=[director], roles=[actor])

        result = recommender.get_movie_details(movie)

        assert result["title"] == "Movie A"
        assert "Director A" in result["directors"]
        assert result["ratings"]["audience_rating"] == 8.0
        assert "Actor A" in result["cast"]
        movie.reload.assert_called_once()

    @patch("recommenders.movie.extract_ids_from_guids", return_value={"imdb_id": None, "tmdb_id": None})
    def test_fetches_tmdb_keywords_when_enabled(self, mock_ids):
        recommender = _make_movie_recommender()
        recommender.use_tmdb_keywords = True
        recommender.tmdb_api_key = "key"
        recommender._get_plex_item_tmdb_id = Mock(return_value=55)
        recommender._get_tmdb_keywords_for_id = Mock(return_value={"kw1", "kw2"})
        movie = Mock(title="Movie B", year=2021, summary="", directors=[], roles=[])

        result = recommender.get_movie_details(movie)

        assert set(result["tmdb_keywords"]) == {"kw1", "kw2"}

    def test_handles_exception_returns_empty_dict(self):
        recommender = _make_movie_recommender()
        movie = Mock()
        movie.reload.side_effect = Exception("plex error")

        result = recommender.get_movie_details(movie)

        assert result == {}


class TestFindPlexItemAndWatchedDataSelectionMovie:
    """Tests for _find_plex_item delegation and _get_watched_data branch selection."""

    @patch("recommenders.movie.find_plex_movie")
    def test_find_plex_item_delegates_to_utility(self, mock_find):
        recommender = _make_movie_recommender()
        mock_find.return_value = "found"
        section = Mock()
        rec = {"title": "X", "year": 2020}

        result = recommender._find_plex_item(section, rec)

        assert result == "found"
        mock_find.assert_called_once_with(section, "X", 2020)

    def test_get_watched_data_uses_plex_history_when_plex_users_configured(self):
        recommender = _make_movie_recommender(
            users={"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        )
        recommender._get_plex_watched_data = Mock(return_value={"genres": {}})
        recommender._get_managed_users_watched_data = Mock(return_value={"genres": {}})

        recommender._get_watched_data()

        recommender._get_plex_watched_data.assert_called_once()
        recommender._get_managed_users_watched_data.assert_not_called()

    @patch("recommenders.base.MyPlexAccount")
    @patch("recommenders.movie.get_watched_movie_count", return_value=0)
    def test_get_watched_data_uses_managed_users_when_no_plex_users(self, mock_count, mock_account_cls):
        recommender = _make_movie_recommender(users={"plex_users": [], "managed_users": ["bob"], "admin_user": "admin"})
        recommender._get_plex_watched_data = Mock(return_value={"genres": {}})
        recommender._get_managed_users_watched_data = Mock(return_value={"genres": {}})

        recommender._get_watched_data()

        recommender._get_managed_users_watched_data.assert_called_once()
        recommender._get_plex_watched_data.assert_not_called()


class TestSaveCacheMovie:
    """Tests for PlexMovieRecommender._save_cache."""

    def test_save_cache_calls_save_watched_cache(self):
        recommender = _make_movie_recommender()
        recommender._save_watched_cache = Mock()

        recommender._save_cache()

        recommender._save_watched_cache.assert_called_once()


class TestProcessRecommendationsMovie:
    """Tests for movie.process_recommendations (per-user/per-library orchestration, #157)."""

    @patch("recommenders.movie.format_movie_output", return_value="formatted")
    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_happy_path_prints_and_manages_labels(self, mock_setup, mock_teardown, mock_recommender_cls, mock_format):
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {"plex_recommendations": [{"title": "A", "year": 2020}]}
        mock_instance.config = {"general": {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({"general": {}}, "/path/to/config.yml", 0)

        mock_instance.manage_plex_labels.assert_called_once()
        mock_instance._save_cache.assert_called_once()

    @patch("recommenders.movie.log_warning")
    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_no_recommendations_warns_and_skips_labels(
        self, mock_setup, mock_teardown, mock_recommender_cls, mock_warn
    ):
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {"plex_recommendations": []}
        mock_instance.config = {"general": {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({"general": {}}, "/path/to/config.yml", 0)

        mock_instance.manage_plex_labels.assert_not_called()
        mock_warn.assert_called()

    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_debug_flag_propagated_from_config(self, mock_setup, mock_teardown, mock_recommender_cls):
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {"plex_recommendations": []}
        mock_instance.config = {"general": {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({"general": {"debug": True}}, "/path/to/config.yml", 0)

        assert mock_instance.debug is True

    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_fatal_error_exits(self, mock_setup, mock_teardown, mock_recommender_cls):
        mock_recommender_cls.side_effect = RuntimeError("Plex server unreachable")

        with patch("recommenders.movie.sys.exit") as mock_exit:
            process_recommendations({"general": {}}, "/path/to/config.yml", 0)

        mock_exit.assert_called_once_with(1)

    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_non_fatal_error_does_not_exit(self, mock_setup, mock_teardown, mock_recommender_cls):
        mock_recommender_cls.side_effect = ValueError("Something else broke")

        with patch("recommenders.movie.sys.exit") as mock_exit:
            process_recommendations({"general": {}}, "/path/to/config.yml", 0)

        mock_exit.assert_not_called()


class TestProcessRecommendationsRecordsRunStatus:
    """#292: process_recommendations() records its own real observed
    outcome via utils.run_status.record_run_status(), independent of
    whether the fatal-keyword check separately decides to sys.exit()
    over it - see web/status.py's get_last_run_status() docstring for
    why this replaced grepping the log tail for English error strings."""

    @patch("recommenders.movie.record_run_status")
    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_success_records_true(self, mock_setup, mock_teardown, mock_recommender_cls, mock_record):
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {"plex_recommendations": []}
        mock_instance.config = {"general": {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({"general": {}}, "/path/to/config.yml", 0, single_user="alice")

        mock_record.assert_called_once()
        args, _kwargs = mock_record.call_args
        assert args[1:] == ("movie", "alice", True, "")

    @patch("recommenders.movie.record_run_status")
    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_non_fatal_error_records_false_with_detail(
        self, mock_setup, mock_teardown, mock_recommender_cls, mock_record
    ):
        mock_recommender_cls.side_effect = ValueError("Something else broke")

        process_recommendations({"general": {}}, "/path/to/config.yml", 0, single_user="alice")

        args, _kwargs = mock_record.call_args
        assert args[1:4] == ("movie", "alice", False)
        assert "Something else broke" in args[4]

    @patch("recommenders.movie.record_run_status")
    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_fatal_error_still_records_before_exiting(
        self, mock_setup, mock_teardown, mock_recommender_cls, mock_record
    ):
        """The finally block must run - and record - even on the
        sys.exit(1) path (fatal errors), never skipped."""
        mock_recommender_cls.side_effect = RuntimeError("Plex server unreachable")

        with patch("recommenders.movie.sys.exit"):
            process_recommendations({"general": {}}, "/path/to/config.yml", 0, single_user="alice")

        args, _kwargs = mock_record.call_args
        assert args[1:4] == ("movie", "alice", False)

    @patch("recommenders.movie.record_run_status")
    @patch("recommenders.movie.PlexMovieRecommender")
    @patch("recommenders.movie.teardown_log_file")
    @patch("recommenders.movie.setup_log_file")
    def test_no_single_user_never_records(self, mock_setup, mock_teardown, mock_recommender_cls, mock_record):
        """An 'all users' caller with no single_user resolved yet must
        never write a nonsensical run_status_movie_None.json."""
        mock_instance = Mock()
        mock_instance.get_recommendations.return_value = {"plex_recommendations": []}
        mock_instance.config = {"general": {}}
        mock_recommender_cls.return_value = mock_instance

        process_recommendations({"general": {}}, "/path/to/config.yml", 0)

        mock_record.assert_not_called()


class TestLibraryFetchedOnceNotSixTimes:
    """#233 audit remediation batch D / PR1(a) regression coverage.

    Before this fix, PlexMovieRecommender.__init__ called
    section.all() independently from up to 6 places for a single
    user's instantiation: MovieCache.update_cache, _get_library_movies_set
    (itself called twice back-to-back for a byte-identical result),
    _get_library_movie_titles, _get_library_imdb_ids, and the view-count
    loop inside _get_plex_watched_data. All six now share one fetch via
    BaseRecommender._get_all_library_items().

    Runs a real (unmocked) MovieCache/update_cache - not a mocked
    stand-in - so this exercises the actual code path, not just the
    call site. get_project_root() is @lru_cache'd, so CURATARR_CONFIG_DIR
    is pointed at tmp_path and the cache cleared before/after, same
    convention as TestBaseRecommenderCacheDirResolution in
    test_base.py - this must never resolve to the real project's own
    cache/ directory.
    """

    def setup_method(self):
        from utils.helpers import get_project_root

        get_project_root.cache_clear()

    def teardown_method(self):
        from utils.helpers import get_project_root

        get_project_root.cache_clear()

    @patch("recommenders.movie.fetch_plex_watch_history_movies", return_value=([], {}))
    @patch("recommenders.movie.get_plex_account_ids", return_value=["acct1"])
    @patch("recommenders.movie.get_watched_movie_count", return_value=0)
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    def test_section_all_called_exactly_once(
        self,
        mock_load,
        mock_tmdb,
        mock_users,
        mock_plex,
        mock_watched_count,
        mock_account_ids,
        mock_history,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setenv("CURATARR_CONFIG_DIR", str(tmp_path))

        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "director": 0.2, "actor": 0.2, "language": 0.1, "keyword": 0.2},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        # No TMDB key - keeps MovieCache._process_item on its no-TMDB
        # branch so this test only measures Plex round trips.
        mock_tmdb.return_value = {"use_keywords": False, "api_key": None}

        mock_movie = Mock(ratingKey=1, year=2020, guids=[], directors=[], genres=[], roles=[], viewCount=0)
        mock_movie.title = "Test Movie"
        mock_section = Mock()
        mock_section.all.return_value = [mock_movie]
        mock_plex_inst = Mock()
        mock_plex_inst.library.section.return_value = mock_section
        mock_plex.return_value = mock_plex_inst

        PlexMovieRecommender("/path/to/config.yml")

        assert mock_section.all.call_count == 1
