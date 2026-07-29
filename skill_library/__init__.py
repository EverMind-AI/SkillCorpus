"""skill_library — general-purpose skill library build pipeline.

Core capabilities:
- CRUD (add/get/update/delete/list)
- Ingest filtering: deduplication + quality filtering + automatic classification
- Multi-source aggregation + incremental refresh
- Export mass_library.db for consumer retrieval

Positioned as an ingest/build pipeline — runtime retrieval (BM25+embedding search over the library) is handled by the consumer side.
"""

from .store import SkillRecord, Category, CATEGORIES
from .pipeline import SkillLibrary
from .pipeline import IngestResult, IngestStatus

__all__ = [
    "SkillRecord", "Category", "CATEGORIES",
    "SkillLibrary", "IngestResult", "IngestStatus",
]
__version__ = "0.1.0"
