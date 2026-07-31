"""
Tests for utils/corpus_idf.py - corpus-level inverse document frequency.
"""

import pytest

from utils.config import IDF_MIN_CORPUS_SIZE, IDF_MIN_WEIGHT
from utils.corpus_idf import (
    build_corpus_idf,
    build_document_frequency,
    describe_least_informative,
    idf_weight,
)


def _corpus(size, common_term="sequel", rare_term="nasa"):
    """A corpus where `common_term` is everywhere and `rare_term` is in one item."""
    items = [{"tmdb_keywords": [common_term]} for _ in range(size)]
    items[0]["tmdb_keywords"] = [common_term, rare_term]
    return items


class TestBuildDocumentFrequency:
    def test_counts_items_containing_each_term(self):
        items = [{"genres": ["Action", "Drama"]}, {"genres": ["Action"]}]
        assert build_document_frequency(items, "genres") == {"action": 2, "drama": 1}

    def test_lowercases_terms(self):
        items = [{"genres": ["ACTION"]}, {"genres": ["action"]}]
        assert build_document_frequency(items, "genres") == {"action": 2}

    def test_duplicate_term_in_one_item_counts_once(self):
        """Document frequency, not term frequency."""
        items = [{"genres": ["action", "Action", "ACTION"]}]
        assert build_document_frequency(items, "genres") == {"action": 1}

    def test_missing_field_is_skipped(self):
        items = [{"genres": ["action"]}, {}, {"genres": None}]
        assert build_document_frequency(items, "genres") == {"action": 1}

    def test_non_string_terms_ignored(self):
        items = [{"genres": ["action", None, 7]}]
        assert build_document_frequency(items, "genres") == {"action": 1}


class TestBuildCorpusIdf:
    def test_ubiquitous_term_scores_lower_than_rare_term(self):
        """The whole point: a term in every item carries no information."""
        weights = build_corpus_idf(_corpus(100), "tmdb_keywords")
        assert weights["sequel"] < weights["nasa"]

    def test_weights_are_bounded(self):
        weights = build_corpus_idf(_corpus(100), "tmdb_keywords")
        assert all(IDF_MIN_WEIGHT <= w <= 1.0 for w in weights.values())

    def test_ubiquitous_term_hits_the_floor_not_zero(self):
        """
        Zeroing a term outright would silently erase the dimension for an
        item whose metadata is entirely common terms - degrade, don't
        drop (CLAUDE.md).
        """
        weights = build_corpus_idf(_corpus(200), "tmdb_keywords")
        assert weights["sequel"] == pytest.approx(IDF_MIN_WEIGHT)

    def test_small_corpus_returns_empty(self):
        """Below the minimum, document frequency is too noisy to trust."""
        assert build_corpus_idf(_corpus(IDF_MIN_CORPUS_SIZE - 1), "tmdb_keywords") == {}

    def test_at_minimum_corpus_size_produces_weights(self):
        assert build_corpus_idf(_corpus(IDF_MIN_CORPUS_SIZE), "tmdb_keywords") != {}

    def test_empty_corpus_returns_empty(self):
        assert build_corpus_idf([], "tmdb_keywords") == {}

    def test_corpus_with_no_terms_returns_empty(self):
        assert build_corpus_idf([{} for _ in range(50)], "tmdb_keywords") == {}

    def test_ordering_matches_real_library_shape(self):
        """
        Reproduces the observed defect: 'sequel' (28% of the library)
        must be discounted well below 'survival' (2%), despite carrying
        MORE weight in the user's profile.
        """
        items = []
        for i in range(100):
            kws = []
            if i < 28:
                kws.append("sequel")
            if i < 14:
                kws.append("aftercreditsstinger")
            if i < 2:
                kws.append("survival")
            items.append({"tmdb_keywords": kws or ["filler"]})
        weights = build_corpus_idf(items, "tmdb_keywords")
        assert weights["survival"] > weights["aftercreditsstinger"] > weights["sequel"]


class TestIdfWeight:
    def test_no_corpus_is_neutral(self):
        """This is what keeps pre-existing callers bit-for-bit unchanged."""
        assert idf_weight("anything", None) == 1.0
        assert idf_weight("anything", {}) == 1.0

    def test_looks_up_case_insensitively(self):
        assert idf_weight("Sequel", {"sequel": 0.2}) == 0.2

    def test_unknown_term_is_treated_as_maximally_distinctive(self):
        """
        A term the library holds nothing else for is MORE distinctive
        than any term in it, so it takes 1.0 rather than the floor.
        """
        assert idf_weight("obscure", {"sequel": 0.2}) == 1.0


class TestDescribeLeastInformative:
    def test_returns_lowest_weighted_first(self):
        weights = {"sequel": 0.05, "survival": 0.9, "stinger": 0.2}
        assert [t for t, _ in describe_least_informative(weights, top_n=2)] == ["sequel", "stinger"]

    def test_respects_top_n(self):
        assert len(describe_least_informative({"a": 0.1, "b": 0.2, "c": 0.3}, top_n=2)) == 2


class TestScoringIntegration:
    """The IDF must actually change a similarity score, not just exist."""

    def test_ubiquitous_keyword_match_scores_below_rare_one(self):
        from utils.scoring import calculate_similarity_score

        corpus = []
        for i in range(100):
            kws = ["sequel"] if i < 90 else []
            if i < 3:
                kws.append("nasa")
            corpus.append({"tmdb_keywords": kws or ["filler"]})
        keyword_idf = build_corpus_idf(corpus, "tmdb_keywords")

        profile = {"genres": {}, "actors": {}, "directors": {}, "keywords": {"sequel": 10, "nasa": 10}}
        weights = {"genre": 0.0, "director": 0.0, "actor": 0.0, "keyword": 1.0}

        # NB: the corpus is built from the cache's storage field name
        # ("tmdb_keywords"), but scoring reads content_info["keywords"] -
        # the recommenders translate between the two. The IDF map is
        # keyed on the keyword strings themselves, so it spans both.
        common, _ = calculate_similarity_score(
            {"keywords": ["sequel"]}, dict(profile), "movie", weights, keyword_idf=keyword_idf
        )
        rare, _ = calculate_similarity_score(
            {"keywords": ["nasa"]}, dict(profile), "movie", weights, keyword_idf=keyword_idf
        )
        assert rare > common, "corpus IDF did not discount the ubiquitous keyword"

    def test_absent_corpus_leaves_scoring_unchanged(self):
        """Backwards compatibility for every existing caller."""
        from utils.scoring import calculate_similarity_score

        profile = {"genres": {}, "actors": {}, "directors": {}, "keywords": {"sequel": 10}}
        weights = {"genre": 0.0, "director": 0.0, "actor": 0.0, "keyword": 1.0}
        content = {"keywords": ["sequel"]}

        without, _ = calculate_similarity_score(content, dict(profile), "movie", weights)
        explicit_none, _ = calculate_similarity_score(
            content, dict(profile), "movie", weights, keyword_idf=None, genre_idf=None
        )
        assert without == explicit_none
