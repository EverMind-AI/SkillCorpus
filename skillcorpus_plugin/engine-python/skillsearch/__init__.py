"""Skill retrieval for agent hosts.

One engine, three adapters. ``SkillSearch.retrieve(query)`` returns the
text to inject; an adapter's whole job is to call it at the right moment
and hand the result to its host.

    from skillsearch import SearchConfig, SkillSearch

    search = SkillSearch(SearchConfig(skills_dir="~/.agent/skills"))
    block = await search.retrieve("extract tables from a scanned PDF")

Nothing under ``skillsearch/`` imports a host. Host-specific code lives in
``skillsearch/adapters/``.
"""

from skillsearch.config import LocalDir, SearchConfig
from skillsearch.engine import SkillSearch
from skillsearch.ports import ChatModel, SkillStore
from skillsearch.types import RouterHit

__version__ = "0.1.0"

__all__ = [
    "ChatModel",
    "LocalDir",
    "RouterHit",
    "SearchConfig",
    "SkillSearch",
    "SkillStore",
]
