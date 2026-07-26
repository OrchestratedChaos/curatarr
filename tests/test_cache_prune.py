"""Tests for utils/cache_prune.py - orphaned per-user cache file pruning
(#233 audit remediation batch D / PR1(b)).

Every test here uses tmp_path - never the real cache/ directory.
"""

from utils.cache_prune import find_orphaned_cache_files, prune_orphaned_cache_files


class TestFindOrphanedCacheFiles:
    def test_no_files_returns_empty(self, tmp_path):
        assert find_orphaned_cache_files(str(tmp_path), ["alice", "bob"]) == []

    def test_missing_dir_returns_empty_not_raises(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        assert find_orphaned_cache_files(str(missing), ["alice"]) == []

    def test_configured_user_cache_is_not_orphaned(self, tmp_path):
        (tmp_path / "watched_cache_plex_alice.json").write_text("{}", encoding="utf-8")
        (tmp_path / "tv_watched_cache_plex_alice.json").write_text("{}", encoding="utf-8")
        (tmp_path / "external_recs_alice_movies.json").write_text("{}", encoding="utf-8")
        (tmp_path / "external_recs_alice_shows.json").write_text("{}", encoding="utf-8")

        result = find_orphaned_cache_files(str(tmp_path), ["alice", "bob"])

        assert result == []

    def test_removed_user_cache_is_orphaned(self, tmp_path):
        (tmp_path / "watched_cache_plex_charlie.json").write_text("{}", encoding="utf-8")

        result = find_orphaned_cache_files(str(tmp_path), ["alice", "bob"])

        assert len(result) == 1
        assert result[0].username == "charlie"
        assert result[0].filename == "watched_cache_plex_charlie.json"
        assert result[0].path == str(tmp_path / "watched_cache_plex_charlie.json")

    def test_external_recs_for_removed_user_is_orphaned(self, tmp_path):
        (tmp_path / "external_recs_departed_movies.json").write_text("{}", encoding="utf-8")
        (tmp_path / "external_recs_departed_shows.json").write_text("{}", encoding="utf-8")

        result = find_orphaned_cache_files(str(tmp_path), ["alice"])

        filenames = {o.filename for o in result}
        assert filenames == {"external_recs_departed_movies.json", "external_recs_departed_shows.json"}

    def test_empty_username_cache_is_orphaned(self, tmp_path):
        """Real example from a live install: watched_cache_plex_.json -
        an empty username from an old bug. Must be caught, not silently
        skipped just because the captured group would be empty."""
        (tmp_path / "watched_cache_plex_.json").write_text("{}", encoding="utf-8")
        (tmp_path / "tv_watched_cache_plex_.json").write_text("{}", encoding="utf-8")

        result = find_orphaned_cache_files(str(tmp_path), ["alice", "bob"])

        usernames = {o.username for o in result}
        assert usernames == {""}
        assert len(result) == 2

    def test_joined_multi_user_legacy_file_is_orphaned(self, tmp_path):
        """Real example from a live install: a joined-multi-user
        watched_cache_plex_<many-usernames>.json left over from before
        every run became strictly single-user (see
        recommenders/base.py's _get_user_context). The current code
        never writes or reads this shape, so even if every individual
        name in the blob is still configured, the file as a whole isn't
        a valid single-user cache filename and must be flagged."""
        (tmp_path / "watched_cache_plex_alice_bob_charlie.json").write_text("{}", encoding="utf-8")

        result = find_orphaned_cache_files(str(tmp_path), ["alice", "bob", "charlie"])

        assert len(result) == 1
        assert result[0].username == "alice_bob_charlie"

    def test_non_matching_files_are_never_flagged(self, tmp_path):
        """Whole-library caches, huntarr caches, and anything else not
        matching one of the four known per-user patterns must never be
        considered, let alone flagged - this module has no positive
        evidence any of these are per-user."""
        for name in (
            "all_movies_cache.json",
            "all_shows_cache.json",
            "horizon_huntarr_cache.json",
            "sequel_huntarr_cache.json",
            "user_id_map.json",
            "some_unrelated_file.txt",
        ):
            (tmp_path / name).write_text("{}", encoding="utf-8")

        result = find_orphaned_cache_files(str(tmp_path), ["alice"])

        assert result == []

    def test_mixed_valid_and_orphaned(self, tmp_path):
        (tmp_path / "watched_cache_plex_alice.json").write_text("{}", encoding="utf-8")
        (tmp_path / "watched_cache_plex_charlie.json").write_text("{}", encoding="utf-8")
        (tmp_path / "all_movies_cache.json").write_text("{}", encoding="utf-8")

        result = find_orphaned_cache_files(str(tmp_path), ["alice"])

        assert len(result) == 1
        assert result[0].username == "charlie"

    def test_results_sorted_by_filename(self, tmp_path):
        (tmp_path / "watched_cache_plex_zeta.json").write_text("{}", encoding="utf-8")
        (tmp_path / "watched_cache_plex_alpha.json").write_text("{}", encoding="utf-8")

        result = find_orphaned_cache_files(str(tmp_path), [])

        assert [o.filename for o in result] == ["watched_cache_plex_alpha.json", "watched_cache_plex_zeta.json"]


class TestPruneOrphanedCacheFiles:
    def test_dry_run_removes_nothing(self, tmp_path):
        target = tmp_path / "watched_cache_plex_charlie.json"
        target.write_text("{}", encoding="utf-8")

        removed = prune_orphaned_cache_files(str(tmp_path), ["alice"], dry_run=True)

        assert removed == []
        assert target.exists()

    def test_dry_run_is_the_default(self, tmp_path):
        target = tmp_path / "watched_cache_plex_charlie.json"
        target.write_text("{}", encoding="utf-8")

        removed = prune_orphaned_cache_files(str(tmp_path), ["alice"])

        assert removed == []
        assert target.exists()

    def test_non_dry_run_removes_orphans(self, tmp_path):
        target = tmp_path / "watched_cache_plex_charlie.json"
        target.write_text("{}", encoding="utf-8")
        kept = tmp_path / "watched_cache_plex_alice.json"
        kept.write_text("{}", encoding="utf-8")

        removed = prune_orphaned_cache_files(str(tmp_path), ["alice"], dry_run=False)

        assert removed == [str(target)]
        assert not target.exists()
        assert kept.exists()

    def test_non_dry_run_survives_removal_failure(self, tmp_path, monkeypatch):
        """A single file's removal failure must not stop the rest or
        raise out of the function."""
        target = tmp_path / "watched_cache_plex_charlie.json"
        target.write_text("{}", encoding="utf-8")
        other = tmp_path / "watched_cache_plex_dave.json"
        other.write_text("{}", encoding="utf-8")

        real_remove = __import__("os").remove

        def _flaky_remove(path):
            if "charlie" in path:
                raise OSError("permission denied")
            real_remove(path)

        monkeypatch.setattr("utils.cache_prune.os.remove", _flaky_remove)

        removed = prune_orphaned_cache_files(str(tmp_path), ["alice"], dry_run=False)

        assert removed == [str(other)]
        assert target.exists()  # the flaky one was left alone, not partially removed
        assert not other.exists()

    def test_never_touches_non_matching_files(self, tmp_path):
        keep = tmp_path / "all_movies_cache.json"
        keep.write_text("{}", encoding="utf-8")

        removed = prune_orphaned_cache_files(str(tmp_path), [], dry_run=False)

        assert removed == []
        assert keep.exists()
