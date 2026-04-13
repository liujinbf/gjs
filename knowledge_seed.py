"""
知识库播种器：导入本地 Markdown 资料，并登记外部来源种子。
"""
from __future__ import annotations

from pathlib import Path

from knowledge_base import import_markdown_source, init_knowledge_base, seed_external_sources, summarize_knowledge_base
from knowledge_sources import EXTERNAL_KNOWLEDGE_SOURCE_SEEDS


def seed_knowledge_base(local_paths: list[str | Path], db_path: Path | str | None = None) -> dict:
    init_knowledge_base(db_path=db_path)
    imported = []
    for raw_path in list(local_paths or []):
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            continue
        imported.append(
            import_markdown_source(
                path,
                title=path.stem,
                author="用户收集资料",
                trust_level="user_note",
                tags=["seed", "local_markdown"],
                notes="作为前期知识库种子导入，后续需结合回测与权威来源再分层清洗。",
                db_path=db_path,
            )
        )

    seed_external_sources(EXTERNAL_KNOWLEDGE_SOURCE_SEEDS, db_path=db_path)
    summary = summarize_knowledge_base(db_path=db_path)
    summary["imported_documents"] = len(imported)
    summary["imported_rule_count"] = sum(int(item.get("rule_count", 0) or 0) for item in imported)
    return summary
