import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_base import extract_candidate_rules, import_markdown_source, seed_external_sources, summarize_knowledge_base
from knowledge_sources import EXTERNAL_KNOWLEDGE_SOURCE_SEEDS


def test_extract_candidate_rules_reads_bullets_and_tables():
    markdown = """
# 黄金策略
## 入场逻辑
- 回调至关键支撑位企稳后再考虑买入
| 关键指标 | 参考标准 |
|---------|---------|
| 美联储货币政策 | 加息尾声、降息周期启动时偏利好黄金 |

## 风险控制
1. 单笔亏损不超过账户 2%
- 连续止损 3 次后先暂停
"""
    rules = extract_candidate_rules(markdown)
    texts = [item["rule_text"] for item in rules]
    assert any("关键支撑位企稳后再考虑买入" in text for text in texts)
    assert any("加息尾声、降息周期启动时偏利好黄金" in text for text in texts)
    assert any(item["category"] == "risk" for item in rules)
    assert any(item["asset_scope"] == "XAUUSD" for item in rules)


def test_import_markdown_source_builds_db_rows(tmp_path):
    db_path = tmp_path / "knowledge.db"
    file_path = tmp_path / "经验.md"
    file_path.write_text(
        """
# 白银交易
## 风险控制
- 白银波动更大，单笔仓位不超过总资金的 5%
## 做多做空
- 不在第一次突破时追单，优先等回踩确认
""",
        encoding="utf-8",
    )

    result = import_markdown_source(file_path, db_path=db_path)
    summary = summarize_knowledge_base(db_path=db_path)
    assert result["rule_count"] >= 2
    assert summary["source_count"] == 1
    assert summary["document_count"] == 1
    assert summary["rule_count"] >= 2


def test_seed_external_sources_registers_metadata_only(tmp_path):
    db_path = tmp_path / "knowledge.db"
    count = seed_external_sources(EXTERNAL_KNOWLEDGE_SOURCE_SEEDS[:3], db_path=db_path)
    summary = summarize_knowledge_base(db_path=db_path)
    assert count == 3
    assert summary["source_count"] == 3
    assert summary["document_count"] == 0
