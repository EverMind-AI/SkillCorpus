"""Reply extraction, against shapes a live model actually produced.

Every case here came off a real model answering one of this package's two
prompts. The trailing-commentary one is why this module exists: it defeated
the rewriter's parser but not the gate's, so a verdict the model did give
was silently discarded and retrieval ran anyway.
"""

from __future__ import annotations

import pytest

from skillsearch.replies import extract_json_object

FOUND = [
    ("a bare object", '{"a": 1}'),
    ("a fenced object", '```json\n{"a": 1}\n```'),
    ("a fence followed by commentary", '```json\n{"a": 1}\n```\n\nThis query is general.'),
    ("a reasoning block first", '<think>weighing it</think>\n```\n{"a": 1}\n```'),
    ("an object embedded in prose", 'Sure! {"a": 1} hope that helps'),
]

MISSING = [
    ("prose with no object", "I think you should try rebasing."),
    ("an empty reply", ""),
    ("a JSON array", "[1, 2, 3]"),
]


@pytest.mark.parametrize(("label", "reply"), FOUND, ids=[c[0] for c in FOUND])
def test_reads(label: str, reply: str) -> None:
    assert extract_json_object(reply) == {"a": 1}


@pytest.mark.parametrize(("label", "reply"), MISSING, ids=[c[0] for c in MISSING])
def test_reports_nothing(label: str, reply: str) -> None:
    assert extract_json_object(reply) is None


async def test_the_rewriter_reads_a_fenced_block_followed_by_prose() -> None:
    """The divergence this module exists to close.

    A live model answered with a fenced block and then explained itself.
    The gate's parser coped and the rewriter's did not, so the same reply
    meant different things to the two callers.
    """
    from skillsearch.rewriter import QueryRewriter

    class Model:
        async def complete(self, *args, **kwargs) -> str:
            return (
                '```json\n{"rewritten_query": "nginx basic auth htpasswd"}\n```\n\n'
                "This is a general troubleshooting request."
            )

    result = await QueryRewriter(Model()).analyze("why did this break")
    assert result.rewritten_query == "nginx basic auth htpasswd"
