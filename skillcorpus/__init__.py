"""skillcorpus — the SkillCorpus build pipeline.

Aggregates agent skills from public repositories, filters them for safety and
license, deduplicates and classifies them, and exports a permissively-licensed
corpus (parquet + attachments + dataset card). See the README and docs/.
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
__version__ = "0.3.0"
