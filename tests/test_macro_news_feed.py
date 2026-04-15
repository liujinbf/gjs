import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import macro_news_feed
from macro_news_feed import _write_cache, apply_macro_news_to_snapshot, load_macro_news_feed
from external_feed_models import MacroNewsItem


def test_load_macro_news_feed_reads_local_rss_and_filters_by_symbols(tmp_path):
    source_file = tmp_path / "macro.xml"
    cache_file = tmp_path / "macro_cache.json"
    source_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>ECB Feed</title>
    <item>
      <title>ECB policy decision keeps rates unchanged</title>
      <description>Lagarde says euro area inflation remains in focus.</description>
      <pubDate>Mon, 13 Apr 2026 09:00:00 GMT</pubDate>
      <link>https://example.com/ecb-1</link>
    </item>
    <item>
      <title>Bank of Japan governor comments on yields</title>
      <description>Market watches yen and JGBs.</description>
      <pubDate>Mon, 13 Apr 2026 08:00:00 GMT</pubDate>
      <link>https://example.com/boj-1</link>
    </item>
  </channel>
</rss>
""",
        encoding="utf-8",
    )

    result = load_macro_news_feed(
        enabled=True,
        source_text=str(source_file),
        refresh_min=30,
        symbols=["EURUSD"],
        now=datetime(2026, 4, 13, 18, 0, 0),
        cache_file=cache_file,
    )

    assert result["status"] == "fresh"
    assert result["item_count"] >= 1
    assert any("ECB" in item["title"] or item["source"] == "ECB Feed" for item in result["items"])
    assert all("USDJPY" not in list(item.get("symbols", []) or []) for item in result["items"])
    assert any(item.get("bias_by_symbol", {}).get("EURUSD") == "bullish" for item in result["items"])


def test_load_macro_news_feed_falls_back_to_cache_when_source_fails(tmp_path):
    missing_file = tmp_path / "missing.xml"
    cache_file = tmp_path / "macro_cache.json"
    cache_file.write_text(
        """
{
  "source_text": "%s",
  "fetched_at": "2026-04-13T17:40:00",
  "fetched_at_text": "2026-04-13 17:40:00",
  "summary_text": "外部资讯流：近一轮抓到 1 条高相关更新，最新包括 ECB：ECB policy decision keeps rates unchanged。",
  "items": [
    {
      "title": "ECB policy decision keeps rates unchanged",
      "summary": "Lagarde says euro area inflation remains in focus.",
      "published_at": "2026-04-13 17:30:00",
      "link": "https://example.com/ecb-1",
      "source": "ECB",
      "importance": "high",
      "symbols": ["EURUSD"]
    }
  ]
}
"""
        % str(missing_file).replace("\\", "\\\\"),
        encoding="utf-8",
    )

    result = load_macro_news_feed(
        enabled=True,
        source_text=str(missing_file),
        refresh_min=10,
        symbols=["EURUSD"],
        now=datetime(2026, 4, 13, 18, 0, 0),
        cache_file=cache_file,
    )

    assert result["status"] == "stale_cache"
    assert result["item_count"] == 1
    assert "继续使用" in result["status_text"]


def test_apply_macro_news_to_snapshot_appends_digest():
    snapshot = {
        "summary_text": "当前共观察 2 个品种。",
        "market_text": "先看美元方向。",
    }
    result = apply_macro_news_to_snapshot(
        snapshot,
        {
            "status_text": "外部资讯流已同步：2 条高相关更新。",
            "summary_text": "外部资讯流：近一轮抓到 2 条高相关更新，最新包括 ECB：ECB policy decision keeps rates unchanged。",
            "items": [{"title": "ECB policy decision keeps rates unchanged"}],
        },
    )

    assert "资讯流：" in result["summary_text"]
    assert "ECB policy decision" in result["market_text"]
    assert result["macro_news_status_text"] == "外部资讯流已同步：2 条高相关更新。"


def test_load_macro_news_feed_infers_bearish_bias_for_gold_from_hawkish_fed(tmp_path):
    source_file = tmp_path / "macro_gold.xml"
    cache_file = tmp_path / "macro_gold_cache.json"
    source_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Fed Feed</title>
    <item>
      <title>Powell stays hawkish as higher yields pressure gold</title>
      <description>Markets price higher for longer after strong payroll and sticky inflation.</description>
      <pubDate>Mon, 13 Apr 2026 09:00:00 GMT</pubDate>
      <link>https://example.com/fed-1</link>
    </item>
  </channel>
</rss>
""",
        encoding="utf-8",
    )

    result = load_macro_news_feed(
        enabled=True,
        source_text=str(source_file),
        refresh_min=30,
        symbols=["XAUUSD"],
        now=datetime(2026, 4, 13, 18, 0, 0),
        cache_file=cache_file,
    )

    assert result["status"] == "fresh"
    assert result["items"][0]["bias_by_symbol"]["XAUUSD"] == "bearish"
    assert "XAUUSD 偏空" in result["summary_text"]


def test_macro_news_cache_write_is_atomic(tmp_path):
    cache_file = tmp_path / "macro_news_cache.json"

    _write_cache(cache_file, {"status": "fresh", "items": [{"title": "demo"}]})

    assert cache_file.exists()
    assert not cache_file.with_suffix(".json.tmp").exists()
    assert macro_news_feed._read_cache(cache_file)["status"] == "fresh"


def test_apply_macro_news_to_snapshot_accepts_macro_news_item_objects():
    snapshot = {
        "summary_text": "当前共观察 2 个品种。",
        "market_text": "先看美元方向。",
    }
    result = apply_macro_news_to_snapshot(
        snapshot,
        {
            "status_text": "外部资讯流已同步：1 条高相关更新。",
            "summary_text": "外部资讯流：近一轮抓到 1 条高相关更新，最新包括 ECB：ECB policy decision keeps rates unchanged。",
            "items": [
                MacroNewsItem(
                    title="ECB policy decision keeps rates unchanged",
                    summary="Lagarde says euro area inflation remains in focus.",
                    published_at="2026-04-13 17:30:00",
                    link="https://example.com/ecb-1",
                    source="ECB",
                    importance="high",
                    symbols=["EURUSD"],
                    bias_by_symbol={"EURUSD": "bullish"},
                    bias_summary_text="EURUSD 偏多",
                )
            ],
        },
    )

    assert result["macro_news_items"][0]["title"] == "ECB policy decision keeps rates unchanged"
    assert result["macro_news_items"][0]["bias_by_symbol"]["EURUSD"] == "bullish"
