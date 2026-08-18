"""Corpus-adaptive stop words: what a query is allowed to match on.

BM25's idf keeps a term that appears in almost every document just above
zero rather than at it, so a query made entirely of such terms still ranks
every document and returns a confident-looking list of whatever happened to
be shortest. In a skills directory those terms are the vocabulary of the
format — "skill", "use", "run" — and the result is a skill injected into a
turn that shares nothing with it.

Pruning them from the query is what lets the local source answer "nothing
here matches", which is the answer the gate would otherwise have to reach
for it.
"""

from __future__ import annotations

from skillsearch.bm25 import STOPWORD_MIN_CORPUS, BM25Okapi, tokenize


def corpus(*docs: str) -> BM25Okapi:
    return BM25Okapi([tokenize(d) for d in docs])


def test_a_term_in_most_documents_is_pruned() -> None:
    index = corpus(*[f"skill for handling case {i}" for i in range(12)])
    assert index.stopwords == {"skill", "for", "handling", "case"}
    # The per-document number is not shared, so it stays a ranking signal.
    assert "11" not in index.stopwords


def test_a_query_of_only_such_terms_scores_nothing() -> None:
    index = corpus(*[f"skill for handling case {i}" for i in range(12)])
    assert max(index.get_scores(tokenize("skill"))) == 0.0


def test_a_distinguishing_term_still_ranks() -> None:
    docs = [f"skill for handling case {i}" for i in range(12)]
    docs.append("skill for parsing pdf acroforms")
    index = corpus(*docs)
    scores = index.get_scores(tokenize("acroforms"))
    assert scores.index(max(scores)) == len(docs) - 1


def test_a_small_corpus_prunes_nothing() -> None:
    """Below the guard, over half the documents is two of three."""
    index = corpus("pdf forms", "pdf tables", "csv audit")
    assert index.stopwords == frozenset()
    assert max(index.get_scores(tokenize("pdf"))) > 0.0


def test_the_guard_is_where_it_says_it_is() -> None:
    common = "shared term appears everywhere"
    below = corpus(*[f"{common} {i}" for i in range(STOPWORD_MIN_CORPUS - 1)])
    at = corpus(*[f"{common} {i}" for i in range(STOPWORD_MIN_CORPUS)])
    assert below.stopwords == frozenset()
    assert "shared" in at.stopwords


async def test_an_unrelated_query_gets_nothing_from_the_local_source(tmp_path) -> None:
    """The behaviour this exists for, end to end.

    A directory of file-handling skills, asked about the weather. Before
    pruning, "the" and "skill" carried the whole query and something always
    came back.
    """
    from skillsearch import SearchConfig, SkillSearch

    skills = tmp_path / "skills"
    for i, (name, description) in enumerate([
        ("pdf-forms", "Use this skill to fill PDF acroforms"),
        ("pdf-tables", "Use this skill to extract tables from a PDF"),
        ("csv-audit", "Use this skill to audit a CSV for malformed rows"),
        ("csv-merge", "Use this skill to merge CSV files on a key"),
        ("json-lint", "Use this skill to lint a JSON document"),
        ("json-patch", "Use this skill to apply a JSON patch"),
        ("xml-strip", "Use this skill to strip an XML namespace"),
        ("yaml-fix", "Use this skill to repair YAML indentation"),
        ("toml-read", "Use this skill to read a TOML table"),
        ("ini-parse", "Use this skill to parse an INI section"),
        ("log-tail", "Use this skill to tail a log file"),
        ("log-grep", "Use this skill to grep a log file"),
    ]):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\nSteps.\n"
        )

    search = SkillSearch(
        SearchConfig.from_mapping({"skills_dir": str(skills), "workspace": str(tmp_path)})
    )
    assert await search.retrieve("use this skill to tell me the weather") == ""
    # The corpus still answers what it does hold.
    assert "pdf-forms" in await search.retrieve("fill an acroform")
