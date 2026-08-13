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

"""Tests for utils/private_label_cache.py - the persisted registry of
PrivateCollection_* label owners (#351).

Every test here uses tmp_path - never the real cache/ directory.
"""

from unittest.mock import Mock, patch

import plexapi.exceptions

from utils.private_label_cache import (
    find_orphaned_owners,
    load_private_label_owners,
    prune_orphaned_private_collections,
    save_private_label_owners,
)


class TestLoadSavePrivateLabelOwners:
    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_private_label_owners(str(tmp_path)) == {}

    def test_save_then_load_roundtrip(self, tmp_path):
        owners = {
            "1": {
                "username": "alice",
                "labels": {"movie": ["PrivateCollection_alice"], "tv": ["PrivateCollection_alice"]},
            },
            "2": {"username": "bob", "labels": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]}},
        }
        save_private_label_owners(str(tmp_path), owners)

        assert load_private_label_owners(str(tmp_path)) == owners

    def test_load_corrupt_json_returns_empty(self, tmp_path):
        (tmp_path / "private_label_owners.json").write_text("not valid json", encoding="utf-8")

        assert load_private_label_owners(str(tmp_path)) == {}

    def test_load_non_dict_json_returns_empty(self, tmp_path):
        (tmp_path / "private_label_owners.json").write_text("[1, 2, 3]", encoding="utf-8")

        assert load_private_label_owners(str(tmp_path)) == {}

    def test_malformed_entries_are_dropped_not_fatal(self, tmp_path):
        """One bad entry must not discard every good entry in the file."""
        (tmp_path / "private_label_owners.json").write_text(
            '{"1": {"username": "alice", "labels": {"movie": ["PC_alice"], "tv": ["PC_alice"]}}, '
            '"2": "not a dict", '
            '"3": {"username": "", "labels": {}}, '
            '"4": {"labels": {"movie": ["PC_dave"], "tv": []}}}',
            encoding="utf-8",
        )

        result = load_private_label_owners(str(tmp_path))

        assert list(result.keys()) == ["1"]
        assert result["1"]["username"] == "alice"

    def test_missing_media_type_defaults_to_empty_list(self, tmp_path):
        """labels missing a movie/tv key (e.g. a single-media-type
        install's owner) must not raise a KeyError downstream."""
        (tmp_path / "private_label_owners.json").write_text(
            '{"1": {"username": "alice", "labels": {"movie": ["PC_alice"]}}}', encoding="utf-8"
        )

        result = load_private_label_owners(str(tmp_path))

        assert result["1"]["labels"] == {"movie": ["PC_alice"], "tv": []}

    def test_save_creates_cache_dir(self, tmp_path):
        cache_dir = tmp_path / "nested" / "cache"

        save_private_label_owners(str(cache_dir), {"1": {"username": "alice", "labels": {"movie": [], "tv": []}}})

        assert (cache_dir / "private_label_owners.json").exists()

    def test_save_failure_does_not_raise(self, tmp_path, monkeypatch):
        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("utils.private_label_cache.os.makedirs", _boom)

        # Must not raise - a cache that can't be written is a
        # lost-memory problem for the next run, never this one's.
        save_private_label_owners(str(tmp_path / "unwritable"), {"1": {"username": "a", "labels": {}}})


class TestFindOrphanedOwners:
    def test_no_persisted_owners_returns_empty(self):
        assert find_orphaned_owners({}, ["1", "2"]) == {}

    def test_all_persisted_still_current_returns_empty(self):
        persisted = {"1": {"username": "alice", "labels": {}}}
        assert find_orphaned_owners(persisted, ["1", "2"]) == {}

    def test_departed_owner_is_returned(self):
        persisted = {
            "1": {"username": "alice", "labels": {}},
            "2": {"username": "bob", "labels": {}},
        }
        result = find_orphaned_owners(persisted, ["1"])

        assert list(result.keys()) == ["2"]
        assert result["2"]["username"] == "bob"

    def test_empty_current_owner_ids_orphans_everyone_persisted(self):
        persisted = {"1": {"username": "alice", "labels": {}}}
        assert find_orphaned_owners(persisted, []) == persisted


class TestPruneOrphanedPrivateCollections:
    def _config(self):
        return {"plex": {"token": "t"}, "plex_users": {}, "libraries": None}

    def test_no_orphans_returns_empty_without_connecting(self):
        with patch("utils.plex.init_plex") as mock_init:
            result = prune_orphaned_private_collections(self._config(), {})

        assert result == []
        mock_init.assert_not_called()

    def test_connect_failure_returns_empty_list(self):
        orphaned = {"2": {"username": "bob", "labels": {"movie": ["PrivateCollection_bob"], "tv": []}}}

        with patch("utils.plex.init_plex", side_effect=Exception("connection refused")):
            result = prune_orphaned_private_collections(self._config(), orphaned)

        assert result == []

    def test_deletes_matching_collection_and_reports_username(self):
        orphaned = {
            "2": {"username": "bob", "labels": {"movie": ["PrivateCollection_bob"], "tv": ["PrivateCollection_bob"]}}
        }

        mock_collection = Mock()
        mock_collection.title = "Bob's Collection"
        mock_collection.labels = [Mock(tag="PrivateCollection_bob")]
        mock_section = Mock()
        mock_section.collections.return_value = [mock_collection]
        mock_plex = Mock()
        mock_plex.library.section.return_value = mock_section

        with patch("utils.plex.init_plex", return_value=mock_plex):
            result = prune_orphaned_private_collections(self._config(), orphaned)

        assert mock_collection.delete.called
        assert result == ["bob"]

    def test_never_touches_a_collection_without_the_label(self):
        """A collection that doesn't carry the departed owner's label is
        never a candidate, no matter what else lives in that library."""
        orphaned = {"2": {"username": "bob", "labels": {"movie": ["PrivateCollection_bob"], "tv": []}}}

        unrelated_collection = Mock()
        unrelated_collection.labels = [Mock(tag="SomeoneElsesLabel")]
        mock_section = Mock()
        mock_section.collections.return_value = [unrelated_collection]
        mock_plex = Mock()
        mock_plex.library.section.return_value = mock_section

        with patch("utils.plex.init_plex", return_value=mock_plex):
            prune_orphaned_private_collections(self._config(), orphaned)

        unrelated_collection.delete.assert_not_called()

    def test_unavailable_library_is_skipped_not_fatal(self):
        """A library that doesn't resolve (e.g. renamed/removed section)
        must not stop pruning for other libraries/owners."""
        orphaned = {"2": {"username": "bob", "labels": {"movie": ["PrivateCollection_bob"], "tv": []}}}

        mock_plex = Mock()
        mock_plex.library.section.side_effect = plexapi.exceptions.NotFound("no such library")

        with patch("utils.plex.init_plex", return_value=mock_plex):
            result = prune_orphaned_private_collections(self._config(), orphaned)

        # Still reports the owner as attempted - nothing left to keep
        # retrying for indefinitely once every library has been tried.
        assert result == ["bob"]
