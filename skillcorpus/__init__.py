"""skillcorpus — general-purpose skill library build pipeline.

Core capabilities:
- CRUD (add/get/update/delete/list)
- Ingest filtering: deduplication + quality filtering + automatic classification
- Multi-source aggregation + incremental refresh
- Export mass_library.db for consumer retrieval

Positioned as an ingest/build pipeline — runtime retrieval (BM25+embedding search over the library) is handled by the consumer side.
"""

from .core.models import SkillRecord, Category, CATEGORIES
from .curate.pipeline import IngestResult, IngestStatus


def __getattr__(name):
    # SkillLibrary lives in cli.py (the orchestration layer). Import it lazily
    # so `import skillcorpus` does not eagerly pull in cli — otherwise
    # `python -m skillcorpus.cli` would load cli twice (package + __main__).
    if name == "SkillLibrary":
        from .cli import SkillLibrary
        return SkillLibrary
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "SkillRecord", "Category", "CATEGORIES",
    "SkillLibrary", "IngestResult", "IngestStatus",
]
__version__ = "0.1.0"
