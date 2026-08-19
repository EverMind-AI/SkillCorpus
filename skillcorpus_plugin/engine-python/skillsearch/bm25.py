"""Dependency-free BM25 keyword retrieval over small in-memory corpora.

A self-contained Okapi BM25 (no ``rank_bm25`` / ``jieba`` / ``nltk``) plus a
CJK-aware tokenizer, shared by anything that needs cheap keyword ranking over
a few hundred short documents — file-based skills, tool catalogs, etc.

Latin runs tokenize on word boundaries. A CJK run is cut into overlapping
two-character tokens rather than single ideographs, because single
ideographs do not carry enough meaning to rank on: measured over 46 skills,
"做个 PPT 讲下季度进展" ranked a stock-research skill first, since 季 and 度
each matched separately in any long document that happened to contain
either. Bigrams put the slide-deck skill first, which is the answer.

A one-character run has no bigram, so it falls back to itself. That is the
only place a single ideograph is still a token.
"""

from __future__ import annotations

import math
import re

# Latin runs of two or more, or a maximal run of CJK ideographs — the run,
# not one character, because the run is what gets cut into bigrams below.
# ``re`` precompile is module-level to dodge per-call regex setup.
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}|[一-鿿]+")
_CJK_RE = re.compile(r"[一-鿿]")

# A term in more than this share of documents is treated as a stop word.
STOPWORD_DF_RATIO = 0.5
# Below this many documents, no term is pruned. See ``BM25Okapi.stopwords``.
STOPWORD_MIN_CORPUS = 10


def tokenize(text: str) -> list[str]:
    """Split into scorable tokens: Latin words, and CJK bigrams.

    The TypeScript implementation must produce the same list for the same
    input — the two promise the same ranking over the same corpus, and a
    tokenizer that differs is the one divergence no later step can correct.
    """
    out: list[str] = []
    for run in _TOKEN_RE.findall(text.lower()):
        if not _CJK_RE.match(run):
            out.append(run)
            continue
        # Overlapping bigrams, so "季度进展" contributes 季度, 度进, 进展 and
        # a query naming any of those pairs matches on the pair rather than
        # on either character alone.
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return out


class BM25Okapi:
    """Minimal Okapi BM25 — same formula as rank_bm25 / Lucene defaults.

    ``score(D, Q) = Σ idf(q_i) * f(q_i, D) * (k1 + 1)
                          / (f(q_i, D) + k1 * (1 - b + b * |D| / avgdl))``
    """

    def __init__(
        self,
        tokenized_corpus: list[list[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.corpus_size = len(tokenized_corpus)
        self.doc_lens = [len(d) for d in tokenized_corpus]
        self.avgdl = sum(self.doc_lens) / self.corpus_size if self.corpus_size else 0.0

        self.doc_freqs: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for doc in tokenized_corpus:
            freqs: dict[str, int] = {}
            for tok in doc:
                freqs[tok] = freqs.get(tok, 0) + 1
            self.doc_freqs.append(freqs)
            for tok in freqs:
                df[tok] = df.get(tok, 0) + 1

        n = self.corpus_size
        # ``log(1 + (N - n + 0.5) / (n + 0.5))`` — Robertson-Spärck-Jones
        # weighting; the ``1 +`` guard keeps it non-negative when n ≈ N.
        self.idf = {term: math.log(1 + (n - count + 0.5) / (count + 0.5)) for term, count in df.items()}

        # Terms this corpus cannot distinguish on. A word in over half the
        # documents carries no ranking signal here — in a skills directory
        # that is "skill", "run", "use", the vocabulary of the format
        # itself — but its idf stays just above zero, so every document
        # holding it still collects score and an unrelated query still
        # produces a ranked list.
        #
        # Below ``STOPWORD_MIN_CORPUS`` documents this is off: on a corpus
        # of three, a term in two of them is over the threshold, and
        # pruning the query down to nothing is a worse answer than a weak
        # ranking.
        self.stopwords: frozenset[str] = (
            frozenset(term for term, count in df.items() if count / n > STOPWORD_DF_RATIO)
            if n >= STOPWORD_MIN_CORPUS
            else frozenset()
        )

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.corpus_size
        if not query_tokens or self.corpus_size == 0:
            return scores
        for term in query_tokens:
            if term in self.stopwords:
                continue
            idf = self.idf.get(term, 0.0)
            if idf <= 0.0:
                continue
            for i, freqs in enumerate(self.doc_freqs):
                f = freqs.get(term, 0)
                if f == 0:
                    continue
                dl = self.doc_lens[i]
                norm = self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
                scores[i] += idf * f * (self.k1 + 1) / (f + norm)
        return scores
