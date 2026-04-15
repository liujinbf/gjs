import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from external_feed_models import MacroDataItem
from monitor_cards import build_macro_data_status_card


def test_build_macro_data_status_card_accepts_macro_data_item_objects():
    cards = build_macro_data_status_card(
        "结构化宏观数据已同步：1 条。",
        macro_data_items=[
            MacroDataItem(
                name="美国10年期实际利率",
                source="FRED",
                value_text="1.85",
                delta_text="较前值 -0.06",
                direction="bullish",
                bias_text="对贵金属偏多",
            )
        ],
    )

    assert cards[0]["title"] == "宏观数据同步状态"
    assert cards[1]["title"] == "美国10年期实际利率"
    assert "当前值 1.85" in cards[1]["detail"]
    assert "对贵金属偏多" in cards[1]["detail"]
