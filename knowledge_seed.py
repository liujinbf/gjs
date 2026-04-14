"""
知识库播种器：导入本地 Markdown 资料，并登记外部来源种子。

本地 Markdown 文件统一放在 knowledge_docs/ 目录下，系统启动时自动全量导入。
外部来源登记在 knowledge_sources.py 的 EXTERNAL_KNOWLEDGE_SOURCE_SEEDS 列表中。
"""
from __future__ import annotations

from pathlib import Path

from app_config import PROJECT_DIR
from knowledge_base import import_markdown_source, init_knowledge_base, seed_external_sources, summarize_knowledge_base
from knowledge_sources import EXTERNAL_KNOWLEDGE_SOURCE_SEEDS

# 本地知识文档目录：统一存放所有 Markdown 学习材料
KNOWLEDGE_DOCS_DIR = PROJECT_DIR / "knowledge_docs"


def get_local_knowledge_paths() -> list[Path]:
    """扫描 knowledge_docs/ 目录，返回所有 .md 文件路径列表。"""
    if not KNOWLEDGE_DOCS_DIR.exists():
        return []
    return sorted(KNOWLEDGE_DOCS_DIR.glob("*.md"))


def seed_knowledge_base(local_paths: list[str | Path] | None = None, db_path: Path | str | None = None) -> dict:
    """
    导入本地 Markdown 知识文档并登记外部来源。

    :param local_paths: 额外指定的 Markdown 文件路径列表（可选）。
                        若为 None 或空列表，自动扫描 knowledge_docs/ 目录。
    :param db_path: 数据库路径（默认使用 KNOWLEDGE_DB_FILE）。
    """
    init_knowledge_base(db_path=db_path)

    # 优先使用调用方传入的路径，否则自动扫描 knowledge_docs/
    if local_paths:
        paths_to_import = [Path(p) for p in local_paths]
    else:
        paths_to_import = get_local_knowledge_paths()

    imported = []
    for path in paths_to_import:
        if not path.exists() or not path.is_file():
            continue
        result = import_markdown_source(
            path,
            title=path.stem,
            author="用户收集资料",
            trust_level="user_note",
            tags=["seed", "local_markdown"],
            notes="作为前期知识库种子导入，后续需结合回测与权威来源再分层清洗。",
            db_path=db_path,
        )
        imported.append(result)

    seed_external_sources(EXTERNAL_KNOWLEDGE_SOURCE_SEEDS, db_path=db_path)
    summary = summarize_knowledge_base(db_path=db_path)
    summary["imported_documents"] = len(imported)
    summary["imported_rule_count"] = sum(int(item.get("rule_count", 0) or 0) for item in imported)
    return summary
