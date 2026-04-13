import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import macro_data_feed
from macro_data_feed import apply_macro_data_to_snapshot, load_macro_data_feed


def test_load_macro_data_feed_supports_fred_and_bls_specs(monkeypatch, tmp_path):
    spec_file = tmp_path / "macro_specs.json"
    spec_file.write_text(
        """
[
  {
    "provider": "fred",
    "name": "美国10年期实际利率",
    "series_id": "DFII10",
    "api_key_env": "FRED_API_KEY",
    "symbols": ["XAUUSD", "XAGUSD"],
    "importance": "high",
    "bias_mode": "higher_bearish"
  },
  {
    "provider": "bls",
    "name": "美国失业率",
    "series_id": "LNS14000000",
    "symbols": ["EURUSD", "USDJPY"],
    "importance": "high",
    "bias_mode": "lower_bullish"
  }
]
""",
        encoding="utf-8",
    )

    def fake_fetch(url, payload=None, headers=None, timeout=10):
        if "fred" in url:
            return {
                "observations": [
                    {"date": "2026-04-10", "value": "1.85"},
                    {"date": "2026-04-09", "value": "1.91"},
                ]
            }
        return {
            "Results": {
                "series": [
                    {
                        "seriesID": "LNS14000000",
                        "data": [
                            {"year": "2026", "period": "M03", "value": "4.1"},
                            {"year": "2026", "period": "M02", "value": "4.2"},
                        ],
                    }
                ]
            }
        }

    monkeypatch.setattr(macro_data_feed, "_fetch_json", fake_fetch)

    result = load_macro_data_feed(
        enabled=True,
        spec_source=str(spec_file),
        refresh_min=60,
        symbols=["XAUUSD", "EURUSD"],
        now=datetime(2026, 4, 13, 18, 0, 0),
        env={"FRED_API_KEY": "demo-key"},
    )

    assert result["status"] == "fresh"
    assert result["item_count"] == 2
    assert any(item["source"] == "FRED" for item in result["items"])
    assert any(item["source"] == "BLS" for item in result["items"])
    assert "结构化宏观数据" in result["summary_text"]


def test_load_macro_data_feed_falls_back_to_cache(tmp_path):
    spec_file = tmp_path / "macro_specs.json"
    cache_file = tmp_path / "macro_cache.json"
    spec_file.write_text("[]", encoding="utf-8")
    cache_file.write_text(
        """
{
  "spec_text": "%s",
  "fetched_at": "2026-04-13T17:00:00",
  "fetched_at_text": "2026-04-13 17:00:00",
  "summary_text": "结构化宏观数据：近一轮高相关数据包括 美国10年期实际利率 1.85（较前值 -0.06，偏多）。",
  "items": [
    {
      "name": "美国10年期实际利率",
      "source": "FRED",
      "published_at": "2026-04-10",
      "latest_value": 1.85,
      "previous_value": 1.91,
      "value_text": "1.85",
      "delta_text": "较前值 -0.06",
      "importance": "high",
      "symbols": ["XAUUSD"],
      "bias_mode": "higher_bearish",
      "direction": "bullish",
      "bias_text": "XAUUSD 在该指标上通常呈现“数值上行偏空、数值回落偏多”。"
    }
  ]
}
"""
        % str(spec_file).replace("\\", "\\\\"),
        encoding="utf-8",
    )

    result = load_macro_data_feed(
        enabled=True,
        spec_source=str(spec_file),
        refresh_min=5,
        symbols=["XAUUSD"],
        now=datetime(2026, 4, 13, 18, 0, 0),
        cache_file=cache_file,
    )

    assert result["status"] == "stale_cache"
    assert result["item_count"] == 1
    assert "继续使用" in result["status_text"]


def test_apply_macro_data_to_snapshot_appends_digest():
    snapshot = {
        "summary_text": "当前共观察 2 个品种。",
        "market_text": "先看美元方向。",
    }
    result = apply_macro_data_to_snapshot(
        snapshot,
        {
            "status_text": "结构化宏观数据已同步：2 条。",
            "summary_text": "结构化宏观数据：近一轮高相关数据包括 美国10年期实际利率 1.85（较前值 -0.06，偏多）。",
            "items": [{"name": "美国10年期实际利率"}],
        },
    )

    assert "宏观数据：" in result["summary_text"]
    assert "美国10年期实际利率" in result["market_text"]
    assert result["macro_data_status_text"] == "结构化宏观数据已同步：2 条。"
