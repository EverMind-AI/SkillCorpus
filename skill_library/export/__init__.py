"""export — produce the open SkillCorpus dataset.

The exporter is ``export/corpus.py``: ``write_corpus()`` emits
``skills.parquet`` + ``attachments/`` + a dataset card per
``docs/corpus-schema.md``. It is the final step of ``cli build``.
"""

from .corpus import CORPUS_SCHEMA, write_corpus

__all__ = ["CORPUS_SCHEMA", "write_corpus"]
