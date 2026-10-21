"""Lexical channel: BM25 over name, description and body (k1 = 1.5, b = 0.75)."""
import re
from collections import Counter

import numpy as np
from scipy import sparse

STOP = set("""a an the of to in for and or is are be with on at by from this that it as
we you your i they them their our using use used can will should would if then when
which what how not no all any each such other than more most some""".split())
TOKEN = re.compile(r"[a-z0-9_\-\.]+")


def tokenize(text):
    return [w for w in TOKEN.findall((text or "").lower()) if len(w) > 1 and w not in STOP]


class BM25:
    """Okapi BM25 with idf = ln(1 + (n - df + 0.5) / (df + 0.5)), scored as a sparse matrix product."""

    def __init__(self, doc_texts, k1=1.5, b=0.75):
        vocab, rows, cols, tfs, dl = {}, [], [], [], []
        for i, text in enumerate(doc_texts):
            toks = tokenize(text)
            dl.append(len(toks))
            for w, c in Counter(toks).items():
                rows.append(i)
                cols.append(vocab.setdefault(w, len(vocab)))
                tfs.append(c)
        self.n, self.vocab = len(dl), vocab
        rows, cols = np.asarray(rows, np.int64), np.asarray(cols, np.int64)
        tf, dl = np.asarray(tfs, np.float32), np.asarray(dl, np.float32)
        df = np.bincount(cols, minlength=len(vocab))
        self.idf = np.log(1 + (self.n - df + 0.5) / (df + 0.5)).astype(np.float32)
        norm = k1 * (1 - b + b * dl / dl.mean())
        weight = self.idf[cols] * (tf * (k1 + 1)) / (tf + norm[rows])
        # term x document, so that a batch of queries multiplies it from the left
        self.weights = sparse.csr_matrix((weight, (cols, rows)), shape=(len(vocab), self.n))

    def _query_matrix(self, queries):
        rows, cols = [], []
        for i, q in enumerate(queries):
            for j in {self.vocab[w] for w in tokenize(q) if w in self.vocab}:
                rows.append(i)
                cols.append(j)
        data = np.ones(len(rows), np.float32)
        return sparse.csr_matrix((data, (rows, cols)), shape=(len(queries), len(self.vocab)))

    def scores(self, query):
        return (self._query_matrix([query]) @ self.weights).toarray()[0]

    def search(self, queries, topn=100, batch=64):
        """Returns (ids, scores): for each query, the top-n document indices and their BM25 scores."""
        all_ids, all_scores = [], []
        topn = min(topn, self.n)
        for s in range(0, len(queries), batch):
            sc = (self._query_matrix(queries[s:s + batch]) @ self.weights).toarray()
            top = np.argpartition(-sc, min(topn, self.n - 1), axis=1)[:, :topn]
            for r in range(sc.shape[0]):
                t = top[r][np.argsort(-sc[r, top[r]], kind="stable")]
                all_ids.append(t.tolist())
                all_scores.append(sc[r, t].tolist())
        return all_ids, all_scores


def lexical_queries(requests, hypothetical=None):
    """With query-side expansion on, the lexical channel searches with the request and its hypothetical skills."""
    if hypothetical is None:
        return [r["query"] for r in requests]
    return [r["query"] + " " + " ".join(hypothetical.get(r["task"], [])) for r in requests]
