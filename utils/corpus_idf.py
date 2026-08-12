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
Corpus-level inverse document frequency for scoring terms.

Fills in the missing half of what utils/scoring.py calls "TF-IDF".

The existing rarity penalties there are computed entirely against the
USER's profile: a genre or keyword that is rare *for this user* is
penalized. That is the TF half. Nothing ever asked the complementary
question - how common is this term across the whole library? - which is
what the IDF in TF-IDF actually means (inverse *document* frequency).

The consequence is that structural, non-discriminative metadata reads as
strong taste signal. On the reference library:

    keyword                 profile weight   share of library
    aftercreditsstinger              14.48                14%
    based on novel or book           12.23                15%
    sequel                           11.88                28%
    survival                         11.04                 2%
    nasa                              7.66                 1%

`sequel` outranks `survival` in that profile - but a quarter of the
library is sequels, so matching on it says almost nothing about whether
a user will like an item, while `nasa` at 1% is highly informative. The
observable effect was a recommendation list that was 44% sequels against
a 28% library baseline, because franchise entries kept collecting credit
for shared packaging rather than shared substance.

IDF weighting corrects that by scaling each matched term's contribution
by how much information the match actually carries. Terms present in
almost every item approach zero weight; genuinely distinctive terms keep
theirs.

Scores are computed once per run from the media cache and passed into
calculate_similarity_score(); when no corpus is supplied, scoring behaves
exactly as it did before.
"""

import math
from typing import Dict, Iterable, Mapping, Optional

from utils.config import IDF_MIN_CORPUS_SIZE, IDF_MIN_WEIGHT


def build_document_frequency(
    items: Iterable[Mapping],
    field: str,
) -> Dict[str, int]:
    """
    Count how many items in the corpus carry each term of `field`.

    Terms are lowercased and de-duplicated per item, so an item listing
    the same keyword twice still counts once - this is *document*
    frequency, not term frequency.

    Args:
        items: The corpus (media cache values).
        field: The per-item list field to index ("genres", "tmdb_keywords").

    Returns:
        term -> number of items containing it.
    """
    frequency: Dict[str, int] = {}
    for item in items:
        terms = item.get(field) or []
        seen = {t.lower() for t in terms if isinstance(t, str)}
        for term in seen:
            frequency[term] = frequency.get(term, 0) + 1
    return frequency


def build_corpus_idf(items: Iterable[Mapping], field: str) -> Dict[str, float]:
    """
    Build normalized IDF weights in [IDF_MIN_WEIGHT, 1.0] for `field`.

    Uses the standard smoothed form idf(t) = log(N / (1 + df(t))),
    rescaled by log(N) so the result is a bounded multiplier rather than
    an unbounded score - callers multiply an existing normalized
    contribution by it, so it has to stay on a predictable scale.

    A floor of IDF_MIN_WEIGHT applies rather than 0: a term appearing in
    every single item is uninformative, but zeroing it outright would
    silently delete a whole dimension for items whose only metadata is
    common terms, and this codebase's rule is to degrade rather than
    silently drop (see CLAUDE.md).

    Returns an empty dict for a corpus too small to say anything
    meaningful about term distribution, which callers treat as "no IDF
    weighting" rather than as all-terms-equally-rare.
    """
    corpus = list(items)
    total = len(corpus)
    if total < IDF_MIN_CORPUS_SIZE:
        return {}

    frequency = build_document_frequency(corpus, field)
    if not frequency:
        return {}

    scale = math.log(total)
    if scale <= 0:
        return {}

    weights: Dict[str, float] = {}
    for term, df in frequency.items():
        raw = math.log(total / (1 + df))
        weights[term] = max(IDF_MIN_WEIGHT, min(1.0, raw / scale))
    return weights


def idf_weight(term: str, corpus_idf: Optional[Mapping[str, float]]) -> float:
    """
    Look up a term's IDF multiplier.

    Returns 1.0 (no adjustment) when no corpus is available, which is
    what keeps scoring bit-for-bit unchanged for callers that do not
    supply one.

    A term absent from the corpus is *more* distinctive than any term in
    it, not less - an unknown keyword means the library holds nothing
    else carrying it - so it takes the full 1.0 rather than the floor.
    """
    if not corpus_idf:
        return 1.0
    return corpus_idf.get(term.lower() if isinstance(term, str) else term, 1.0)


def describe_least_informative(corpus_idf: Mapping[str, float], top_n: int = 5):
    """
    Return the `top_n` lowest-weighted (most ubiquitous, least useful)
    terms as (term, weight), for logging what IDF is discounting.
    """
    return sorted(corpus_idf.items(), key=lambda kv: kv[1])[:top_n]
