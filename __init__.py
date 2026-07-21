"""skill_library — 通用 skill 库构建管线 (build pipeline).

核心能力:
- CRUD (add/get/update/delete/list)
- 入库筛选: 去重 + 质量过滤 + 自动分类
- 多源聚合 + 增量 refresh
- 导出 mass_library.db 供 consumer 检索

定位是入库/构建管线 — runtime 检索 (BM25+embedding 搜库) 由 consumer 端负责。
"""

from .store import SkillRecord, Category, CATEGORIES
from .pipeline import SkillLibrary
from .pipeline import IngestResult, IngestStatus

__all__ = [
    "SkillRecord", "Category", "CATEGORIES",
    "SkillLibrary", "IngestResult", "IngestStatus",
]
__version__ = "0.1.0"
