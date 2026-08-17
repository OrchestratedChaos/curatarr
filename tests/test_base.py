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
Tests for recommenders/base.py - Base cache and recommender classes.
"""

import copy
import json
import os
from collections import Counter
from unittest.mock import Mock, patch

import plexapi.exceptions
import requests

import recommenders.base as base_module
from recommenders.base import RECOMMEND_FOR_NO_HISTORY_DEFAULT, BaseCache, BaseRecommender
from utils.helpers import get_project_root


class ConcreteCache(BaseCache):
    """Concrete implementation of BaseCache for testing."""

    media_type = "movie"
    media_key = "movies"
    cache_filename = "test_cache.json"

    def _process_item(self, item, tmdb_api_key):
        return {"title": item.title, "year": getattr(item, "year", None), "genres": ["action", "comedy"]}


class TestBaseCacheInit:
    """Tests for BaseCache initialization."""

    @patch("recommenders.base.load_media_cache")
    def test_init_sets_cache_path(self, mock_load):
        """Test that cache path is set correctly."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = ConcreteCache("/tmp/cache")

        # os.path.join, not a hardcoded '/'-joined string: cache_path is
        # built with os.path.join in production (see recommenders/base.py),
        # which uses '\\' on Windows - matches that, same as every other
        # cache_path assertion in this class.
        assert cache.cache_path == os.path.join("/tmp/cache", "test_cache.json")

    @patch("recommenders.base.load_media_cache")
    def test_init_loads_cache(self, mock_load):
        """Test that cache is loaded on init."""
        mock_load.return_value = {"movies": {"123": {"title": "Test"}}, "library_count": 1}

        cache = ConcreteCache("/tmp/cache")

        mock_load.assert_called_once()
        assert "123" in cache.cache["movies"]

    @patch("recommenders.base.load_media_cache")
    def test_init_stores_recommender_reference(self, mock_load):
        """Test that recommender reference is stored."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_recommender = Mock()

        cache = ConcreteCache("/tmp/cache", recommender=mock_recommender)

        assert cache.recommender is mock_recommender


class TestBaseCachePerLibraryFilePath:
    """Tests for #157 cross-library cache eviction fix.

    BaseCache.cache_path must be qualified with the recommender's
    per-library prefix (_cache_library_prefix()) so that two same-media-type
    libraries never share one cache file. Before this fix, cache_path was
    built from cache_filename alone, so update_cache() on library B would
    evict library A's entries (and vice versa) because both libraries wrote
    to the exact same file.
    """

    def test_single_library_cache_path_unchanged(self, tmp_path):
        """Back-compat: a single-library recommender (prefix '') keeps the
        exact legacy filename."""
        recommender = Mock()
        recommender._cache_library_prefix.return_value = ""

        cache = ConcreteCache(str(tmp_path), recommender=recommender)

        assert cache.cache_path == os.path.join(str(tmp_path), "test_cache.json")

    def test_no_recommender_cache_path_unchanged(self, tmp_path):
        """Back-compat: no recommender passed at all also keeps the legacy
        path (guards callers that construct a cache without one)."""
        cache = ConcreteCache(str(tmp_path))

        assert cache.cache_path == os.path.join(str(tmp_path), "test_cache.json")

    def test_multi_library_caches_use_distinct_files(self, tmp_path):
        """Two same-media-type libraries get distinct, library-qualified
        cache files instead of sharing one."""
        recommender_a = Mock()
        recommender_a._cache_library_prefix.return_value = "movies_"
        recommender_b = Mock()
        recommender_b._cache_library_prefix.return_value = "movies-4k_"

        cache_a = ConcreteCache(str(tmp_path), recommender=recommender_a)
        cache_b = ConcreteCache(str(tmp_path), recommender=recommender_b)

        assert cache_a.cache_path == os.path.join(str(tmp_path), "movies_test_cache.json")
        assert cache_b.cache_path == os.path.join(str(tmp_path), "movies-4k_test_cache.json")
        assert cache_a.cache_path != cache_b.cache_path

    def test_processing_one_library_does_not_evict_another(self, tmp_path):
        """Reproduces #157: processing library A must not evict or
        overwrite library B's cached entries. Each library now owns its own
        file on disk, so B's cache survives A's update_cache() eviction pass.
        """
        recommender_a = Mock()
        recommender_a._cache_library_prefix.return_value = "movies_"
        recommender_b = Mock()
        recommender_b._cache_library_prefix.return_value = "movies-4k_"

        # Library B already has a cached item on disk.
        cache_b = ConcreteCache(str(tmp_path), recommender=recommender_b)
        cache_b.cache["movies"]["b-item"] = {"title": "B Movie"}
        cache_b.cache["library_count"] = 1
        cache_b._save_cache()

        # Process library A, which only contains one item distinct from B's.
        mock_item = Mock()
        mock_item.ratingKey = "a-item"
        mock_item.title = "A Movie"
        mock_item.year = 2024

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache_a = ConcreteCache(str(tmp_path), recommender=recommender_a)
        cache_a.update_cache(mock_plex, "Movies")

        assert "a-item" in cache_a.cache["movies"]

        # Library B's on-disk cache file is untouched by A's eviction pass.
        cache_b_reloaded = ConcreteCache(str(tmp_path), recommender=recommender_b)
        assert "b-item" in cache_b_reloaded.cache["movies"]
        assert "a-item" not in cache_b_reloaded.cache["movies"]


class TestBaseCacheSave:
    """Tests for BaseCache save functionality."""

    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_save_cache_adds_version(self, mock_load, mock_save):
        """Test that save adds cache version."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = ConcreteCache("/tmp/cache")
        cache._save_cache()

        assert "cache_version" in cache.cache
        mock_save.assert_called_once()


class TestBaseCacheUpdate:
    """Tests for BaseCache update functionality."""

    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_update_returns_false_when_up_to_date(self, mock_load, mock_save):
        """Test that update returns False when cache is current."""
        mock_load.return_value = {"movies": {}, "library_count": 5}

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.all.return_value = [Mock() for _ in range(5)]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache("/tmp/cache")
        result = cache.update_cache(mock_plex, "Movies")

        assert result is False

    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_update_processes_new_items(self, mock_load, mock_save):
        """Test that update processes new items."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        mock_item = Mock()
        mock_item.ratingKey = "123"
        mock_item.title = "New Movie"
        mock_item.year = 2024

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache("/tmp/cache")
        result = cache.update_cache(mock_plex, "Movies")

        assert result is True
        assert "123" in cache.cache["movies"]

    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_update_removes_deleted_items(self, mock_load, mock_save):
        """Test that update removes items no longer in library."""
        mock_load.return_value = {
            "movies": {"old_id": {"title": "Old Movie"}},
            "library_count": 0,  # Different from current count to trigger update
        }

        mock_item = Mock()
        mock_item.ratingKey = "new_id"
        mock_item.title = "New Movie"

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache("/tmp/cache")
        cache.update_cache(mock_plex, "Movies")

        assert "old_id" not in cache.cache["movies"]

    @patch("recommenders.base.log_warning")
    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_update_handles_item_processing_error(self, mock_load, mock_save, mock_warn):
        """Test that update continues when item processing fails."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        mock_item = Mock()
        mock_item.ratingKey = "123"
        mock_item.title = "Bad Movie"
        mock_item.reload.side_effect = plexapi.exceptions.PlexApiException("Network error")

        mock_plex = Mock()
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache("/tmp/cache")
        result = cache.update_cache(mock_plex, "Movies")

        assert result is True  # Still returns True (cache was updated)
        mock_warn.assert_called()


class TestBaseCacheGetLanguage:
    """Tests for BaseCache._get_language method."""

    @patch("recommenders.base.load_media_cache")
    def test_get_language_returns_na_when_no_media(self, mock_load):
        """Test that N/A is returned when item has no media."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = ConcreteCache("/tmp/cache")
        mock_item = Mock()
        mock_item.media = None

        result = cache._get_language(mock_item)

        assert result == "N/A"

    @patch("recommenders.base.get_full_language_name")
    @patch("recommenders.base.load_media_cache")
    def test_get_language_extracts_from_audio_stream(self, mock_load, mock_lang):
        """Test language extraction from audio stream."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_lang.return_value = "English"

        cache = ConcreteCache("/tmp/cache")

        mock_audio = Mock()
        mock_audio.languageTag = "en"
        mock_part = Mock()
        mock_part.audioStreams.return_value = [mock_audio]
        mock_media = Mock()
        mock_media.parts = [mock_part]
        mock_item = Mock()
        mock_item.media = [mock_media]

        result = cache._get_language(mock_item)

        assert result == "English"

    @patch("recommenders.base.load_media_cache")
    def test_get_language_for_tv_uses_first_episode(self, mock_load):
        """Test that TV shows use first episode for language."""
        mock_load.return_value = {"shows": {}, "library_count": 0}

        # Create TV cache
        class TVCache(BaseCache):
            media_type = "tv"
            media_key = "shows"
            cache_filename = "test_shows.json"

            def _process_item(self, item, tmdb_api_key):
                return {}

        cache = TVCache("/tmp/cache")

        mock_episode = Mock()
        mock_episode.media = None
        mock_show = Mock()
        mock_show.episodes.return_value = [mock_episode]

        cache._get_language(mock_show)

        mock_show.episodes.assert_called_once()

    @patch("recommenders.base.load_media_cache")
    def test_get_language_returns_na_on_exception(self, mock_load):
        """Test that N/A is returned on Plex API or attribute errors."""
        mock_load.return_value = {"movies": {}, "library_count": 0}

        cache = ConcreteCache("/tmp/cache")
        mock_item = Mock()
        mock_item.media = Mock()
        mock_item.media.__iter__ = Mock(side_effect=AttributeError("Error"))

        result = cache._get_language(mock_item)

        assert result == "N/A"


class TestBaseCacheGetTmdbData:
    """Tests for BaseCache._get_tmdb_data method."""

    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_extracts_ids_from_guids(self, mock_load, mock_extract):
        """Test that IDs are extracted from GUIDs."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": "tt123", "tmdb_id": 456}

        cache = ConcreteCache("/tmp/cache")
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, None)

        assert result["imdb_id"] == "tt123"
        assert result["tmdb_id"] == 456

    @patch("recommenders.base.get_tmdb_keywords")
    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_fetches_keywords(self, mock_load, mock_extract, mock_keywords):
        """Test that keywords are fetched from TMDB."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": 123}
        mock_keywords.return_value = ["action", "hero"]

        cache = ConcreteCache("/tmp/cache")
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, "api_key")

        assert result["keywords"] == ["action", "hero"]

    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.get_tmdb_keywords")
    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_fetches_movie_rating(self, mock_load, mock_extract, mock_keywords, mock_fetch):
        """Test that movie rating is fetched from TMDB."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": 123}
        mock_keywords.return_value = []
        mock_fetch.return_value = {"vote_average": 7.5, "vote_count": 1000}

        cache = ConcreteCache("/tmp/cache")
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, "api_key")

        assert result["rating"] == 7.5
        assert result["vote_count"] == 1000

    @patch("recommenders.base.get_tmdb_id_for_item")
    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_falls_back_to_search(self, mock_load, mock_extract, mock_get_id):
        """Test fallback to TMDB search when no ID in GUIDs."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": None}
        mock_get_id.return_value = 789

        cache = ConcreteCache("/tmp/cache")
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, "api_key")

        mock_get_id.assert_called_once()
        assert result["tmdb_id"] == 789

    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_updates_recommender_caches(self, mock_load, mock_extract):
        """Test that recommender caches are updated."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": 123}

        mock_recommender = Mock()
        mock_recommender.plex_tmdb_cache = {}
        mock_recommender.tmdb_keywords_cache = {}

        cache = ConcreteCache("/tmp/cache", recommender=mock_recommender)
        mock_item = Mock()
        mock_item.ratingKey = "456"

        cache._get_tmdb_data(mock_item, None)

        assert mock_recommender.plex_tmdb_cache["456"] == 123

    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.get_tmdb_keywords")
    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_fetches_tv_rating(self, mock_load, mock_extract, mock_keywords, mock_fetch):
        """Test that TV show rating/vote_count is fetched from TMDB too,
        mirroring test_get_tmdb_data_fetches_movie_rating above. Regression
        test for the tv: quality_filters no-op bug - ShowCache never
        carried these fields before (see CHANGELOG); this proves the
        underlying _get_tmdb_data fetch itself populates them for
        media_type="tv", alongside the pre-existing production_company_ids
        fetch (which must keep working unchanged)."""
        mock_load.return_value = {"shows": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": 123}
        mock_keywords.return_value = []
        mock_fetch.return_value = {
            "vote_average": 8.4,
            "vote_count": 2000,
            "production_companies": [{"id": 42, "name": "Test Studio"}],
        }

        class TVCache(BaseCache):
            media_type = "tv"
            media_key = "shows"
            cache_filename = "test_shows.json"

            def _process_item(self, item, tmdb_api_key):
                return {}

        cache = TVCache("/tmp/cache")
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, "api_key")

        assert result["rating"] == 8.4
        assert result["vote_count"] == 2000
        assert result["production_company_ids"] == [42]

    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.get_tmdb_keywords")
    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_movie_rating_unaffected_by_tv_change(
        self, mock_load, mock_extract, mock_keywords, mock_fetch
    ):
        """Movie path regression check: a movie's _get_tmdb_data result
        must still carry rating/vote_count/collection info and never
        production_company_ids, unchanged by the TV branch fix above."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": 123}
        mock_keywords.return_value = []
        mock_fetch.return_value = {
            "vote_average": 7.1,
            "vote_count": 300,
            "belongs_to_collection": {"id": 9, "name": "Test Collection"},
        }

        cache = ConcreteCache("/tmp/cache")
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, "api_key")

        assert result["rating"] == 7.1
        assert result["vote_count"] == 300
        assert result["collection_id"] == 9
        assert result["collection_name"] == "Test Collection"
        assert result["production_company_ids"] == []


class ConcreteRecommender(BaseRecommender):
    """Concrete implementation of BaseRecommender for testing."""

    media_type = "movie"
    media_key = "movies"
    library_config_key = "movie_library"
    default_library_name = "Movies"

    def _load_weights(self, weights_config):
        return {"genre": 0.5, "actor": 0.5}

    def _get_watched_data(self):
        return {"genres": Counter(), "actors": Counter()}

    def _get_watched_count(self):
        return 0

    def _save_watched_cache(self):
        pass

    def _get_media_cache(self):
        return Mock()

    def _find_plex_item(self, section, rec):
        return None

    def _calculate_similarity_from_cache(self, item_info):
        return (0.5, {})

    def _print_similarity_breakdown(self, item_info, score, breakdown):
        pass


class TestBaseRecommenderInit:
    """Tests for BaseRecommender initialization."""

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_init_loads_config(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that config is loaded on init."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        ConcreteRecommender("/path/to/config.yml")

        mock_load.assert_called_once_with("/path/to/config.yml")

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_init_connects_to_plex(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that Plex connection is established."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        ConcreteRecommender("/path/to/config.yml")

        mock_plex.assert_called_once()

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_init_loads_display_options(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that display options are loaded from config."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {"show_summary": True, "show_cast": True, "limit_plex_results": 25},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.show_summary is True
        assert recommender.show_cast is True
        assert recommender.limit_plex_results == 25

    @patch("recommenders.base.log_warning")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_init_warns_on_invalid_weights(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_warn):
        """Test that warning is logged when weights don't sum to 1.0."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.3, "actor": 0.3},  # Sums to 0.6
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        # Override _load_weights to return bad weights
        class BadWeightsRecommender(ConcreteRecommender):
            def _load_weights(self, weights_config):
                return {"genre": 0.3, "actor": 0.3}

        BadWeightsRecommender("/path/to/config.yml")

        mock_warn.assert_called()


class TestBaseRecommenderMediaSectionConfig:
    """Tests for movies:/tv: media-specific config (config/tuning.yml)
    must actually be honored by BaseRecommender, not silently ignored in
    favor of the (almost always absent) root-level/general: keys of the
    same name. Covers the resolved *runtime* attribute, not just that
    adapt_config_for_media_type() computes the right value somewhere
    unused - see utils/config.py's adapt_config_for_media_type() and its
    docstring for the parallel (and, before this fix, disconnected)
    resolution path."""

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_movies_section_randomize_recommendations_overrides_general_default(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "movies": {"randomize_recommendations": False},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.randomize_recommendations is False

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_tv_section_randomize_recommendations_overrides_general_default(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "tv": {"randomize_recommendations": False},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteTVRecommender("/path/to/config.yml")

        assert recommender.randomize_recommendations is False

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_general_level_randomize_recommendations_still_honored_when_no_media_section(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        """Back-compat: an install with no movies:/tv: section at all (or
        one that doesn't set this key) keeps reading the legacy
        general.randomize_recommendations override."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {"randomize_recommendations": False},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.randomize_recommendations is False

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_movies_section_quality_filters_resolved_into_media_config(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "movies": {"quality_filters": {"min_rating": 5.0, "min_vote_count": 15}},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.media_config["quality_filters"] == {"min_rating": 5.0, "min_vote_count": 15}

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_movies_section_quality_filters_excludes_low_rated(self, mock_excl):
        """Same assertion as TestGetRecommendationsBranches.test_quality_filter_excludes_low_rated,
        but with quality_filters set at its documented movies: location
        instead of legacy config['quality_filters'] - proves
        get_recommendations() actually reads the media-specific section."""
        items = {
            "1": {"title": "Good", "rating": 8.0, "vote_count": 500, "genres": []},
            "2": {"title": "Bad", "rating": 2.0, "vote_count": 5, "genres": []},
        }
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": items}
        media_cache._save_cache = Mock()
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = {90001, 90002, 90003}  # #291: non-empty, so the zero-watch-history gate never fires
        recommender.profile_hash = "hash1"
        recommender.exclude_genres = []
        recommender.user_preferences = {}
        recommender.randomize_recommendations = False
        recommender.media_config = {"quality_filters": {"min_rating": 5.0, "min_vote_count": 100}}

        result = recommender.get_recommendations()

        titles = [i["title"] for i in result["plex_recommendations"]]
        assert "Bad" not in titles
        assert "Good" in titles

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_movies_section_display_options_override_general_defaults(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "movies": {
                "show_summary": True,
                "show_cast": True,
                "show_language": True,
                "show_rating": True,
                "show_imdb_link": True,
                "show_genres": False,
                "normalize_counters": False,
            },
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.show_summary is True
        assert recommender.show_cast is True
        assert recommender.show_language is True
        assert recommender.show_rating is True
        assert recommender.show_imdb_link is True
        assert recommender.show_genres is False
        assert recommender.normalize_counters is False

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_movies_section_weights_override_legacy_root_weights(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            # Legacy root-level 'weights' - should be shadowed by movies.weights below.
            "weights": {"genre": 0.9, "actor": 0.1},
            "movies": {"weights": {"genre": 0.1, "actor": 0.9}},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        class WeightsEchoRecommender(ConcreteRecommender):
            def _load_weights(self, weights_config):
                return weights_config

        recommender = WeightsEchoRecommender("/path/to/config.yml")

        assert recommender.weights == {"genre": 0.1, "actor": 0.9}

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_limit_results_unset_keeps_old_50_20_defaults(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        """PR1 (limit_results dead config audit finding): limit_results
        unset must not change behavior for existing installs - movies
        default to 50, tv to 20, and the candidate buffer
        (limit_plex_results) still defaults to 2x that (100/40), exactly
        matching pre-fix behavior."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        movie_recommender = ConcreteRecommender("/path/to/config.yml")
        assert movie_recommender.limit_results == 50
        assert movie_recommender.limit_plex_results == 100

        tv_recommender = ConcreteTVRecommender("/path/to/config.yml")
        assert tv_recommender.limit_results == 20
        assert tv_recommender.limit_plex_results == 40

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_movies_section_limit_results_is_honored(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """PR1: config/tuning.yml movies: limit_results now actually
        controls the final recommendation/collection count - previously
        documented but never read anywhere (see CHANGELOG). The candidate
        buffer scales with it (2x), so the buffer >= limit_results
        invariant holds by construction whenever limit_plex_results is
        left unset."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "movies": {"limit_results": 15},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.limit_results == 15
        assert recommender.limit_plex_results == 30
        assert recommender.limit_plex_results >= recommender.limit_results

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_explicit_limit_plex_results_override_still_honored_exactly(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        """An explicit general.limit_plex_results (the advanced/internal
        candidate-buffer override) is honored exactly as configured, even
        when it's smaller than limit_results - matches this repo's existing
        documented/tested behavior (see
        TestBaseRecommenderInit.test_init_loads_display_options), which PR1
        must not change: the buffer >= limit_results invariant is only
        guaranteed for the *computed default* buffer, not an admin's
        explicit override."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {"limit_plex_results": 5},
            "movies": {"limit_results": 50},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.limit_results == 50
        assert recommender.limit_plex_results == 5


class TestBaseRecommenderCacheDirResolution:
    """Tests for BaseRecommender's cache_dir setup (recommenders/base.py).

    Prior to 2.10.3 this was computed relative to base.py's own
    __file__, bypassing get_project_root() entirely - meaning it ignored
    CURATARR_CONFIG_DIR (Docker) and, for a frozen binary, resolved
    inside the PyInstaller temp unpack dir that's deleted on exit. These
    tests cover the fix: cache_dir must go through get_project_root(),
    same as utils/cli.py and recommenders/external.py, honoring both
    that override and the config-level 'cache_dir' override.

    get_project_root() is @lru_cache(maxsize=1), so tests that rely on
    the real (unmocked) function clear its cache before/after - same
    convention as TestGetProjectRoot in tests/test_helpers.py.
    """

    def setup_method(self):
        get_project_root.cache_clear()

    def teardown_method(self):
        get_project_root.cache_clear()

    @patch("recommenders.base.migrate_legacy_cache_dir")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_cache_dir_honors_config_dir_env_override(
        self,
        mock_makedirs,
        mock_load,
        mock_tmdb,
        mock_users,
        mock_plex,
        mock_migrate,
        tmp_path,
        monkeypatch,
    ):
        """CURATARR_CONFIG_DIR (Docker) must be honored by cache_dir,
        not just the fixed default of a directory next to base.py."""
        override = str(tmp_path / "data")
        monkeypatch.setenv("CURATARR_CONFIG_DIR", override)

        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.cache_dir == os.path.join(override, "cache")

    @patch("recommenders.base.migrate_legacy_cache_dir")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_cache_dir_config_override_relative_name(
        self,
        mock_makedirs,
        mock_load,
        mock_tmdb,
        mock_users,
        mock_plex,
        mock_migrate,
    ):
        """config.yml's cache_dir: <relative subdir name> is joined onto
        the resolved project root, matching external.py's own handling."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
            "cache_dir": "my_custom_cache",
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        with patch("recommenders.base.get_project_root", return_value=os.path.join("C:\\", "project", "root")):
            recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.cache_dir == os.path.join("C:\\", "project", "root", "my_custom_cache")

    @patch("recommenders.base.migrate_legacy_cache_dir")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_cache_dir_config_override_absolute_path(
        self,
        mock_makedirs,
        mock_load,
        mock_tmdb,
        mock_users,
        mock_plex,
        mock_migrate,
        tmp_path,
    ):
        """config.yml's cache_dir: <absolute path> is used verbatim -
        os.path.join() discards the project root when the second
        component is already absolute, so this must NOT get nested
        under the resolved project root."""
        absolute_cache_dir = str(tmp_path / "abs_cache")
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
            "cache_dir": absolute_cache_dir,
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        with patch("recommenders.base.get_project_root", return_value=os.path.join("C:\\", "project", "root")):
            recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.cache_dir == absolute_cache_dir
        assert not recommender.cache_dir.startswith(os.path.join("C:\\", "project", "root"))

    @patch("recommenders.base.migrate_legacy_cache_dir")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_cache_dir_defaults_to_cache_subdir(
        self,
        mock_makedirs,
        mock_load,
        mock_tmdb,
        mock_users,
        mock_plex,
        mock_migrate,
    ):
        """No config-level cache_dir override -> defaults to 'cache' under
        the resolved project root (unchanged default behavior)."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        with patch("recommenders.base.get_project_root", return_value=os.path.join("C:\\", "project", "root")):
            recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.cache_dir == os.path.join("C:\\", "project", "root", "cache")

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_init_invokes_legacy_cache_migration(
        self,
        mock_makedirs,
        mock_load,
        mock_tmdb,
        mock_users,
        mock_plex,
    ):
        """The best-effort legacy-cache-dir migration is invoked once per
        init with the pre-2.10.3 __file__-relative path and the resolved
        cache_dir - actual migration behavior is covered separately in
        tests/test_helpers.py::TestMigrateLegacyCacheDir."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        expected_legacy_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(base_module.__file__))),
            "cache",
        )

        with (
            patch("recommenders.base.get_project_root", return_value=os.path.join("C:\\", "project", "root")),
            patch("recommenders.base.migrate_legacy_cache_dir") as mock_migrate,
        ):
            recommender = ConcreteRecommender("/path/to/config.yml")

        mock_migrate.assert_called_once_with(expected_legacy_dir, recommender.cache_dir)


class TestBaseRecommenderGetUserContext:
    """Tests for BaseRecommender._get_user_context method."""

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_user_context_single_user(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test user context for single user mode."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": ["user1"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml", single_user="testuser")
        result = recommender._get_user_context()

        assert result == "plex_testuser"

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_user_context_plex_users(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test user context for plex users."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": ["user1", "user2"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")
        result = recommender._get_user_context()

        assert result == "plex_user1_user2"

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_get_user_context_sanitizes_special_chars(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that special characters are removed from user context."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": ["user@email.com"], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")
        result = recommender._get_user_context()

        assert "@" not in result
        assert "." not in result


class TestBaseRecommenderRefreshWatchedData:
    """Tests for BaseRecommender._refresh_watched_data method."""

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_refresh_clears_existing_data(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Test that refresh clears existing watched data."""
        mock_load.return_value = {
            "plex": {"url": "http://localhost", "token": "abc"},
            "general": {},
            "weights": {"genre": 0.5, "actor": 0.5},
        }
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")
        recommender.watched_data_counters = {"genres": Counter({"action": 5})}
        recommender.watched_ids = {1, 2, 3}

        recommender._refresh_watched_data()

        assert len(recommender.watched_ids) == 0


class TestBaseCacheBackfillCollectionData:
    """Tests for BaseCache._backfill_collection_data method."""

    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_backfill_returns_false_when_no_movies_need_update(self, mock_load, mock_save):
        """Test backfill returns False when all movies have collection_id."""
        mock_load.return_value = {
            "movies": {"123": {"tmdb_id": 456, "collection_id": 789, "collection_name": "Test Collection"}},
            "library_count": 1,
        }

        cache = ConcreteCache("/tmp/cache")
        result = cache._backfill_collection_data("api_key")

        assert result is False

    @patch("recommenders.base.time.sleep")
    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_backfill_updates_movies_missing_collection_id(self, mock_load, mock_save, mock_fetch, mock_sleep):
        """Test backfill adds collection data to movies missing it."""
        mock_load.return_value = {
            "movies": {
                "123": {"tmdb_id": 456, "title": "Test Movie"}  # No collection_id
            },
            "library_count": 1,
        }
        mock_fetch.return_value = {"belongs_to_collection": {"id": 789, "name": "Test Collection"}}

        cache = ConcreteCache("/tmp/cache")
        result = cache._backfill_collection_data("api_key")

        assert result is True
        assert cache.cache["movies"]["123"]["collection_id"] == 789
        assert cache.cache["movies"]["123"]["collection_name"] == "Test Collection"

    @patch("recommenders.base.time.sleep")
    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_backfill_sets_none_when_no_collection(self, mock_load, mock_save, mock_fetch, mock_sleep):
        """Test backfill sets None when movie has no collection."""
        mock_load.return_value = {"movies": {"123": {"tmdb_id": 456, "title": "Standalone Movie"}}, "library_count": 1}
        mock_fetch.return_value = {"id": 456, "title": "Standalone Movie"}  # No belongs_to_collection key

        cache = ConcreteCache("/tmp/cache")
        result = cache._backfill_collection_data("api_key")

        assert result is True
        assert cache.cache["movies"]["123"]["collection_id"] is None
        assert cache.cache["movies"]["123"]["collection_name"] is None

    @patch("recommenders.base.time.sleep")
    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_backfill_handles_fetch_error(self, mock_load, mock_save, mock_fetch, mock_sleep):
        """Test backfill continues when fetch fails."""
        mock_load.return_value = {
            "movies": {"123": {"tmdb_id": 456, "title": "Movie 1"}, "124": {"tmdb_id": 457, "title": "Movie 2"}},
            "library_count": 2,
        }
        mock_fetch.side_effect = [
            requests.RequestException("Network error"),
            {"belongs_to_collection": {"id": 1, "name": "Collection"}},
        ]

        cache = ConcreteCache("/tmp/cache")
        result = cache._backfill_collection_data("api_key")

        # Should still return True as some movies were processed
        assert result is True

    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_backfill_skips_movies_without_tmdb_id(self, mock_load, mock_save):
        """Test backfill skips movies without TMDB ID."""
        mock_load.return_value = {
            "movies": {
                "123": {"title": "No TMDB Movie"}  # No tmdb_id
            },
            "library_count": 1,
        }

        cache = ConcreteCache("/tmp/cache")
        result = cache._backfill_collection_data("api_key")

        assert result is False


class TestBaseCacheUpdateWithBackfill:
    """Tests for BaseCache.update_cache with backfill integration."""

    @patch("recommenders.base.time.sleep")
    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_update_triggers_backfill_when_cache_up_to_date(self, mock_load, mock_save, mock_fetch, mock_sleep):
        """Test that backfill runs even when cache is up to date."""
        mock_load.return_value = {
            "movies": {
                "123": {"tmdb_id": 456, "title": "Test Movie"}  # No collection_id
            },
            "library_count": 1,
        }
        mock_fetch.return_value = {"belongs_to_collection": {"id": 789, "name": "Collection"}}

        mock_plex = Mock()
        mock_item = Mock()
        mock_item.ratingKey = "123"
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = ConcreteCache("/tmp/cache")
        result = cache.update_cache(mock_plex, "Movies", tmdb_api_key="api_key")

        # Returns False (cache was up to date) but backfill should have run
        assert result is False
        assert cache.cache["movies"]["123"]["collection_id"] == 789


class TestBaseCacheGetTmdbDataWithCollection:
    """Tests for BaseCache._get_tmdb_data collection data extraction."""

    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.get_tmdb_keywords")
    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_extracts_collection_info(self, mock_load, mock_extract, mock_keywords, mock_fetch):
        """Test that collection info is extracted from TMDB response."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": 123}
        mock_keywords.return_value = []
        mock_fetch.return_value = {
            "vote_average": 7.5,
            "vote_count": 1000,
            "belongs_to_collection": {"id": 456, "name": "Marvel Collection"},
        }

        cache = ConcreteCache("/tmp/cache")
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, "api_key")

        assert result["collection_id"] == 456
        assert result["collection_name"] == "Marvel Collection"

    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.get_tmdb_keywords")
    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_handles_no_collection(self, mock_load, mock_extract, mock_keywords, mock_fetch):
        """Test that no collection is handled gracefully."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": 123}
        mock_keywords.return_value = []
        mock_fetch.return_value = {"vote_average": 7.5, "vote_count": 1000}

        cache = ConcreteCache("/tmp/cache")
        mock_item = Mock()

        result = cache._get_tmdb_data(mock_item, "api_key")

        assert result["collection_id"] is None
        assert result["collection_name"] is None


class TestBaseCacheGetTmdbDataKeywordsCache:
    """Tests for BaseCache._get_tmdb_data updating keyword caches."""

    @patch("recommenders.base.get_tmdb_keywords")
    @patch("recommenders.base.extract_ids_from_guids")
    @patch("recommenders.base.load_media_cache")
    def test_get_tmdb_data_updates_keywords_cache(self, mock_load, mock_extract, mock_keywords):
        """Test that TMDB keywords are cached on recommender."""
        mock_load.return_value = {"movies": {}, "library_count": 0}
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": 123}
        mock_keywords.return_value = ["action", "hero", "superhero"]

        mock_recommender = Mock()
        mock_recommender.plex_tmdb_cache = {}
        mock_recommender.tmdb_keywords_cache = {}

        cache = ConcreteCache("/tmp/cache", recommender=mock_recommender)
        mock_item = Mock()
        mock_item.ratingKey = "456"

        cache._get_tmdb_data(mock_item, "api_key")

        assert "123" in mock_recommender.tmdb_keywords_cache
        assert mock_recommender.tmdb_keywords_cache["123"] == ["action", "hero", "superhero"]


class TestBaseCacheTVShowBackfill:
    """Tests for backfill behavior with TV shows."""

    @patch("recommenders.base.save_media_cache")
    @patch("recommenders.base.load_media_cache")
    def test_backfill_skips_tv_shows(self, mock_load, mock_save):
        """Test that backfill does not run for TV show caches."""
        mock_load.return_value = {"shows": {}, "library_count": 1}

        class TVCache(BaseCache):
            media_type = "tv"  # Not 'movie'
            media_key = "shows"
            cache_filename = "test_shows.json"

            def _process_item(self, item, tmdb_api_key):
                return {}

        mock_plex = Mock()
        mock_item = Mock()
        mock_item.ratingKey = "123"
        mock_section = Mock()
        mock_section.all.return_value = [mock_item]
        mock_plex.library.section.return_value = mock_section

        cache = TVCache("/tmp/cache")
        # TV caches don't have _backfill_collection_data called (only movies)
        # This test confirms the method exists but the update_cache only calls it for movies
        result = cache.update_cache(mock_plex, "TV Shows", tmdb_api_key="api_key")

        # Should not error - backfill isn't called for TV
        assert result is False  # Cache was up to date


# ------------------------------------------------------------------------
# #157 Phase 3: per-library recommendation loop - library threading,
# cache-key back-compat, and collection/label naming.
# ------------------------------------------------------------------------

SINGLE_MOVIE_LIBRARY_CONFIG = {
    "plex": {"url": "http://localhost", "token": "abc", "movie_library": "Movies"},
    "general": {},
    "weights": {"genre": 0.5, "actor": 0.5},
}

MULTI_MOVIE_LIBRARY_CONFIG = {
    "plex": {"url": "http://localhost", "token": "abc"},
    "general": {},
    "weights": {"genre": 0.5, "actor": 0.5},
    "libraries": [
        {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"},
        {"id": "movies-4k", "name": "Movies 4K", "section": "Movies 4K", "media_type": "movie"},
    ],
}

LIB_MOVIES_4K = {"id": "movies-4k", "name": "Movies 4K", "section": "Movies 4K", "media_type": "movie"}
LIB_MOVIES = {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"}


class TestBaseRecommenderLibraryInit:
    """Tests for BaseRecommender.__init__ library threading (#157 Phase 3)."""

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_no_library_uses_legacy_resolution(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """library=None (default) keeps the legacy library_config_key lookup."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender.library is None
        assert recommender.library_id is None
        assert recommender.library_title == "Movies"
        assert recommender._is_multi_library is False

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_synthesized_single_library_passed_stays_single(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        """Even when the (single, synthesized) library object IS passed
        through (as the new cli.py matrix loop always does), a single
        library for this media type must NOT trigger multi-library
        naming/cache behavior."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        synthesized = {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"}
        recommender = ConcreteRecommender("/path/to/config.yml", library=synthesized)

        assert recommender.library == synthesized
        assert recommender.library_id == "movies"
        assert recommender.library_title == "Movies"
        assert recommender._is_multi_library is False

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_multi_library_sets_library_fields(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """When >1 library shares this media type, the given library's
        section/id are used and multi-library mode is flagged."""
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml", library=LIB_MOVIES_4K)

        assert recommender.library_id == "movies-4k"
        assert recommender.library_title == "Movies 4K"
        assert recommender._is_multi_library is True


class TestBaseRecommenderCacheLibraryPrefix:
    """Tests for per-library cache filename back-compat (#157 Phase 3)."""

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_single_library_user_context_unprefixed(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Single-library install (legacy, no library passed): user context
        has no library prefix, so watched_cache_{user}.json is unchanged."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": ["jason"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender._get_user_context() == "plex_jason"

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_synthesized_single_library_passed_stays_unprefixed(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex
    ):
        """Same as above but library IS passed (matrix loop always passes
        one) - still unprefixed because it's the sole library for the
        media type. This is the single-library byte-identical proof for
        cache filenames."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": ["jason"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        synthesized = {"id": "movies", "name": "Movies", "section": "Movies", "media_type": "movie"}
        recommender = ConcreteRecommender("/path/to/config.yml", library=synthesized)

        assert recommender._get_user_context() == "plex_jason"

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_multi_library_user_context_prefixed(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        """Multi-library install: user context gets a library-id prefix, so
        watched caches for different libraries never collide."""
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": ["jason"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        movies_recommender = ConcreteRecommender("/path/to/config.yml", library=LIB_MOVIES)
        movies_4k_recommender = ConcreteRecommender("/path/to/config.yml", library=LIB_MOVIES_4K)

        assert movies_recommender._get_user_context() == "movies_plex_jason"
        assert movies_4k_recommender._get_user_context() == "movies-4k_plex_jason"
        # Distinct filenames - no cross-library cache collision
        assert movies_recommender._get_user_context() != movies_4k_recommender._get_user_context()


class TestBaseRecommenderCollectionNaming:
    """Tests for per-library collection/label naming (#157 Phase 3)."""

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_single_library_no_suffix(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml")

        assert recommender._library_suffix_for_collection_name() == ""
        assert recommender._library_suffix_for_label() == ""
        assert recommender._cache_library_prefix() == ""

    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_multi_library_adds_suffixes(self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex):
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()

        recommender = ConcreteRecommender("/path/to/config.yml", library=LIB_MOVIES_4K)

        assert recommender._library_suffix_for_collection_name() == " (Movies 4K)"
        assert recommender._library_suffix_for_label() == "_movies-4k"
        assert recommender._cache_library_prefix() == "movies-4k_"

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_sync_plex_collection_single_library_name_unchanged(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        """Single-library install: collection name is byte-identical to
        pre-Phase-3 (no suffix)."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml")
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "🎬 Alice - Recommendation"
        mock_cleanup.assert_called_once()
        assert mock_cleanup.call_args[0][1] == "🎬 Alice - Recommendation"
        mock_cleanup_legacy.assert_called_once()

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_sync_plex_collection_multi_library_adds_suffix(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        """Multi-library install: collection name is suffixed with the
        library name so same-named collections across libraries are
        distinguishable."""
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml", library=LIB_MOVIES_4K)
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "🎬 Alice - Recommendation (Movies 4K)"

    @patch("recommenders.base.build_label_name")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_manage_plex_labels_qualifies_label_for_multi_library(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_build_label
    ):
        """Multi-library install: the internal Plex label is qualified with
        the library id so labeling doesn't collide across libraries."""
        mock_load.return_value = MULTI_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": ["alice"], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_build_label.return_value = "Recommended_movies-4k_alice"

        recommender = ConcreteRecommender("/path/to/config.yml", library=LIB_MOVIES_4K)
        recommender.plex = Mock()
        # Short-circuit deep inside manage_plex_labels right after label_name
        # is built, by making item-finding raise - we only care about the
        # base_label passed into build_label_name.
        recommender._find_plex_items_for_recs = Mock(side_effect=RuntimeError("stop"))

        try:
            recommender.manage_plex_labels([{"title": "Test", "year": 2020}])
        except Exception:
            pass

        assert mock_build_label.called
        # manage_plex_labels now calls build_label_name twice before the
        # short-circuit: once for the item label (base_label), once for
        # the collection-level private label (private_base_label, #261) -
        # both must be library-id-qualified the same way.
        call_base_labels = [call.args[0] for call in mock_build_label.call_args_list]
        assert call_base_labels[0] == "Recommended_movies-4k"
        assert "PrivateCollection_movies-4k" in call_base_labels


class TestCollectionNameTemplate:
    """#267: collections.movie_name_template/tv_name_template - custom
    collection-name templates, with {user}/{media_type} placeholders,
    a safe fallback on an invalid template, and the multi-library
    suffix still applied unconditionally afterward."""

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_default_template_unchanged_from_pre_267_format(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        """No collections.movie_name_template set - byte-for-byte the
        same name a pre-#267 install would have produced."""
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml")
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "🎬 Alice - Recommendation"

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_custom_movie_template_with_user_placeholder(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        config = copy.deepcopy(SINGLE_MOVIE_LIBRARY_CONFIG)
        config["collections"] = {"movie_name_template": "Recommended movies - {user}"}
        mock_load.return_value = config
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml")
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "Recommended movies - Alice"

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_media_type_placeholder_renders_title_case(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        config = copy.deepcopy(SINGLE_MOVIE_LIBRARY_CONFIG)
        config["collections"] = {"movie_name_template": "{media_type} picks for {user}"}
        mock_load.return_value = config
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml")
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "Movie picks for Alice"

    @patch("utils.labels.log_warning")
    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_invalid_template_falls_back_to_default_and_warns(
        self,
        mock_makedirs,
        mock_load,
        mock_tmdb,
        mock_users,
        mock_plex,
        mock_update,
        mock_cleanup,
        mock_cleanup_legacy,
        mock_warn,
    ):
        """A bad placeholder (typo'd {usr} instead of {user}) must never
        crash a run - falls back to the default template instead.
        Patches utils.labels.log_warning (not recommenders.base's own
        reference) since that's where render_collection_name's warning
        actually gets emitted from."""
        config = copy.deepcopy(SINGLE_MOVIE_LIBRARY_CONFIG)
        config["collections"] = {"movie_name_template": "Oops {usr}"}
        mock_load.return_value = config
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml")
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "🎬 Alice - Recommendation"
        mock_warn.assert_called_once()

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_custom_template_still_gets_multi_library_suffix(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        """A custom template can't accidentally break the multi-library
        disambiguation guarantee (#157) - the suffix is always appended
        after the template renders, regardless of what the template is."""
        config = copy.deepcopy(MULTI_MOVIE_LIBRARY_CONFIG)
        config["collections"] = {"movie_name_template": "Recommended movies - {user}"}
        mock_load.return_value = config
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml", library=LIB_MOVIES_4K)
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "Recommended movies - Alice (Movies 4K)"

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_movie_and_tv_templates_are_independent(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        config = copy.deepcopy(SINGLE_MOVIE_LIBRARY_CONFIG)
        config["collections"] = {
            "movie_name_template": "Movie custom - {user}",
            "tv_name_template": "TV custom - {user}",
        }
        mock_load.return_value = config
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteTVRecommender("/path/to/config.yml")
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        collection_name = mock_update.call_args[0][1]
        assert collection_name == "TV custom - Alice"


class TestSyncPlexCollectionRenameOnTemplateChangeWiring:
    """_sync_plex_collection reads collections.rename_on_template_change
    from config and passes it through to update_plex_collection - the
    actual rename decision/logic is utils.plex.update_plex_collection's
    own responsibility (see TestUpdatePlexCollectionRenameOnTemplateChange
    in tests/test_plex.py)."""

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_defaults_true_when_unset(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml")
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        assert mock_update.call_args.kwargs["rename_on_template_change"] is True

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_honors_explicit_false(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        config = copy.deepcopy(SINGLE_MOVIE_LIBRARY_CONFIG)
        config["collections"] = {"rename_on_template_change": False}
        mock_load.return_value = config
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml")
        section = Mock()

        recommender._sync_plex_collection(section, "Recommended_alice", [Mock()], username="alice")

        assert mock_update.call_args.kwargs["rename_on_template_change"] is False

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection")
    @patch("recommenders.base.init_plex")
    @patch("recommenders.base.get_configured_users")
    @patch("recommenders.base.get_tmdb_config")
    @patch("recommenders.base.load_config")
    @patch("os.makedirs")
    def test_private_label_still_passed_through(
        self, mock_makedirs, mock_load, mock_tmdb, mock_users, mock_plex, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        mock_load.return_value = SINGLE_MOVIE_LIBRARY_CONFIG
        mock_users.return_value = {"plex_users": [], "managed_users": [], "admin_user": "admin"}
        mock_tmdb.return_value = {"use_keywords": True, "api_key": "key"}
        mock_plex.return_value = Mock()
        mock_update.return_value = True

        recommender = ConcreteRecommender("/path/to/config.yml")
        section = Mock()

        recommender._sync_plex_collection(
            section, "Recommended_alice", [Mock()], username="alice", private_label="PrivateCollection_alice"
        )

        assert mock_update.call_args.kwargs["private_label"] == "PrivateCollection_alice"


# ------------------------------------------------------------------------
# Core recommendation-engine coverage: label management, candidate scoring,
# and the TMDB/IMDb id-resolution chain shared by movie.py and tv.py.
# ------------------------------------------------------------------------


class ConcreteTVRecommender(BaseRecommender):
    """Concrete TV implementation of BaseRecommender for testing shared logic."""

    media_type = "tv"
    media_key = "shows"
    library_config_key = "tv_library"
    default_library_name = "TV Shows"

    def _load_weights(self, weights_config):
        return {"genre": 0.5, "actor": 0.5}

    def _get_watched_data(self):
        return {"genres": Counter(), "actors": Counter()}

    def _get_watched_count(self):
        return 0

    def _save_watched_cache(self):
        pass

    def _get_media_cache(self):
        return Mock()

    def _find_plex_item(self, section, rec):
        return None

    def _calculate_similarity_from_cache(self, item_info):
        return (0.5, {})

    def _print_similarity_breakdown(self, item_info, score, breakdown):
        pass


def _make_recommender(
    config=None, users=None, library=None, recommender_cls=ConcreteRecommender, config_path="/path/to/config.yml"
):
    """Build a fully-initialized recommender with Plex/TMDB/config init mocked out.

    Deep-copies the config so tests are free to mutate recommender.config
    without polluting the shared module-level fixture dicts.
    """
    config = copy.deepcopy(config if config is not None else SINGLE_MOVIE_LIBRARY_CONFIG)
    users = users or {"plex_users": [], "managed_users": [], "admin_user": "admin"}
    with (
        patch("recommenders.base.load_config", return_value=config),
        patch("recommenders.base.get_configured_users", return_value=users),
        patch("recommenders.base.get_tmdb_config", return_value={"use_keywords": True, "api_key": "key"}),
        patch("recommenders.base.init_plex", return_value=Mock()),
        patch("os.makedirs"),
    ):
        return recommender_cls(config_path, library=library)


class TestGetManagedUsersWatchedData:
    """Tests for BaseRecommender._get_managed_users_watched_data."""

    def test_returns_cached_data_when_not_single_user(self):
        recommender = _make_recommender()
        recommender.watched_data_counters = {"genres": Counter({"a": 1})}
        result = recommender._get_managed_users_watched_data()
        assert result == recommender.watched_data_counters

    def test_returns_cached_data_when_single_user(self):
        recommender = _make_recommender()
        recommender.single_user = "alice"
        recommender.watched_data_counters = {"genres": Counter({"a": 1})}
        result = recommender._get_managed_users_watched_data()
        assert result == recommender.watched_data_counters

    @patch("recommenders.base.MyPlexAccount")
    def test_admin_user_uses_direct_plex_connection(self, mock_account_cls):
        recommender = _make_recommender(users={"plex_users": [], "managed_users": ["admin"], "admin_user": "admin"})
        recommender.watched_data_counters = {}
        recommender.plex = Mock()
        item = Mock(ratingKey="10", lastViewedAt=None, userRating=None, viewCount=1)
        recommender.plex.library.section.return_value.search.return_value = [item]
        media_cache = Mock()
        media_cache.cache = {"movies": {"10": {"tmdb_id": 555}}}
        recommender._get_media_cache = Mock(return_value=media_cache)

        result = recommender._get_managed_users_watched_data()

        assert 10 in recommender.watched_ids
        assert 555 in result["tmdb_ids"]
        mock_account_cls.return_value.user.assert_not_called()

    @patch("recommenders.base.MyPlexAccount")
    def test_non_admin_user_switches_user(self, mock_account_cls):
        recommender = _make_recommender(users={"plex_users": [], "managed_users": ["bob"], "admin_user": "admin"})
        recommender.watched_data_counters = {}
        recommender.plex = Mock()
        switched_plex = Mock()
        recommender.plex.switchUser.return_value = switched_plex
        switched_plex.library.section.return_value.search.return_value = []
        media_cache = Mock()
        media_cache.cache = {"movies": {}}
        recommender._get_media_cache = Mock(return_value=media_cache)

        recommender._get_managed_users_watched_data()

        recommender.plex.switchUser.assert_called_once()

    @patch("recommenders.base.log_error")
    @patch("recommenders.base.MyPlexAccount")
    def test_user_processing_error_continues_to_next_user(self, mock_account_cls, mock_log_error):
        recommender = _make_recommender(
            users={"plex_users": [], "managed_users": ["bob", "admin"], "admin_user": "admin"}
        )
        recommender.watched_data_counters = {}
        recommender.plex = Mock()
        recommender.plex.switchUser.side_effect = plexapi.exceptions.PlexApiException("fail")
        recommender.plex.library.section.return_value.search.return_value = []
        media_cache = Mock()
        media_cache.cache = {"movies": {}}
        recommender._get_media_cache = Mock(return_value=media_cache)

        recommender._get_managed_users_watched_data()

        mock_log_error.assert_called()

    @patch("recommenders.base.MyPlexAccount")
    def test_single_user_admin_alias_uses_admin_user(self, mock_account_cls):
        recommender = _make_recommender(users={"plex_users": [], "managed_users": [], "admin_user": "admin"})
        recommender.single_user = "Administrator"
        recommender.watched_data_counters = {}
        recommender.plex = Mock()
        recommender.plex.library.section.return_value.search.return_value = []
        media_cache = Mock()
        media_cache.cache = {"movies": {}}
        recommender._get_media_cache = Mock(return_value=media_cache)

        recommender._get_managed_users_watched_data()

        recommender.plex.switchUser.assert_not_called()


class TestGetAllLibraryItemsForUser:
    """Tests for BaseRecommender._get_all_library_items_for_user (#273) -
    the per-user-token library snapshot fetch used behind the
    profile_accuracy.enabled config flag (see recommenders/movie.py's/
    tv.py's watched-data builders) instead of _get_all_library_items()'s
    shared admin-token snapshot."""

    def test_admin_user_delegates_to_shared_admin_snapshot(self):
        recommender = _make_recommender(users={"plex_users": [], "managed_users": [], "admin_user": "admin"})
        recommender.plex = Mock()
        admin_items = [Mock(ratingKey="1")]
        recommender.plex.library.section.return_value.all.return_value = admin_items

        result = recommender._get_all_library_items_for_user("admin")

        assert result == admin_items
        # Same admin-token connection reused, not switched.
        recommender.plex.switchUser.assert_not_called()

    def test_admin_user_case_insensitive(self):
        recommender = _make_recommender(users={"plex_users": [], "managed_users": [], "admin_user": "Admin"})
        recommender.plex = Mock()
        admin_items = [Mock(ratingKey="1")]
        recommender.plex.library.section.return_value.all.return_value = admin_items

        result = recommender._get_all_library_items_for_user("admin")

        assert result == admin_items
        recommender.plex.switchUser.assert_not_called()

    @patch("recommenders.base.MyPlexAccount")
    def test_non_admin_user_switches_user_and_caches(self, mock_account_cls):
        recommender = _make_recommender(users={"plex_users": ["bob"], "managed_users": [], "admin_user": "admin"})
        recommender.plex = Mock()
        switched_plex = Mock()
        recommender.plex.switchUser.return_value = switched_plex
        bob_items = [Mock(ratingKey="2")]
        switched_plex.library.section.return_value.all.return_value = bob_items

        first = recommender._get_all_library_items_for_user("bob")
        second = recommender._get_all_library_items_for_user("bob")

        assert first == bob_items
        assert second == bob_items
        # Cached after the first fetch - switchUser only called once.
        recommender.plex.switchUser.assert_called_once()

    @patch("recommenders.base.MyPlexAccount")
    def test_different_users_get_independent_snapshots(self, mock_account_cls):
        recommender = _make_recommender(
            users={"plex_users": ["alice", "bob"], "managed_users": [], "admin_user": "admin"}
        )
        recommender.plex = Mock()
        alice_plex, bob_plex = Mock(), Mock()
        alice_items = [Mock(ratingKey="10", viewCount=3)]
        bob_items = [Mock(ratingKey="10", viewCount=1)]
        alice_plex.library.section.return_value.all.return_value = alice_items
        bob_plex.library.section.return_value.all.return_value = bob_items
        # account.user(...) returns a fresh Mock per call regardless of the
        # username passed in, so key switchUser()'s return value off call
        # order instead (alice is always fetched first below).
        recommender.plex.switchUser.side_effect = [alice_plex, bob_plex]

        alice_result = recommender._get_all_library_items_for_user("alice")
        bob_result = recommender._get_all_library_items_for_user("bob")

        assert alice_result == alice_items
        assert bob_result == bob_items
        assert recommender.plex.switchUser.call_count == 2

    @patch("recommenders.base.log_warning")
    @patch("recommenders.base.MyPlexAccount")
    def test_switch_user_failure_falls_back_to_admin_snapshot(self, mock_account_cls, mock_log_warning):
        recommender = _make_recommender(users={"plex_users": ["bob"], "managed_users": [], "admin_user": "admin"})
        recommender.plex = Mock()
        recommender.plex.switchUser.side_effect = plexapi.exceptions.PlexApiException("fail")
        admin_items = [Mock(ratingKey="1")]
        recommender.plex.library.section.return_value.all.return_value = admin_items

        result = recommender._get_all_library_items_for_user("bob")

        assert result == admin_items
        mock_log_warning.assert_called()


class TestFindPlexItemsForRecs:
    """Tests for BaseRecommender._find_plex_items_for_recs."""

    def test_finds_by_rating_key(self):
        recommender = _make_recommender()
        recommender.plex = Mock()
        found_item = Mock()
        recommender.plex.fetchItem.return_value = found_item
        section = Mock()
        selected = [{"title": "A", "year": 2020, "plex_rating_key": 123}]

        items_found, skipped = recommender._find_plex_items_for_recs(section, selected)

        assert items_found == [found_item]
        assert skipped == []
        found_item.reload.assert_called_once()

    def test_falls_back_to_fuzzy_search_on_fetch_error(self):
        recommender = _make_recommender()
        recommender.plex = Mock()
        recommender.plex.fetchItem.side_effect = Exception("not found")
        found_item = Mock()
        recommender._find_plex_item = Mock(return_value=found_item)
        section = Mock()
        selected = [{"title": "A", "year": 2020, "plex_rating_key": 123}]

        items_found, skipped = recommender._find_plex_items_for_recs(section, selected)

        assert items_found == [found_item]
        recommender._find_plex_item.assert_called_once_with(section, selected[0])

    def test_no_rating_key_uses_fuzzy_search(self):
        recommender = _make_recommender()
        recommender.plex = Mock()
        recommender._find_plex_item = Mock(return_value=None)
        section = Mock()
        selected = [{"title": "Missing", "year": 2019}]

        items_found, skipped = recommender._find_plex_items_for_recs(section, selected)

        assert items_found == []
        assert skipped == ["Missing (2019)"]


class TestRemoveOutdatedLabels:
    """Tests for BaseRecommender._remove_outdated_labels."""

    @patch("recommenders.base.remove_labels_from_items")
    @patch("recommenders.base.categorize_labeled_items")
    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_removes_watched_and_excluded_returns_fresh(self, mock_excl, mock_categorize, mock_remove):
        recommender = _make_recommender()
        section = Mock()
        fresh_item, watched_item, excluded_item = Mock(), Mock(), Mock()
        section.search.return_value = [fresh_item, watched_item, excluded_item]
        mock_categorize.return_value = {
            "fresh": [fresh_item],
            "watched": [watched_item],
            "excluded": [excluded_item],
            "stale": [],
        }

        result = recommender._remove_outdated_labels(section, "Recommended_alice", 7)

        assert result == [fresh_item]
        assert mock_remove.call_count == 2
        reasons = {call.args[3] for call in mock_remove.call_args_list}
        assert reasons == {"watched", "excluded genre"}


class TestBuildScoredCandidates:
    """Tests for BaseRecommender._build_scored_candidates."""

    def test_scores_labeled_items_from_cache(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": {"1": {"title": "A"}}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender._calculate_similarity_from_cache = Mock(return_value=(0.8, {}))
        labeled_item = Mock(ratingKey=1, title="A")

        result = recommender._build_scored_candidates([labeled_item], [], [])

        assert result[1] == (labeled_item, 0.8)

    def test_labeled_item_not_in_cache_gets_zero_score(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": {}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        labeled_item = Mock(ratingKey=2, title="B")

        result = recommender._build_scored_candidates([labeled_item], [], [])

        assert result[2] == (labeled_item, 0.0)

    def test_scoring_exception_defaults_to_zero(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": {"3": {"title": "C"}}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender._calculate_similarity_from_cache = Mock(side_effect=Exception("boom"))
        labeled_item = Mock(ratingKey=3, title="C")

        result = recommender._build_scored_candidates([labeled_item], [], [])

        assert result[3] == (labeled_item, 0.0)

    def test_selected_items_matched_by_rating_key(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": {}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = set()
        plex_item = Mock(ratingKey=5, isPlayed=False)
        selected = [{"title": "D", "year": 2021, "plex_rating_key": 5, "similarity_score": 0.6}]

        result = recommender._build_scored_candidates([], selected, [plex_item])

        assert result[5] == (plex_item, 0.6)

    def test_selected_items_fallback_title_year_match(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": {}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = set()
        plex_item = Mock(ratingKey=6, title="E", year=2018, isPlayed=False)
        selected = [{"title": "E", "year": 2018, "similarity_score": 0.4}]

        result = recommender._build_scored_candidates([], selected, [plex_item])

        assert result[6] == (plex_item, 0.4)

    def test_watched_selected_item_excluded(self):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": {}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = {7}
        plex_item = Mock(ratingKey=7, isPlayed=False)
        selected = [{"title": "F", "year": 2020, "plex_rating_key": 7, "similarity_score": 0.9}]

        result = recommender._build_scored_candidates([], selected, [plex_item])

        assert 7 not in result


class TestFilterCandidatesByRating:
    """Tests for BaseRecommender._filter_candidates_by_rating."""

    def test_no_max_rating_returns_unchanged(self):
        recommender = _make_recommender()
        candidates = {1: (Mock(), 0.5)}

        result = recommender._filter_candidates_by_rating(candidates, None)

        assert result is candidates

    @patch("recommenders.base.is_rating_allowed")
    def test_filters_disallowed_ratings(self, mock_allowed):
        recommender = _make_recommender()
        allowed_item = Mock(contentRating="PG-13")
        blocked_item = Mock(contentRating="R")
        mock_allowed.side_effect = lambda rating, max_rating, media_type: rating == "PG-13"
        candidates = {1: (allowed_item, 0.5), 2: (blocked_item, 0.7)}

        result = recommender._filter_candidates_by_rating(candidates, "PG-13")

        assert 1 in result
        assert 2 not in result


class TestUpdateLabelsByRank:
    """Tests for BaseRecommender._update_labels_by_rank."""

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_keeps_top_scoring_and_evicts_rest(self, mock_remove, mock_add):
        recommender = _make_recommender()
        item_high = Mock(ratingKey=1)
        item_low = Mock(ratingKey=2)
        item_new = Mock(ratingKey=3)
        candidates = {1: (item_high, 0.9), 2: (item_low, 0.1), 3: (item_new, 0.8)}
        unwatched_labeled = [item_high, item_low]

        result = recommender._update_labels_by_rank(candidates, unwatched_labeled, "Recommended_alice", target_count=2)

        result_keys = {int(i.ratingKey) for i in result}
        assert result_keys == {1, 3}
        mock_remove.assert_called_once()
        mock_add.assert_called_once()

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_no_changes_when_already_optimal(self, mock_remove, mock_add):
        recommender = _make_recommender()
        item = Mock(ratingKey=1)
        candidates = {1: (item, 0.9)}

        result = recommender._update_labels_by_rank(candidates, [item], "Recommended_alice", target_count=1)

        mock_remove.assert_not_called()
        mock_add.assert_not_called()
        assert result == [item]

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_tied_scores_broken_by_rating_then_vote_count(self, mock_remove, mock_add):
        """#291: all_candidates values are (plex_item, score) tuples -
        plex_item itself carries no TMDB rating/vote_count, so ties must
        be broken by looking those fields up from the media cache by
        item_id (same cache/key shape _build_scored_candidates already
        reads), not by falling through to arbitrary dict/insertion
        order."""
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {
            "movies": {
                "1": {"rating": 5.0, "vote_count": 100},
                "2": {"rating": 9.0, "vote_count": 5000},
                "3": {"rating": 9.0, "vote_count": 100},
            }
        }
        recommender._get_media_cache = Mock(return_value=media_cache)
        item_low = Mock(ratingKey=1)
        item_best = Mock(ratingKey=2)
        item_mid = Mock(ratingKey=3)
        candidates = {1: (item_low, 0.5), 2: (item_best, 0.5), 3: (item_mid, 0.5)}

        result = recommender._update_labels_by_rank(candidates, [], "Recommended_alice", target_count=3)

        assert [int(i.ratingKey) for i in result] == [2, 3, 1]

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_missing_cache_entry_treated_as_zero_rating_and_votes(self, mock_remove, mock_add):
        """An item labeled in Plex but absent from the media cache (same
        edge case _build_scored_candidates itself defensively handles)
        must not raise - it just sorts as if rating/vote_count were 0."""
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": {"2": {"rating": 9.0, "vote_count": 5000}}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        item_missing = Mock(ratingKey=1)
        item_best = Mock(ratingKey=2)
        candidates = {1: (item_missing, 0.5), 2: (item_best, 0.5)}

        result = recommender._update_labels_by_rank(candidates, [], "Recommended_alice", target_count=2)

        assert [int(i.ratingKey) for i in result] == [2, 1]


class TestMinSimilarityFloor:
    """
    config/tuning.yml movies:/tv: min_similarity - the library path's
    quality gate.

    Before this existed the collection was always padded to
    limit_results no matter how weak the remaining candidates were, so a
    user who had watched most of their library got sub-15%-similarity
    items presented as recommendations.
    """

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_disabled_by_default_pads_to_target(self, mock_remove, mock_add):
        """Default 0.0 must reproduce the historical fill-to-target behavior."""
        recommender = _make_recommender()
        assert recommender.min_similarity == 0.0
        candidates = {1: (Mock(ratingKey=1), 0.9), 2: (Mock(ratingKey=2), 0.01)}

        result = recommender._update_labels_by_rank(candidates, [], "Recommended_alice", target_count=2)

        assert len(result) == 2

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_excludes_candidates_below_floor(self, mock_remove, mock_add):
        recommender = _make_recommender()
        recommender.min_similarity = 0.2
        candidates = {1: (Mock(ratingKey=1), 0.9), 2: (Mock(ratingKey=2), 0.05)}

        result = recommender._update_labels_by_rank(candidates, [], "Recommended_alice", target_count=2)

        assert [int(i.ratingKey) for i in result] == [1]

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_collection_comes_in_short_rather_than_padding(self, mock_remove, mock_add):
        """
        A collection under target_count is a truthful report that the
        library is exhausted for this profile. Padding it with
        sub-threshold items is what produced the original complaint.
        """
        recommender = _make_recommender()
        recommender.min_similarity = 0.5
        candidates = {i: (Mock(ratingKey=i), 0.1) for i in range(1, 11)}
        candidates[1] = (Mock(ratingKey=1), 0.8)

        result = recommender._update_labels_by_rank(candidates, [], "Recommended_alice", target_count=10)

        assert len(result) == 1

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_score_exactly_at_floor_is_kept(self, mock_remove, mock_add):
        """The gate is >=, not > - a score landing exactly on the
        configured threshold qualifies."""
        recommender = _make_recommender()
        recommender.min_similarity = 0.25
        candidates = {1: (Mock(ratingKey=1), 0.25)}

        result = recommender._update_labels_by_rank(candidates, [], "Recommended_alice", target_count=5)

        assert [int(i.ratingKey) for i in result] == [1]


class TestLibraryHealthReport:
    """BaseRecommender._report_library_health (utils/library_health.py)."""

    @patch("recommenders.base.log_warning")
    def test_no_watch_history_reports_nothing(self, mock_warn):
        """
        Depletion means "this user consumed their library" - which is
        meaningless without watch history. A zero-history user facing a
        small library has NOT watched most of it, and saying so would be
        false. Cold start is the recommend_for_no_history path's job.
        """
        recommender = _make_recommender()
        recommender.watched_data_counters = {}
        recommender._report_library_health([{"genres": ["action"]}])
        mock_warn.assert_not_called()
        assert recommender.supply_gaps == []

    @patch("recommenders.base.log_warning")
    def test_depleted_pool_warns_for_a_user_with_history(self, mock_warn):
        recommender = _make_recommender()
        recommender.watched_data_counters = {"genres": {"thriller": 50.0}}
        recommender.limit_results = 50
        recommender._report_library_health([{"genres": ["comedy"]}])
        assert mock_warn.called

    @patch("recommenders.base.log_warning")
    def test_supply_gaps_are_recorded_for_discovery(self, mock_warn):
        """The gap list is what redirects external acquisition."""
        recommender = _make_recommender()
        recommender.watched_data_counters = {"genres": {"thriller": 90.0, "comedy": 10.0}}
        recommender.limit_results = 2
        recommender._report_library_health([{"genres": ["comedy"]}, {"genres": ["comedy"]}])
        assert any(g.genre == "thriller" for g in recommender.supply_gaps)

    @patch("recommenders.base.log_warning")
    def test_reporting_failure_never_breaks_a_run(self, mock_warn):
        recommender = _make_recommender()
        recommender.watched_data_counters = {"genres": {"thriller": 50.0}}
        recommender._report_library_health([{"genres": None}, "not-a-dict"])
        assert recommender.supply_gaps == []


class TestSelectCalibrated:
    """
    config/tuning.yml movies:/tv: calibration_strength - see
    utils/calibration.py.
    """

    @staticmethod
    def _recommender_with_genres(genre_map, profile_genres, strength):
        recommender = _make_recommender()
        recommender.calibration_strength = strength
        recommender.watched_data_counters = {"genres": profile_genres}
        # Enough watched items for the target to clear
        # CALIBRATION_MIN_PROFILE_SAMPLE - calibration refuses a target
        # derived from a handful of titles (see TestMinimumProfileSample).
        recommender.watched_ids = set(range(9000, 9200))
        media_cache = Mock()
        media_cache.cache = {"movies": {str(k): {"genres": v} for k, v in genre_map.items()}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        return recommender

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_disabled_by_default_keeps_pure_score_order(self, mock_remove, mock_add):
        recommender = _make_recommender()
        assert recommender.calibration_strength == 0.0
        candidates = {1: (Mock(ratingKey=1), 0.5), 2: (Mock(ratingKey=2), 0.9)}

        result = recommender._update_labels_by_rank(candidates, [], "Recommended_alice", target_count=2)

        assert [int(i.ratingKey) for i in result] == [2, 1]

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_pulls_overrepresented_genre_back_toward_profile(self, mock_remove, mock_add):
        """
        The reported defect, end to end: a thriller-dominated profile
        whose remaining unwatched pool is mostly family (because the
        thrillers are already watched) must not yield an all-family
        collection.
        """
        genre_map = {i: ["family"] for i in range(1, 11)}
        genre_map.update({i: ["thriller"] for i in range(11, 21)})
        recommender = self._recommender_with_genres(genre_map, {"thriller": 95.0, "family": 5.0}, strength=0.5)
        # Family scores higher across the board - score order alone would
        # return nothing but family.
        candidates = {i: (Mock(ratingKey=i), 0.6 if i <= 10 else 0.5) for i in range(1, 21)}

        result = recommender._update_labels_by_rank(candidates, [], "Recommended_alice", target_count=10)

        family_kept = sum(1 for i in result if genre_map[int(i.ratingKey)] == ["family"])
        assert family_kept < 10, "calibration failed to pull the overrepresented genre down"

    @patch("recommenders.base.add_labels_to_items")
    @patch("recommenders.base.remove_labels_from_items")
    def test_cold_start_profile_falls_back_to_score_order(self, mock_remove, mock_add):
        """No watch history means no distribution to calibrate against."""
        recommender = self._recommender_with_genres({1: ["family"], 2: ["thriller"]}, {}, strength=0.5)
        candidates = {1: (Mock(ratingKey=1), 0.5), 2: (Mock(ratingKey=2), 0.9)}

        result = recommender._update_labels_by_rank(candidates, [], "Recommended_alice", target_count=2)

        assert [int(i.ratingKey) for i in result] == [2, 1]


class TestCalibrationCannotActGuard:
    """
    Calibration works by CHOOSING. Handed no more candidates than slots
    it returns them unchanged - indistinguishable from working, from the
    outside.

    This is not hypothetical: min_similarity 0.10 cut a real 125-candidate
    pool to 48 for a 50-item collection, so every run silently produced an
    uncalibrated collection while printing that it had calibrated one.
    Dropping the floor took the over-represented share from 34% to 18% on
    the same library.
    """

    @staticmethod
    def _recommender(n_candidates, target, strength=0.5):
        recommender = _make_recommender()
        recommender.calibration_strength = strength
        recommender.min_similarity = 0.10
        # Multi-category and well-sampled on purpose: this class tests the
        # candidates-vs-slots guard, not the profile-sample or
        # degenerate-target guards, both of which would otherwise skip
        # calibration before it is reached.
        recommender.watched_data_counters = {"genres": {"thriller": 50.0, "drama": 30.0}}
        recommender.watched_ids = set(range(9000, 9200))
        media_cache = Mock()
        media_cache.cache = {"movies": {str(i): {"genres": ["thriller"]} for i in range(n_candidates)}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        candidates = [(i, (Mock(ratingKey=i), 0.5)) for i in range(n_candidates)]
        return recommender, candidates, media_cache.cache["movies"]

    @patch("recommenders.base.log_warning")
    def test_warns_when_candidates_do_not_exceed_slots(self, mock_warn):
        recommender, candidates, media_items = self._recommender(40, 50)
        recommender._select_calibrated(candidates, media_items, 50)
        assert mock_warn.called, "silent no-op: calibration could not act and said nothing"
        assert "Calibration cannot act" in mock_warn.call_args[0][0]

    @patch("recommenders.base.log_warning")
    def test_warning_names_the_setting_to_change(self, mock_warn):
        recommender, candidates, media_items = self._recommender(40, 50)
        recommender._select_calibrated(candidates, media_items, 50)
        assert "min_similarity" in mock_warn.call_args[0][0]

    @patch("recommenders.base.log_warning")
    def test_no_warning_when_calibration_has_room(self, mock_warn):
        recommender, candidates, media_items = self._recommender(200, 50)
        recommender._select_calibrated(candidates, media_items, 50)
        warned = [c for c in mock_warn.call_args_list if "Calibration cannot act" in str(c)]
        assert not warned

    @patch("recommenders.base.log_warning")
    def test_exactly_equal_counts_still_warns(self, mock_warn):
        """50 candidates for 50 slots is still zero choice."""
        recommender, candidates, media_items = self._recommender(50, 50)
        recommender._select_calibrated(candidates, media_items, 50)
        assert mock_warn.called


class TestSyncPlexCollectionEmpty:
    """Tests for BaseRecommender._sync_plex_collection with no items."""

    @patch("recommenders.base.update_plex_collection")
    def test_returns_false_when_no_final_items(self, mock_update):
        recommender = _make_recommender()

        result = recommender._sync_plex_collection(Mock(), "Recommended_alice", [])

        assert result is False
        mock_update.assert_not_called()


class TestManagePlexLabelsFullFlow:
    """Tests for BaseRecommender.manage_plex_labels orchestration."""

    def _base_recommender(self, users=None):
        users = users or {"plex_users": ["alice"], "managed_users": [], "admin_user": "admin"}
        recommender = _make_recommender(users=users)
        recommender.plex = Mock()
        recommender.config["collections"] = {
            "add_label": True,
            "label_name": "Recommended",
            # True matches config/tuning.example.yml's documented default
            # and the code default (#261) - False was the exact broken
            # production config every fresh install actually ran with
            # (nothing in any install path ever wrote a real tuning.yml),
            # which collapsed every user's label to the identical literal
            # "Recommended". See TestPrivateCollectionsAppendUsernames
            # below for explicit coverage of both the True and False paths.
            "append_usernames": True,
            "private_collections": False,
        }
        recommender.single_user = "alice"
        recommender.confirm_operations = False
        recommender._find_plex_items_for_recs = Mock(return_value=([Mock()], []))
        recommender._remove_outdated_labels = Mock(return_value=[])
        recommender._build_scored_candidates = Mock(return_value={1: (Mock(), 0.9)})
        recommender._update_labels_by_rank = Mock(return_value=[Mock()])
        recommender._sync_plex_collection = Mock(return_value=True)
        recommender._save_watched_cache = Mock()
        return recommender

    @patch("recommenders.base.build_label_name", return_value="Recommended_alice")
    def test_happy_path_returns_sync_result(self, mock_build_label):
        recommender = self._base_recommender()

        result = recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        assert result is True
        recommender._sync_plex_collection.assert_called_once()

    @patch("recommenders.base.build_label_name", return_value="Recommended_alice")
    def test_no_items_found_returns_false(self, mock_build_label):
        recommender = self._base_recommender()
        recommender._find_plex_items_for_recs = Mock(return_value=([], ["Skipped Movie (2020)"]))

        result = recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        assert result is False
        recommender._sync_plex_collection.assert_not_called()

    @patch("recommenders.base.build_label_name", return_value="Recommended_alice")
    def test_confirm_operations_uses_user_selection(self, mock_build_label):
        recommender = self._base_recommender()
        recommender.confirm_operations = True
        recommender._user_select_recommendations = Mock(return_value=[{"title": "Movie", "year": 2020}])

        recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        recommender._user_select_recommendations.assert_called_once()

    @patch("recommenders.base.build_label_name", return_value="Recommended_alice")
    def test_confirm_operations_empty_selection_passes_empty_list(self, mock_build_label):
        recommender = self._base_recommender()
        recommender.confirm_operations = True
        recommender._user_select_recommendations = Mock(return_value=[])

        recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        args = recommender._find_plex_items_for_recs.call_args[0]
        assert args[1] == []

    def test_private_label_applied_even_with_private_collections_disabled(self):
        """#291 removal-path prerequisite: the PrivateCollection_<user>
        label used to CONFIRM ownership for removal is applied by
        _sync_plex_collection unconditionally, before the
        collections.private_collections check below it is ever reached -
        that setting only gates the cross-user Plex exclude-filter
        (apply_user_label_restrictions), never whether the label itself
        gets set on the collection. So a collection's ownership stays
        identifiable via that label even in a private_collections: false
        install - utils.plex.remove_owned_collection (the #291 removal
        path) can rely on it regardless of this setting."""
        recommender = self._base_recommender()  # fixture already sets private_collections: False
        assert recommender.config["collections"]["private_collections"] is False

        recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        args = recommender._sync_plex_collection.call_args[0]
        # (section, label_name, final_items, username, private_label)
        assert args[4] == "PrivateCollection_alice"

    @patch("recommenders.base.apply_user_label_restrictions")
    def test_private_collections_applies_restrictions(self, mock_apply):
        """#261 regression: uses the REAL build_label_name (not a hardcoded
        patch) and a multi-user fixture, and asserts on the actual per-user
        PrivateCollection_* labels apply_user_label_restrictions receives -
        not just that it was called. The old version of this test patched
        build_label_name to a fixed "Recommended_alice" and used a
        single-user fixture, so it couldn't have caught #261: every user
        collapsing to the identical label was invisible to it."""
        recommender = self._base_recommender(
            users={"plex_users": ["alice", "bob"], "managed_users": [], "admin_user": "admin"}
        )
        recommender.single_user = "alice"
        recommender.config["collections"]["private_collections"] = True

        recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        mock_apply.assert_called_once()
        all_user_private_labels = mock_apply.call_args[0][1]
        # A user owns one private label PER LIBRARY (#332), so this is a
        # list even on a single-library install.
        # Per media type (#340): filterMovies and filterTelevision are
        # separate Plex filters and must not receive each other's labels.
        assert all_user_private_labels == {
            "alice": {"movie": ["PrivateCollection_alice"], "tv": ["PrivateCollection_alice"]},
            "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
        }

    @patch("recommenders.base.apply_user_label_restrictions")
    def test_applies_restrictions_only_once_across_a_shared_run_state(self, mock_apply):
        """#360: a real run invokes manage_plex_labels() once per
        (library x user) pair, all sharing the SAME
        label_restrictions_state dict (utils.cli.run_recommender_main
        creates it once and threads it through every process_func call -
        see tests/test_cli.py::
        test_label_restrictions_state_is_the_same_object_across_every_call).
        Simulate two such calls here with two recommender instances (one
        per user, standing in for two iterations of that loop) sharing
        one dict. apply_user_label_restrictions must fire exactly once,
        on the first call, and still receive the correct FULL cross-user
        label set built from the full configured user list - never
        scoped down to whichever single instance happened to trigger it,
        which is what makes calling it only once still correct."""
        shared_state = {}

        users = {"plex_users": ["alice", "bob"], "managed_users": [], "admin_user": "admin"}
        first = self._base_recommender(users=users)
        first.single_user = "alice"
        first.config["collections"]["private_collections"] = True
        first._label_restrictions_state = shared_state

        second = self._base_recommender(users=users)
        second.single_user = "bob"
        second.config["collections"]["private_collections"] = True
        second._label_restrictions_state = shared_state

        first.manage_plex_labels([{"title": "Movie", "year": 2020}])
        second.manage_plex_labels([{"title": "Movie", "year": 2020}])

        mock_apply.assert_called_once()
        all_user_private_labels = mock_apply.call_args[0][1]
        assert all_user_private_labels == {
            "alice": {"movie": ["PrivateCollection_alice"], "tv": ["PrivateCollection_alice"]},
            "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
        }

    @patch("recommenders.base.apply_user_label_restrictions")
    def test_a_fresh_instance_with_no_shared_state_still_applies_every_time(self, mock_apply):
        """Direct/test instantiation (or any caller that never threads
        label_restrictions_state through - e.g. every pre-#360 call site)
        must see zero behavior change: each instance gets its own fresh
        {} (see BaseRecommender.__init__), so restrictions are still
        applied on every single call, exactly as before #360."""
        users = {"plex_users": ["alice"], "managed_users": [], "admin_user": "admin"}
        first = self._base_recommender(users=users)
        first.config["collections"]["private_collections"] = True
        second = self._base_recommender(users=users)
        second.config["collections"]["private_collections"] = True

        first.manage_plex_labels([{"title": "Movie", "year": 2020}])
        second.manage_plex_labels([{"title": "Movie", "year": 2020}])

        assert mock_apply.call_count == 2

    @patch("recommenders.base.get_max_rating_for_user", return_value="PG-13")
    @patch("recommenders.base.build_label_name", return_value="Recommended_alice")
    def test_max_rating_filters_candidates(self, mock_build_label, mock_max_rating):
        recommender = self._base_recommender()
        recommender._filter_candidates_by_rating = Mock(return_value={1: (Mock(), 0.9)})

        recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        recommender._filter_candidates_by_rating.assert_called_once()

    def test_no_recommendations_returns_false(self):
        recommender = self._base_recommender()

        result = recommender.manage_plex_labels([])

        assert result is False

    def test_add_label_disabled_returns_false(self):
        recommender = self._base_recommender()
        recommender.config["collections"]["add_label"] = False

        result = recommender.manage_plex_labels([{"title": "Movie"}])

        assert result is False

    @patch("recommenders.base.build_label_name", return_value="Recommended_alice")
    def test_target_count_uses_limit_results(self, mock_build_label):
        """PR1: the final collection size (target_count) is
        self.limit_results, resolved once in __init__ - not a second,
        independent 50/20 default re-derived here from the undocumented
        general.limit_plex_results key."""
        recommender = self._base_recommender()
        recommender.limit_results = 7

        recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        assert recommender._update_labels_by_rank.call_args[0][3] == 7


class TestPrivateCollectionsAppendUsernames:
    """#261: collections.append_usernames' True/False paths, explicitly.

    True is both the documented default (config/tuning.example.yml) and
    the code default - every user gets a distinct label, so
    private_collections' exclude filters correctly isolate each user.
    False is only safe with exactly one user configured (build_label_name
    just returns the bare base label either way - nothing to disambiguate
    since there's nothing to disambiguate FROM); with more than one user
    it must fail loud instead of applying restrictions (see
    TestManagePlexLabelsFullFlow._base_recommender for the shared setup
    this subclasses)."""

    def _recommender(self, users, append_usernames, single_user="alice"):
        recommender = _make_recommender(users=users)
        recommender.plex = Mock()
        recommender.config["collections"] = {
            "add_label": True,
            "label_name": "Recommended",
            "append_usernames": append_usernames,
            "private_collections": True,
        }
        recommender.single_user = single_user
        recommender.confirm_operations = False
        recommender._find_plex_items_for_recs = Mock(return_value=([Mock()], []))
        recommender._remove_outdated_labels = Mock(return_value=[])
        recommender._build_scored_candidates = Mock(return_value={1: (Mock(), 0.9)})
        recommender._update_labels_by_rank = Mock(return_value=[Mock()])
        recommender._sync_plex_collection = Mock(return_value=True)
        recommender._save_watched_cache = Mock()
        return recommender

    @patch("recommenders.base.apply_user_label_restrictions")
    def test_append_usernames_true_multi_user_applies_distinct_labels(self, mock_apply):
        recommender = self._recommender(
            users={"plex_users": ["alice", "bob"], "managed_users": [], "admin_user": "admin"},
            append_usernames=True,
        )

        result = recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        assert result is True
        mock_apply.assert_called_once()
        assert mock_apply.call_args[0][1] == {
            "alice": {"movie": ["PrivateCollection_alice"], "tv": ["PrivateCollection_alice"]},
            "bob": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]},
        }

    @patch("recommenders.base.apply_user_label_restrictions")
    def test_append_usernames_false_single_user_still_applies(self, mock_apply):
        """A single configured user has nothing to disambiguate from, so
        False is harmless here - apply_user_label_restrictions itself
        also no-ops on a single-entry dict (see utils/plex_policy.py)."""
        recommender = self._recommender(
            users={"plex_users": ["alice"], "managed_users": [], "admin_user": "admin"},
            append_usernames=False,
        )

        result = recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        assert result is True
        mock_apply.assert_called_once()
        assert mock_apply.call_args[0][1] == {"alice": {"movie": ["PrivateCollection"], "tv": ["PrivateCollection"]}}

    @patch("recommenders.base.log_warning")
    @patch("recommenders.base.apply_user_label_restrictions")
    def test_append_usernames_false_multi_user_skips_and_warns(self, mock_apply, mock_warn):
        """The #261 failure mode itself: more than one user configured
        with append_usernames false must never reach
        apply_user_label_restrictions (every user's label would be the
        identical bare "PrivateCollection", so the exclude filter would
        hide the one shared collection - and its items - from everyone
        instead of isolating each user's own). Must fail loud instead,
        naming the config key so an operator can actually fix it."""
        recommender = self._recommender(
            users={"plex_users": ["alice", "bob"], "managed_users": [], "admin_user": "admin"},
            append_usernames=False,
        )

        result = recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        assert result is True
        mock_apply.assert_not_called()
        mock_warn.assert_called_once()
        warning_text = mock_warn.call_args[0][0]
        assert "append_usernames" in warning_text
        assert "tuning.yml" in warning_text


class TestCollectionTitleUsesRealUsername:
    """#261 regression: the collection title is always built from the
    real username (self.single_user), even with append_usernames false
    (the exact broken production default) - never from label_name string
    surgery. Before the fix, this exact config produced the literal
    title "🎬 Recommended - Recommendation" for every user, since
    label_name was just the bare base label with no "Recommended_" prefix
    to strip."""

    @patch("recommenders.base.cleanup_legacy_unnamed_collection")
    @patch("recommenders.base.cleanup_old_collections")
    @patch("recommenders.base.update_plex_collection", return_value=True)
    def test_title_uses_real_username_even_with_append_usernames_false(
        self, mock_update, mock_cleanup, mock_cleanup_legacy
    ):
        recommender = _make_recommender(users={"plex_users": ["alice"], "managed_users": [], "admin_user": "admin"})
        recommender.plex = Mock()
        recommender.single_user = "alice"
        recommender.config["collections"] = {
            "add_label": True,
            "label_name": "Recommended",
            "append_usernames": False,
            "private_collections": False,
        }
        recommender.confirm_operations = False
        recommender._find_plex_items_for_recs = Mock(return_value=([Mock()], []))
        recommender._remove_outdated_labels = Mock(return_value=[])
        recommender._build_scored_candidates = Mock(return_value={1: (Mock(), 0.9)})
        recommender._update_labels_by_rank = Mock(return_value=[Mock()])
        recommender._save_watched_cache = Mock()

        result = recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        assert result is True
        collection_name = mock_update.call_args[0][1]
        assert "Alice" in collection_name
        assert collection_name == "🎬 Alice - Recommendation"
        assert collection_name != "🎬 Recommended - Recommendation"


class TestManagePlexLabelsExceptionHandling:
    """Tests for BaseRecommender.manage_plex_labels error handling."""

    def test_plex_exception_returns_false(self):
        recommender = _make_recommender(users={"plex_users": ["alice"], "managed_users": [], "admin_user": "admin"})
        recommender.plex = Mock()
        recommender.plex.library.section.side_effect = plexapi.exceptions.PlexApiException("boom")

        result = recommender.manage_plex_labels([{"title": "Movie", "year": 2020}])

        assert result is False


class TestGetPlexItemTmdbId:
    """Tests for BaseRecommender._get_plex_item_tmdb_id cache-miss path."""

    @patch("recommenders.base.get_tmdb_id_for_item")
    def test_cache_miss_saves_and_returns_id(self, mock_get_id):
        recommender = _make_recommender()
        recommender._save_watched_cache = Mock()
        mock_get_id.return_value = 999
        plex_item = Mock(ratingKey="42")

        result = recommender._get_plex_item_tmdb_id(plex_item)

        assert result == 999
        assert recommender.plex_tmdb_cache["42"] == 999
        recommender._save_watched_cache.assert_called_once()

    @patch("recommenders.base.get_tmdb_id_for_item")
    def test_cache_miss_no_id_found_does_not_save(self, mock_get_id):
        recommender = _make_recommender()
        recommender._save_watched_cache = Mock()
        mock_get_id.return_value = None
        plex_item = Mock(ratingKey="42")

        result = recommender._get_plex_item_tmdb_id(plex_item)

        assert result is None
        recommender._save_watched_cache.assert_not_called()


class TestGetPlexItemImdbId:
    """Tests for BaseRecommender._get_plex_item_imdb_id fallback chain."""

    @patch("recommenders.base.extract_ids_from_guids")
    def test_returns_imdb_from_guids(self, mock_extract):
        recommender = _make_recommender()
        mock_extract.return_value = {"imdb_id": "tt111", "tmdb_id": None}

        result = recommender._get_plex_item_imdb_id(Mock())

        assert result == "tt111"

    @patch("recommenders.base.extract_ids_from_guids")
    def test_falls_back_to_legacy_guid_attribute(self, mock_extract):
        recommender = _make_recommender()
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": None}
        plex_item = Mock(guid="imdb://tt222")

        result = recommender._get_plex_item_imdb_id(plex_item)

        assert result == "tt222"

    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.extract_ids_from_guids")
    def test_falls_back_to_tmdb_movie_lookup(self, mock_extract, mock_fetch):
        recommender = _make_recommender()  # media_type == 'movie'
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": None}
        recommender._get_plex_item_tmdb_id = Mock(return_value=555)
        mock_fetch.return_value = {"imdb_id": "tt333"}
        plex_item = Mock(guid=None)

        result = recommender._get_plex_item_imdb_id(plex_item)

        assert result == "tt333"
        assert "movie/555" in mock_fetch.call_args[0][0]

    @patch("recommenders.base.fetch_tmdb_with_retry")
    @patch("recommenders.base.extract_ids_from_guids")
    def test_falls_back_to_tmdb_tv_external_ids(self, mock_extract, mock_fetch):
        recommender = _make_recommender(recommender_cls=ConcreteTVRecommender)
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": None}
        recommender._get_plex_item_tmdb_id = Mock(return_value=555)
        mock_fetch.return_value = {"imdb_id": "tt444"}
        plex_item = Mock(guid=None)

        result = recommender._get_plex_item_imdb_id(plex_item)

        assert result == "tt444"
        assert "tv/555/external_ids" in mock_fetch.call_args[0][0]

    @patch("recommenders.base.extract_ids_from_guids")
    def test_returns_none_when_no_tmdb_id_available(self, mock_extract):
        recommender = _make_recommender()
        mock_extract.return_value = {"imdb_id": None, "tmdb_id": None}
        recommender._get_plex_item_tmdb_id = Mock(return_value=None)
        plex_item = Mock(guid=None)

        result = recommender._get_plex_item_imdb_id(plex_item)

        assert result is None


class TestGetTmdbIdViaImdb:
    """Tests for BaseRecommender._get_tmdb_id_via_imdb."""

    @patch("recommenders.base.fetch_tmdb_with_retry")
    def test_returns_tmdb_id_for_movie(self, mock_fetch):
        recommender = _make_recommender()
        recommender._get_plex_item_imdb_id = Mock(return_value="tt123")
        recommender.tmdb_api_key = "key"
        mock_fetch.return_value = {"movie_results": [{"id": 42}]}

        result = recommender._get_tmdb_id_via_imdb(Mock())

        assert result == 42

    def test_returns_none_without_imdb_id(self):
        recommender = _make_recommender()
        recommender._get_plex_item_imdb_id = Mock(return_value=None)

        result = recommender._get_tmdb_id_via_imdb(Mock())

        assert result is None

    @patch("recommenders.base.fetch_tmdb_with_retry")
    def test_returns_none_when_no_results(self, mock_fetch):
        recommender = _make_recommender()
        recommender._get_plex_item_imdb_id = Mock(return_value="tt123")
        recommender.tmdb_api_key = "key"
        mock_fetch.return_value = {"movie_results": []}

        result = recommender._get_tmdb_id_via_imdb(Mock())

        assert result is None


class TestGetTmdbKeywordsForId:
    """Tests for BaseRecommender._get_tmdb_keywords_for_id."""

    def test_returns_empty_set_without_tmdb_id(self):
        recommender = _make_recommender()

        assert recommender._get_tmdb_keywords_for_id(None) == set()

    def test_returns_empty_set_when_keywords_disabled(self):
        recommender = _make_recommender()
        recommender.use_tmdb_keywords = False

        assert recommender._get_tmdb_keywords_for_id(123) == set()

    @patch("recommenders.base.get_tmdb_keywords")
    def test_fetches_and_saves_keywords(self, mock_keywords):
        recommender = _make_recommender()
        recommender._save_watched_cache = Mock()
        mock_keywords.return_value = ["a", "b"]

        result = recommender._get_tmdb_keywords_for_id(123)

        assert result == {"a", "b"}
        recommender._save_watched_cache.assert_called_once()


class TestGetRecommendationsBranches:
    """Additional branch coverage for BaseRecommender.get_recommendations."""

    def _recommender_with_cache(self, items):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": items}
        media_cache._save_cache = Mock()
        recommender._get_media_cache = Mock(return_value=media_cache)
        # #291: non-empty, so these tests (about quality filters/
        # caching/tiered selection/debug logging) never trip the
        # zero-watch-history gate (see TestRecommendForNoHistoryGate for
        # that).
        recommender.watched_ids = {90001, 90002, 90003}
        recommender.profile_hash = "hash1"
        recommender.exclude_genres = []
        recommender.user_preferences = {}
        recommender.randomize_recommendations = False
        return recommender, media_cache

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_quality_filter_excludes_low_rated(self, mock_excl):
        items = {
            "1": {"title": "Good", "rating": 8.0, "vote_count": 500, "genres": []},
            "2": {"title": "Bad", "rating": 2.0, "vote_count": 5, "genres": []},
        }
        recommender, media_cache = self._recommender_with_cache(items)
        recommender.config["quality_filters"] = {"min_rating": 5.0, "min_vote_count": 100}

        result = recommender.get_recommendations()

        titles = [i["title"] for i in result["plex_recommendations"]]
        assert "Bad" not in titles

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_quality_filter_excludes_low_rated_tv_shows(self, mock_excl):
        """TV parity for test_quality_filter_excludes_low_rated above.
        Regression test for the tv: quality_filters no-op bug: once
        ShowCache._process_item actually populates rating/vote_count (see
        CHANGELOG), this shared filter must apply to "shows"-keyed cache
        entries exactly the way it already does for "movies"-keyed ones -
        proving the fix isn't just that the fields exist, but that they
        actually drive real filtering for TV end to end."""
        recommender = _make_recommender(
            config={
                "plex": {"url": "http://localhost", "token": "abc", "tv_library": "TV Shows"},
                "general": {},
                "weights": {"genre": 0.5, "actor": 0.5},
            },
            recommender_cls=ConcreteTVRecommender,
        )
        media_cache = Mock()
        media_cache.cache = {
            "shows": {
                "1": {"title": "Good Show", "rating": 8.0, "vote_count": 500, "genres": []},
                "2": {"title": "Bad Show", "rating": 2.0, "vote_count": 5, "genres": []},
            }
        }
        media_cache._save_cache = Mock()
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = {90001, 90002, 90003}  # #291: non-empty, so the zero-watch-history gate never fires
        recommender.profile_hash = "hash1"
        recommender.exclude_genres = []
        recommender.user_preferences = {}
        recommender.randomize_recommendations = False
        recommender.media_config = {"quality_filters": {"min_rating": 5.0, "min_vote_count": 100}}

        result = recommender.get_recommendations()

        titles = [i["title"] for i in result["plex_recommendations"]]
        assert "Bad Show" not in titles
        assert "Good Show" in titles

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_quality_filter_tv_show_missing_rating_treated_as_zero_not_crashed(self, mock_excl):
        """A show cache entry with no rating/vote_count data at all (the
        exact shape every existing on-disk show cache has today, before
        this fix's CACHE_VERSION bump forces a rebuild) must not crash the
        filter, and is treated the same way a movie with no rating data
        already is: as below any positive threshold, not specially
        exempted. In production this is a non-issue for pre-fix caches
        specifically because bumping CACHE_VERSION deletes and fully
        rebuilds them on the next run (see utils/config.py CACHE_VERSION
        comment / CHANGELOG) - this test documents the fallback behavior
        for the narrower, ongoing case of a genuine per-item TMDB lookup
        miss, which is symmetric with movies and unchanged by this fix."""
        recommender = _make_recommender(
            config={
                "plex": {"url": "http://localhost", "token": "abc", "tv_library": "TV Shows"},
                "general": {},
                "weights": {"genre": 0.5, "actor": 0.5},
            },
            recommender_cls=ConcreteTVRecommender,
        )
        media_cache = Mock()
        media_cache.cache = {"shows": {"1": {"title": "No TMDB Match", "genres": []}}}
        media_cache._save_cache = Mock()
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = {90001, 90002, 90003}  # #291: non-empty, so the zero-watch-history gate never fires
        recommender.profile_hash = "hash1"
        recommender.exclude_genres = []
        recommender.user_preferences = {}
        recommender.randomize_recommendations = False
        recommender.media_config = {"quality_filters": {"min_rating": 5.0, "min_vote_count": 100}}

        result = recommender.get_recommendations()  # must not raise

        assert result["plex_recommendations"] == []

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_uses_cached_score_when_profile_hash_matches(self, mock_excl):
        items = {
            "1": {
                "title": "Cached",
                "rating": 8,
                "vote_count": 500,
                "genres": [],
                "profile_hash": "hash1",
                "cached_score": 0.77,
                "score_breakdown": {},
            }
        }
        recommender, media_cache = self._recommender_with_cache(items)
        recommender._calculate_similarity_from_cache = Mock(side_effect=AssertionError("should not recompute"))

        result = recommender.get_recommendations()

        assert result["plex_recommendations"][0]["similarity_score"] == 0.77
        media_cache._save_cache.assert_not_called()

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_scoring_error_skips_item(self, mock_excl):
        items = {"1": {"title": "Bad Score", "rating": 8, "vote_count": 500, "genres": []}}
        recommender, media_cache = self._recommender_with_cache(items)
        recommender._calculate_similarity_from_cache = Mock(side_effect=KeyError("boom"))

        result = recommender.get_recommendations()

        assert result["plex_recommendations"] == []

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_tied_scores_ordered_best_rated_first(self, mock_excl):
        """#291: when every candidate scores identically (verified
        against a real 289-movie cache: every calculate_similarity_score
        component returns 0.0 against an empty/cold-start profile), the
        primary sort must break the tie by (rating, vote_count) rather
        than falling through to media-cache insertion order (which is
        alphabetical by title) - a cold-start collection should surface
        well-regarded, well-known unwatched titles, the standard
        cold-start fallback, not an arbitrary alphabetical slice."""
        items = {
            "1": {"title": "Alphabetically First But Mediocre", "rating": 5.0, "vote_count": 100, "genres": []},
            "2": {"title": "Best Rated", "rating": 9.0, "vote_count": 5000, "genres": []},
            "3": {"title": "Same Rating More Votes", "rating": 7.0, "vote_count": 9000, "genres": []},
            "4": {"title": "Same Rating Fewer Votes", "rating": 7.0, "vote_count": 100, "genres": []},
        }
        recommender, media_cache = self._recommender_with_cache(items)
        recommender._calculate_similarity_from_cache = Mock(return_value=(0.0, {}))

        result = recommender.get_recommendations()

        titles = [i["title"] for i in result["plex_recommendations"]]
        assert titles == [
            "Best Rated",
            "Same Rating More Votes",
            "Same Rating Fewer Votes",
            "Alphabetically First But Mediocre",
        ]

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_no_unwatched_items_returns_empty(self, mock_excl):
        recommender, media_cache = self._recommender_with_cache({})

        result = recommender.get_recommendations()

        assert result == {"plex_recommendations": []}

    @patch("recommenders.base.select_tiered_recommendations")
    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_randomize_recommendations_uses_tiered_selection(self, mock_excl, mock_tiered):
        items = {"1": {"title": "A", "rating": 8, "vote_count": 500, "genres": []}}
        recommender, media_cache = self._recommender_with_cache(items)
        recommender.randomize_recommendations = True
        mock_tiered.return_value = [items["1"]]

        recommender.get_recommendations()

        mock_tiered.assert_called_once()

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_debug_logging_prints_breakdown(self, mock_excl):
        items = {"1": {"title": "A", "rating": 8, "vote_count": 500, "genres": []}}
        recommender, media_cache = self._recommender_with_cache(items)
        recommender._print_similarity_breakdown = Mock()
        with patch("recommenders.base.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True
            recommender.get_recommendations()

        recommender._print_similarity_breakdown.assert_called()

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_refreshes_watched_data_when_ids_missing(self, mock_excl):
        recommender, media_cache = self._recommender_with_cache({})
        recommender.cached_watched_count = 5
        recommender.watched_ids = set()
        recommender._get_watched_data = Mock(return_value={"genres": {}})
        recommender._save_watched_cache = Mock()

        recommender.get_recommendations()

        recommender._get_watched_data.assert_called_once()
        recommender._save_watched_cache.assert_called_once()

    @patch("recommenders.base.get_excluded_genres_for_user")
    def test_excluded_genres_filtered_and_counted(self, mock_excl):
        mock_excl.return_value = ["horror"]
        items = {
            "1": {"title": "Scary", "rating": 8, "vote_count": 500, "genres": ["Horror"]},
            "2": {"title": "Fine", "rating": 8, "vote_count": 500, "genres": ["Comedy"]},
        }
        recommender, media_cache = self._recommender_with_cache(items)

        result = recommender.get_recommendations()

        titles = [i["title"] for i in result["plex_recommendations"]]
        assert titles == ["Fine"]


class TestRecommendForNoHistoryGate:
    """#291: a user with ZERO watch history gets no collection at all
    when movies.recommend_for_no_history/tv.recommend_for_no_history is
    explicitly set to False. Default True means a zero-history user
    gets EXACTLY today's behavior - no change for anyone who doesn't
    touch the setting (see RECOMMEND_FOR_NO_HISTORY_DEFAULT's own
    comment in utils/config.py for the cold-start rationale). Only on
    the explicit opt-out does get_recommendations() also remove any
    collection curatarr already created for that user - see
    TestRemoveCollectionForNoHistory and
    tests/test_plex.py::TestRemoveOwnedCollection for the
    ownership-safety rules that removal path enforces (never inferred
    from title/emoji/name pattern, only the PrivateCollection_<user>
    label)."""

    def _recommender_with_watched_ids(self, watched_ids, recommend_for_no_history=None):
        recommender = _make_recommender()
        media_cache = Mock()
        media_cache.cache = {"movies": {"1": {"title": "Candidate", "rating": 8.0, "vote_count": 500, "genres": []}}}
        media_cache._save_cache = Mock()
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender.watched_ids = set(watched_ids)
        recommender.profile_hash = "hash1"
        recommender.exclude_genres = []
        recommender.user_preferences = {}
        recommender.randomize_recommendations = False
        if recommend_for_no_history is not None:
            recommender.media_config = {"recommend_for_no_history": recommend_for_no_history}
        return recommender

    @patch("recommenders.base.log_warning")
    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_default_on_creates_recommendations_for_zero_history(self, mock_excl, mock_warn):
        """No behavior change for anyone who doesn't touch the setting -
        a zero-history user still gets a collection, exactly as today."""
        recommender = self._recommender_with_watched_ids(set())
        recommender.single_user = "alice"

        result = recommender.get_recommendations()

        assert len(result["plex_recommendations"]) == 1
        assert result["plex_recommendations"][0]["title"] == "Candidate"
        mock_warn.assert_not_called()

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_default_on_is_the_documented_code_default(self, mock_excl):
        """Same as above but relying on RECOMMEND_FOR_NO_HISTORY_DEFAULT
        rather than an explicit config value, so a future default flip
        would fail this test loudly."""
        assert RECOMMEND_FOR_NO_HISTORY_DEFAULT is True
        recommender = self._recommender_with_watched_ids(set())
        recommender.single_user = "alice"

        result = recommender.get_recommendations()

        assert len(result["plex_recommendations"]) == 1

    @patch("recommenders.base.log_warning")
    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_explicit_off_skips_zero_history_user_and_logs_clearly(self, mock_excl, mock_warn):
        recommender = self._recommender_with_watched_ids(set(), recommend_for_no_history=False)
        recommender.single_user = "alice"
        recommender._remove_collection_for_no_history = Mock()

        result = recommender.get_recommendations()

        assert result == {"plex_recommendations": []}
        message = mock_warn.call_args[0][0]
        assert "alice" in message
        assert "no watch history" in message
        assert "recommend_for_no_history" in message

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_explicit_off_triggers_removal_for_the_right_user(self, mock_excl):
        recommender = self._recommender_with_watched_ids(set(), recommend_for_no_history=False)
        recommender.single_user = "alice"
        recommender._remove_collection_for_no_history = Mock()

        recommender.get_recommendations()

        recommender._remove_collection_for_no_history.assert_called_once_with("alice")

    @patch("recommenders.base.get_excluded_genres_for_user", return_value=[])
    def test_off_does_not_affect_or_remove_for_a_user_with_history(self, mock_excl):
        """Even a single watched item counts as history - only a
        genuine zero is gated/removed."""
        recommender = self._recommender_with_watched_ids({101}, recommend_for_no_history=False)
        recommender.single_user = "alice"
        recommender._remove_collection_for_no_history = Mock()

        result = recommender.get_recommendations()

        assert len(result["plex_recommendations"]) == 1
        recommender._remove_collection_for_no_history.assert_not_called()


class TestRemoveCollectionForNoHistory:
    """Tests for BaseRecommender._remove_collection_for_no_history - the
    #291 recommend_for_no_history: false removal path. The actual
    find/confirm/remove logic lives in utils.plex.remove_owned_collection
    (see tests/test_plex.py::TestRemoveOwnedCollection for that); these
    tests only cover this method's own wiring (label computed correctly,
    add_label gate, defensive section-access handling)."""

    @patch("recommenders.base.remove_owned_collection")
    def test_delegates_with_this_users_computed_private_label(self, mock_remove):
        recommender = _make_recommender()
        recommender.single_user = "alice"
        section = Mock()
        recommender.plex.library.section.return_value = section

        recommender._remove_collection_for_no_history("alice")

        mock_remove.assert_called_once()
        args, _kwargs = mock_remove.call_args
        assert args[0] is section
        # #357: a list of candidate label forms, not a bare string - here
        # just the current (normalized) form since "alice" has nothing to
        # normalize away, so the legacy form is identical and omitted
        # (see _compute_legacy_private_label_names).
        assert args[1] == ["PrivateCollection_alice"]
        assert args[2] == "alice"
        assert "no watch history" in args[3]

    @patch("recommenders.base.remove_owned_collection")
    def test_add_label_disabled_never_touches_plex(self, mock_remove):
        """If curatarr never applies PrivateCollection_* labels in this
        config, ownership can never be confirmed - skip entirely rather
        than guess (mirrors utils.plex.update_plex_collection's own
        label_name/private_label on/off gate)."""
        recommender = _make_recommender()
        recommender.config["collections"] = {"add_label": False}
        recommender.single_user = "alice"

        recommender._remove_collection_for_no_history("alice")

        mock_remove.assert_not_called()
        recommender.plex.library.section.assert_not_called()

    @patch("recommenders.base.log_warning")
    @patch("recommenders.base.remove_owned_collection")
    def test_section_access_failure_logs_and_does_not_raise(self, mock_remove, mock_warn):
        recommender = _make_recommender()
        recommender.single_user = "alice"
        recommender.plex.library.section.side_effect = plexapi.exceptions.PlexApiException("boom")

        recommender._remove_collection_for_no_history("alice")

        mock_remove.assert_not_called()
        mock_warn.assert_called_once()

    @patch("recommenders.base.remove_owned_collection")
    def test_also_passes_the_legacy_label_form_for_a_name_that_normalizes(self, mock_remove):
        """#357: for a username whose #352 normalized form actually
        differs from its pre-#352 form (case/whitespace present), both
        forms must be passed through - a collection created before #352
        and never refreshed since (recommend_for_no_history: false skips
        manage_plex_labels entirely) can only be found by its legacy
        form."""
        recommender = _make_recommender()
        recommender.single_user = "Alex Pigot"
        section = Mock()
        recommender.plex.library.section.return_value = section

        recommender._remove_collection_for_no_history("Alex Pigot")

        mock_remove.assert_called_once()
        args, _kwargs = mock_remove.call_args
        assert args[1] == ["PrivateCollection_alexpigot", "PrivateCollection_Alex_Pigot"]


class TestLoadWatchedCache:
    """Tests for BaseRecommender._load_watched_cache."""

    def _recommender_with_cache_path(self, tmp_path):
        recommender = _make_recommender()
        recommender.watched_cache_path = str(tmp_path / "watched_cache.json")
        return recommender

    @patch("recommenders.base.check_cache_version", return_value=False)
    def test_invalid_cache_version_returns_empty_without_reading_file(self, mock_valid, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        with open(recommender.watched_cache_path, "w") as f:
            f.write('{"watched_count": 3}')

        result = recommender._load_watched_cache()

        assert result == {}
        assert recommender.cached_watched_count == 0

    @patch("recommenders.base.check_cache_version", return_value=True)
    def test_loads_valid_cache_fields(self, mock_valid, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        cache_data = {
            "watched_count": 2,
            "watched_data_counters": {"genres": {"Action": 2}},
            "plex_tmdb_cache": {1: 100},
            "tmdb_keywords_cache": {100: ["x"]},
            "label_dates": {"a": "2024-01-01"},
            "watched_movie_ids": [1, 2],
        }
        import json as _json

        with open(recommender.watched_cache_path, "w") as f:
            _json.dump(cache_data, f)

        result = recommender._load_watched_cache()

        assert recommender.cached_watched_count == 2
        assert recommender.watched_ids == {1, 2}
        assert recommender.plex_tmdb_cache == {"1": 100}
        assert result["watched_count"] == 2

    @patch("recommenders.base.log_warning")
    @patch("recommenders.base.check_cache_version", return_value=True)
    def test_invalid_watched_ids_format_warns_and_clears(self, mock_valid, mock_warn, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        cache_data = {"watched_count": 0, "watched_movie_ids": "not-a-list"}
        import json as _json

        with open(recommender.watched_cache_path, "w") as f:
            _json.dump(cache_data, f)

        recommender._load_watched_cache()

        mock_warn.assert_called()
        assert recommender.watched_ids == set()

    @patch("recommenders.base.check_cache_version", return_value=True)
    def test_missing_ids_with_positive_count_triggers_refresh(self, mock_valid, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        cache_data = {"watched_count": 5, "watched_movie_ids": []}
        import json as _json

        with open(recommender.watched_cache_path, "w") as f:
            _json.dump(cache_data, f)
        recommender._refresh_watched_data = Mock()

        recommender._load_watched_cache()

        recommender._refresh_watched_data.assert_called_once()

    @patch("recommenders.base.log_warning")
    @patch("recommenders.base.check_cache_version", return_value=True)
    def test_corrupt_json_triggers_refresh(self, mock_valid, mock_warn, tmp_path):
        recommender = self._recommender_with_cache_path(tmp_path)
        with open(recommender.watched_cache_path, "w") as f:
            f.write("{not valid json")
        recommender._refresh_watched_data = Mock()

        recommender._load_watched_cache()

        mock_warn.assert_called()
        recommender._refresh_watched_data.assert_called_once()


class TestLabelDatesSurviveCacheRebuild:
    """
    label_dates must outlive a CACHE_VERSION bump.

    CACHE_VERSION invalidates DERIVED data - cached scores, metadata
    shape - and is bumped for scoring changes. label_dates is not
    derived: it records when each recommendation was first shown, and it
    is the only clock the ignored-recommendation signal has. Because
    check_cache_version deletes the whole watched cache, a scoring change
    used to reset that clock for every user. Observed directly: two
    CACHE_VERSION bumps in one week left every label across six users no
    older than 6 days, so a 60-day signal could never fire.
    """

    def test_salvages_label_dates_from_an_outdated_cache(self, tmp_path):
        recommender = _make_recommender()
        path = tmp_path / "watched.json"
        path.write_text(
            json.dumps({"cache_version": 1, "label_dates": {"1_Recommended_alice": "2026-01-01T00:00:00"}}),
            encoding="utf-8",
        )
        salvaged = recommender._salvage_label_dates(str(path))
        assert salvaged == {"1_Recommended_alice": "2026-01-01T00:00:00"}

    def test_salvage_ignores_the_version(self, tmp_path):
        """Version-blind on purpose - that is the whole point."""
        recommender = _make_recommender()
        path = tmp_path / "watched.json"
        path.write_text(
            json.dumps({"cache_version": 999999, "label_dates": {"7_R_bob": "2026-02-02T00:00:00"}}), encoding="utf-8"
        )
        assert recommender._salvage_label_dates(str(path)) == {"7_R_bob": "2026-02-02T00:00:00"}

    def test_missing_file_yields_empty(self, tmp_path):
        recommender = _make_recommender()
        assert recommender._salvage_label_dates(str(tmp_path / "nope.json")) == {}

    def test_malformed_file_yields_empty_not_a_crash(self, tmp_path):
        """A missing clock is recoverable; a crashed run is not."""
        recommender = _make_recommender()
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert recommender._salvage_label_dates(str(path)) == {}

    def test_non_string_entries_are_dropped(self, tmp_path):
        recommender = _make_recommender()
        path = tmp_path / "w.json"
        path.write_text(json.dumps({"label_dates": {"ok": "2026-01-01T00:00:00", "bad": 12345}}), encoding="utf-8")
        assert recommender._salvage_label_dates(str(path)) == {"ok": "2026-01-01T00:00:00"}

    def test_label_dates_absent_yields_empty(self, tmp_path):
        recommender = _make_recommender()
        path = tmp_path / "w.json"
        path.write_text(json.dumps({"cache_version": 9}), encoding="utf-8")
        assert recommender._salvage_label_dates(str(path)) == {}


# ---------------------------------------------------------------------------
# Franchise ordering (utils/franchise.py) - see its module docstring
# ---------------------------------------------------------------------------
ROCKY_COLLECTION_ID = 1575

ROCKY_CACHE = {
    "1": {"title": "Rocky", "year": 1976, "collection_id": ROCKY_COLLECTION_ID, "collection_name": "Rocky Collection"},
    "2": {
        "title": "Rocky II",
        "year": 1979,
        "collection_id": ROCKY_COLLECTION_ID,
        "collection_name": "Rocky Collection",
    },
    "3": {
        "title": "Rocky III",
        "year": 1982,
        "collection_id": ROCKY_COLLECTION_ID,
        "collection_name": "Rocky Collection",
    },
    "4": {
        "title": "Rocky IV",
        "year": 1985,
        "collection_id": ROCKY_COLLECTION_ID,
        "collection_name": "Rocky Collection",
    },
}


def _franchise_recommender(cache=None):
    """A recommender whose media cache holds a real multi-entry series."""
    recommender = _make_recommender()
    media_cache = Mock()
    media_cache.cache = {"movies": copy.deepcopy(ROCKY_CACHE if cache is None else cache)}
    recommender._get_media_cache = Mock(return_value=media_cache)
    return recommender


def _scored_from(recommender, rating_key, score):
    info = recommender._get_media_cache().cache["movies"][str(rating_key)]
    info["plex_rating_key"] = int(rating_key)
    info["similarity_score"] = score
    return info


class TestApplyFranchiseOrderingIntegration:
    """BaseRecommender._apply_franchise_ordering - the get_recommendations() hook."""

    def test_started_series_advances_to_the_next_entry(self):
        recommender = _franchise_recommender()
        recommender.watched_ids = {1}
        ordered = recommender._apply_franchise_ordering([_scored_from(recommender, 4, 0.8)], [])
        assert [i["title"] for i in ordered] == ["Rocky II"]

    def test_unstarted_series_drops_the_sequel_rather_than_promoting(self):
        """The measured fix: a series the user never touched must not have
        its original promoted into the sequel's slot."""
        recommender = _franchise_recommender()
        ordered = recommender._apply_franchise_ordering([_scored_from(recommender, 4, 0.8)], [])
        assert ordered == []

    def test_unstarted_first_entry_keeps_its_own_rank(self):
        recommender = _franchise_recommender()
        scored = [_scored_from(recommender, 4, 0.9), _scored_from(recommender, 1, 0.2)]
        ordered = recommender._apply_franchise_ordering(scored, [])
        assert [(i["title"], i["similarity_score"]) for i in ordered] == [("Rocky", 0.2)]

    def test_disabled_flag_leaves_recommendations_untouched(self):
        recommender = _franchise_recommender()
        recommender.franchise_order = False
        scored = [_scored_from(recommender, 4, 0.8)]
        assert recommender._apply_franchise_ordering(scored, []) is scored

    def test_the_users_own_plex_view_counts_as_watched(self):
        """user_played_ids is that user's own state, and it decides both
        how far along the series is AND whether it counts as started."""
        recommender = _franchise_recommender()
        recommender.user_played_ids = {1, 2}
        ordered = recommender._apply_franchise_ordering([_scored_from(recommender, 4, 0.8)], [])
        assert [i["title"] for i in ordered] == ["Rocky III"]

    def test_declined_entry_is_not_promoted(self):
        recommender = _franchise_recommender()
        recommender.watched_ids = {1}
        recommender.declined_rating_keys = {2}
        ordered = recommender._apply_franchise_ordering([_scored_from(recommender, 4, 0.8)], [])
        assert [i["title"] for i in ordered] == ["Rocky III"]

    def test_excluded_genre_entry_is_not_promoted(self):
        recommender = _franchise_recommender()
        recommender.watched_ids = {1}
        recommender._get_media_cache().cache["movies"]["2"]["genres"] = ["horror"]
        ordered = recommender._apply_franchise_ordering([_scored_from(recommender, 4, 0.8)], ["horror"])
        assert [i["title"] for i in ordered] == ["Rocky III"]

    def test_max_rating_preference_is_honored(self):
        recommender = _franchise_recommender()
        recommender.single_user = "kid"
        recommender.user_preferences = {"kid": {"max_rating": "PG"}}
        recommender.watched_ids = {1}
        recommender._get_media_cache().cache["movies"]["2"]["content_rating"] = "R"
        recommender._get_media_cache().cache["movies"]["3"]["content_rating"] = "PG"
        ordered = recommender._apply_franchise_ordering([_scored_from(recommender, 4, 0.8)], [])
        assert [i["title"] for i in ordered] == ["Rocky III"]

    def test_empty_input_is_a_no_op(self):
        recommender = _franchise_recommender()
        assert recommender._apply_franchise_ordering([], []) == []

    def test_library_without_collection_data_is_a_no_op(self):
        recommender = _franchise_recommender(cache={"1": {"title": "Heat", "year": 1995}})
        scored = [_scored_from(recommender, 1, 0.8)]
        assert recommender._apply_franchise_ordering(scored, []) is scored

    def test_per_user_preference_can_disable_it(self):
        recommender = _franchise_recommender()
        recommender.single_user = "sarah"
        recommender.user_preferences = {"sarah": {"franchise_order": False}}
        recommender.watched_ids = {1}
        scored = [_scored_from(recommender, 4, 0.8)]
        assert recommender._apply_franchise_ordering(scored, []) is scored

    def test_per_user_preference_can_enable_it_against_the_media_default(self):
        recommender = _franchise_recommender()
        recommender.franchise_order = False
        recommender.single_user = "sarah"
        recommender.user_preferences = {"sarah": {"franchise_order": True}}
        recommender.watched_ids = {1}
        ordered = recommender._apply_franchise_ordering([_scored_from(recommender, 4, 0.8)], [])
        assert [i["title"] for i in ordered] == ["Rocky II"]

    def test_per_user_preference_also_reaches_the_collection_side(self):
        recommender = _franchise_recommender()
        recommender.single_user = "sarah"
        recommender.user_preferences = {"sarah": {"franchise_order": False}}
        candidates = {4: (Mock(title="Rocky IV"), 0.9), 1: (Mock(title="Rocky"), 0.3)}
        assert recommender._suppress_superseded_franchise_candidates(candidates) is candidates

    def test_a_broken_cache_never_costs_the_user_their_recommendations(self):
        recommender = _franchise_recommender()
        scored = [_scored_from(recommender, 4, 0.8)]
        recommender._get_media_cache = Mock(side_effect=AttributeError("boom"))
        assert recommender._apply_franchise_ordering(scored, []) is scored

    def test_promotions_and_suppressions_are_reported_separately(self, capsys):
        recommender = _franchise_recommender()
        recommender.watched_ids = {1}
        scored = [_scored_from(recommender, 4, 0.9), _scored_from(recommender, 3, 0.8)]
        capsys.readouterr()  # drop construction chatter
        recommender._apply_franchise_ordering(scored, [])
        out = capsys.readouterr().out
        assert "series you've started moved to your next entry" in out
        assert "collapsed 1 duplicate entries" in out

    def test_suppressions_are_reported_with_the_series_count(self, capsys):
        recommender = _franchise_recommender()
        scored = [_scored_from(recommender, 4, 0.9), _scored_from(recommender, 2, 0.8)]
        capsys.readouterr()  # drop construction chatter
        recommender._apply_franchise_ordering(scored, [])
        out = capsys.readouterr().out
        assert "held back 2 mid-series movies across 1 series you haven't started" in out


class TestSuppressSupersededFranchiseCandidates:
    """BaseRecommender._suppress_superseded_franchise_candidates - the
    manage_plex_labels() hook that stops a previously-labeled sequel
    outliving the promotion."""

    def _candidates(self, *pairs):
        return {rating_key: (Mock(title=f"item-{rating_key}"), score) for rating_key, score in pairs}

    def test_labeled_sequel_is_dropped_when_the_first_entry_is_a_candidate(self):
        recommender = _franchise_recommender()
        result = recommender._suppress_superseded_franchise_candidates(self._candidates((4, 0.9), (1, 0.3)))
        assert set(result) == {1}

    def test_started_series_survivor_keeps_the_best_score(self):
        """On a series being worked through, franchise ordering changes
        WHICH entry is recommended, not how highly the series ranks."""
        recommender = _franchise_recommender()
        recommender.watched_ids = {1}
        result = recommender._suppress_superseded_franchise_candidates(self._candidates((4, 0.9), (2, 0.3)))
        assert set(result) == {2}
        assert result[2][1] == 0.9

    def test_unstarted_survivor_keeps_its_own_score(self):
        """Transferring here would reintroduce, via a stale label, exactly
        the inheritance the started/unstarted split removed."""
        recommender = _franchise_recommender()
        result = recommender._suppress_superseded_franchise_candidates(self._candidates((4, 0.9), (1, 0.3)))
        assert result[1][1] == 0.3

    def test_started_series_survives_when_its_next_entry_is_not_a_candidate(self):
        """Otherwise the series vanishes from the collection rather than
        advancing - e.g. when max_rating just removed that next entry."""
        recommender = _franchise_recommender()
        recommender.watched_ids = {1}
        candidates = self._candidates((4, 0.9))
        assert recommender._suppress_superseded_franchise_candidates(candidates) == candidates

    def test_unstarted_series_is_dropped_even_with_no_replacement(self):
        """The fresh pool already refuses to offer this; a label left over
        from a previous run must not keep it alive."""
        recommender = _franchise_recommender()
        assert recommender._suppress_superseded_franchise_candidates(self._candidates((4, 0.9))) == {}

    def test_unrelated_candidates_are_untouched(self):
        recommender = _franchise_recommender()
        candidates = self._candidates((4, 0.9), (1, 0.3), (99, 0.5))
        result = recommender._suppress_superseded_franchise_candidates(candidates)
        assert set(result) == {1, 99}

    def test_disabled_flag_is_a_no_op(self):
        recommender = _franchise_recommender()
        recommender.franchise_order = False
        candidates = self._candidates((4, 0.9), (1, 0.3))
        assert recommender._suppress_superseded_franchise_candidates(candidates) is candidates

    def test_empty_pool(self):
        recommender = _franchise_recommender()
        assert recommender._suppress_superseded_franchise_candidates({}) == {}

    def test_a_broken_cache_never_costs_the_user_their_collection(self):
        recommender = _franchise_recommender()
        candidates = self._candidates((4, 0.9), (1, 0.3))
        recommender._get_media_cache = Mock(side_effect=AttributeError("boom"))
        assert recommender._suppress_superseded_franchise_candidates(candidates) is candidates

    def test_library_without_collection_data_is_a_no_op(self):
        recommender = _franchise_recommender(cache={"1": {"title": "Heat", "year": 1995}})
        candidates = self._candidates((1, 0.3))
        assert recommender._suppress_superseded_franchise_candidates(candidates) is candidates

    def test_fully_watched_series_suppresses_nothing(self):
        """find_next_unwatched() returning None must leave the pool
        alone, not empty the series out of it."""
        recommender = _franchise_recommender()
        recommender.watched_ids = {1, 2, 4}
        candidates = self._candidates((4, 0.9), (1, 0.3))
        assert recommender._suppress_superseded_franchise_candidates(candidates) == candidates

    def test_suppression_is_reported_with_explicit_truncation(self, capsys):
        cache = {
            str(i): {
                "title": f"Part {i}",
                "year": 1970 + i,
                "collection_id": ROCKY_COLLECTION_ID,
                "collection_name": "Rocky Collection",
            }
            for i in range(1, 10)
        }
        recommender = _franchise_recommender(cache=cache)
        candidates = self._candidates(*[(i, 0.5) for i in range(1, 10)])
        capsys.readouterr()  # drop construction chatter
        result = recommender._suppress_superseded_franchise_candidates(candidates)
        out = capsys.readouterr().out
        assert set(result) == {1}
        assert "holding back 8 later movies" in out
        assert "... and 3 more" in out


class TestReportFranchiseGaps:
    """BaseRecommender._report_franchise_gaps - "you got Rocky II because
    you don't own Rocky"."""

    HUNTARR = {
        "collection_details": {
            str(ROCKY_COLLECTION_ID): {
                "collection_id": ROCKY_COLLECTION_ID,
                "collection_name": "Rocky Collection",
                "movies": [
                    {"tmdb_id": 1366, "title": "Rocky", "year": "1976"},
                    {"tmdb_id": 1367, "title": "Rocky II", "year": "1979"},
                ],
            }
        }
    }

    def _recommender_with_huntarr_cache(self, tmp_path, payload=None):
        recommender = _franchise_recommender()
        recommender.cache_dir = str(tmp_path)
        if payload is not None:
            (tmp_path / "huntarr_cache.json").write_text(json.dumps(payload), encoding="utf-8")
        return recommender

    def test_missing_earlier_entry_is_reported(self, tmp_path, capsys):
        recommender = self._recommender_with_huntarr_cache(tmp_path, self.HUNTARR)
        recs = [{"title": "Rocky II", "year": 1979, "collection_id": ROCKY_COLLECTION_ID}]
        recommender._report_franchise_gaps(recs, {"2": {"tmdb_id": 1367}})
        out = capsys.readouterr().out
        assert "Franchise gaps" in out
        assert "Rocky (1976)" in out

    def test_owned_earlier_entry_is_not_reported(self, tmp_path, capsys):
        recommender = self._recommender_with_huntarr_cache(tmp_path, self.HUNTARR)
        recs = [{"title": "Rocky II", "year": 1979, "collection_id": ROCKY_COLLECTION_ID}]
        recommender._report_franchise_gaps(recs, {"1": {"tmdb_id": 1366}, "2": {"tmdb_id": 1367}})
        assert "Franchise gaps" not in capsys.readouterr().out

    def test_silent_without_a_huntarr_cache(self, tmp_path, capsys):
        recommender = self._recommender_with_huntarr_cache(tmp_path)
        recs = [{"title": "Rocky II", "year": 1979, "collection_id": ROCKY_COLLECTION_ID}]
        capsys.readouterr()  # drop construction chatter
        recommender._report_franchise_gaps(recs, {})
        assert capsys.readouterr().out == ""

    def test_disabled_flag_is_silent(self, tmp_path, capsys):
        recommender = self._recommender_with_huntarr_cache(tmp_path, self.HUNTARR)
        recommender.franchise_order = False
        recs = [{"title": "Rocky II", "year": 1979, "collection_id": ROCKY_COLLECTION_ID}]
        capsys.readouterr()  # drop construction chatter
        recommender._report_franchise_gaps(recs, {})
        assert capsys.readouterr().out == ""

    def test_no_recommendations_is_silent(self, tmp_path, capsys):
        recommender = self._recommender_with_huntarr_cache(tmp_path, self.HUNTARR)
        capsys.readouterr()  # drop construction chatter
        recommender._report_franchise_gaps([], {})
        assert capsys.readouterr().out == ""

    def test_a_broken_cache_is_never_fatal(self, tmp_path, capsys):
        recommender = self._recommender_with_huntarr_cache(tmp_path, self.HUNTARR)
        recs = [{"title": "Rocky II", "year": 1979, "collection_id": ROCKY_COLLECTION_ID}]
        capsys.readouterr()  # drop construction chatter
        with patch("recommenders.base.load_collection_details", side_effect=OSError("boom")):
            recommender._report_franchise_gaps(recs, {})
        assert capsys.readouterr().out == ""

    def test_standalone_and_unknown_collections_are_skipped(self, tmp_path, capsys):
        recommender = self._recommender_with_huntarr_cache(tmp_path, self.HUNTARR)
        recs = [
            {"title": "Heat", "year": 1995, "collection_id": None},
            {"title": "Some Sequel", "year": 2000, "collection_id": 999999},
        ]
        capsys.readouterr()  # drop construction chatter
        recommender._report_franchise_gaps(recs, {})
        assert capsys.readouterr().out == ""

    def test_each_series_is_reported_once(self, tmp_path, capsys):
        """Three Rocky candidates are one gap report, not three."""
        recommender = self._recommender_with_huntarr_cache(tmp_path, self.HUNTARR)
        recs = [{"title": "Rocky II", "year": 1979, "collection_id": ROCKY_COLLECTION_ID}] * 3
        capsys.readouterr()  # drop construction chatter
        recommender._report_franchise_gaps(recs, {})
        assert capsys.readouterr().out.count("earliest owned entry") == 1

    def test_long_gap_lists_are_truncated_explicitly(self, tmp_path, capsys):
        payload = {
            "collection_details": {
                str(cid): {
                    "collection_id": cid,
                    "collection_name": f"Series {cid}",
                    "movies": [
                        {"tmdb_id": cid * 100 + n, "title": f"Series {cid} Part {n}", "year": str(1960 + n)}
                        for n in range(5)
                    ],
                }
                for cid in range(1, 8)
            }
        }
        recommender = self._recommender_with_huntarr_cache(tmp_path, payload)
        recs = [{"title": f"Series {cid} Part 5", "year": 1990, "collection_id": cid} for cid in range(1, 8)]
        capsys.readouterr()  # drop construction chatter
        recommender._report_franchise_gaps(recs, {})
        out = capsys.readouterr().out
        assert "across 7 series" in out
        assert "+2 more" in out  # per-series gap list
        assert "... and 2 more series" in out  # series list itself


class TestDeclinedRatingKeys:
    """The declined set franchise ordering refuses to promote into."""

    def test_starts_empty(self):
        assert _make_recommender().declined_rating_keys == set()

    def test_populated_from_ignored_recommendations(self):
        recommender = _make_recommender()
        recommender.label_dates = {"7_Recommended": "2020-01-01T00:00:00"}
        recommender.watched_data_counters = {"genres": Counter()}
        media_cache = Mock()
        media_cache.cache = {"movies": {"7": {"title": "Rocky", "genres": ["drama"], "tmdb_keywords": []}}}
        recommender._get_media_cache = Mock(return_value=media_cache)
        recommender._collection_label_name = Mock(return_value="Recommended")

        recommender._apply_ignored_recommendation_feedback()

        assert recommender.declined_rating_keys == {7}

    def test_stays_empty_when_negative_signals_are_off(self):
        recommender = _make_recommender()
        recommender.config["negative_signals"] = {"enabled": False}
        recommender.label_dates = {"7_Recommended": "2020-01-01T00:00:00"}
        recommender._apply_ignored_recommendation_feedback()
        assert recommender.declined_rating_keys == set()
