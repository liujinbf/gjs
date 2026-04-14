import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import event_feed
from event_feed import _write_cache, apply_event_feed_to_snapshot, build_schedule_text_from_payload, build_structured_event_items, load_event_feed, merge_event_schedule_texts


def test_build_schedule_text_from_payload_supports_nested_events():
    payload = {
        "events": [
            {"time": "2026-04-15 20:30", "name": "美国 CPI", "importance": "high", "symbols": ["XAUUSD", "EURUSD"]},
            {"time": "2026-04-16T02:00:00+08:00", "title": "联储利率决议", "importance": "medium"},
        ]
    }
    text = build_schedule_text_from_payload(payload)
    assert "2026-04-15 20:30|美国 CPI|high|XAUUSD,EURUSD" in text
    assert "2026-04-16 02:00|联储利率决议" in text


def test_build_structured_event_items_supports_actual_forecast_previous():
    payload = {
        "events": [
            {
                "time": "2026-04-15 20:30",
                "name": "美国 CPI",
                "importance": "high",
                "symbols": ["XAUUSD", "EURUSD"],
                "actual": "3.4",
                "forecast": "3.2",
                "previous": "3.1",
                "unit": "%",
                "better_when": "higher_bearish",
            }
        ]
    }
    items = build_structured_event_items(payload)
    assert len(items) == 1
    assert items[0]["has_result"] is True
    assert items[0]["actual"] == 3.4
    assert items[0]["forecast"] == 3.2
    assert items[0]["previous"] == 3.1
    assert items[0]["result_bias"] == "bearish"
    assert "结果解读 偏空" in items[0]["result_summary_text"]


def test_merge_event_schedule_texts_dedupes_entries():
    merged = merge_event_schedule_texts(
        "2026-04-15 20:30|美国 CPI",
        "2026-04-15 20:30|美国 CPI;2026-04-16 02:00|联储利率决议|high|XAUUSD",
    )
    assert merged == "2026-04-15 20:30|美国 CPI;2026-04-16 02:00|联储利率决议|high|XAUUSD"


def test_load_event_feed_reads_local_json_and_writes_cache(tmp_path):
    source_file = tmp_path / "events.json"
    cache_file = tmp_path / "event_feed_cache.json"
    source_file.write_text(
        '[{"time":"2026-04-15 20:30","name":"美国 CPI","importance":"high","symbols":["XAUUSD"]}]',
        encoding="utf-8",
    )

    result = load_event_feed(
        enabled=True,
        source=str(source_file),
        refresh_min=60,
        now=datetime(2026, 4, 15, 18, 0),
        cache_file=cache_file,
    )

    assert result["status"] == "fresh"
    assert "美国 CPI" in result["schedule_text"]
    assert cache_file.exists()


def test_load_event_feed_returns_result_summary_for_structured_items(tmp_path):
    source_file = tmp_path / "events_with_results.json"
    cache_file = tmp_path / "event_feed_cache.json"
    source_file.write_text(
        json.dumps(
            [
                {
                    "time": "2026-04-15 20:30",
                    "name": "美国 CPI",
                    "importance": "high",
                    "symbols": ["XAUUSD"],
                    "actual": 3.4,
                    "forecast": 3.2,
                    "previous": 3.1,
                    "unit": "%",
                    "better_when": "higher_bearish",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = load_event_feed(
        enabled=True,
        source=str(source_file),
        refresh_min=60,
        now=datetime(2026, 4, 15, 18, 0),
        cache_file=cache_file,
    )

    assert result["status"] == "fresh"
    assert result["result_item_count"] == 1
    assert "事件结果：" in result["result_summary_text"]
    assert result["items"][0]["result_bias"] == "bearish"


def test_load_event_feed_falls_back_to_cache_when_source_fails(tmp_path):
    missing_source = tmp_path / "missing.json"
    cache_file = tmp_path / "event_feed_cache.json"
    cache_file.write_text(
        (
            '{'
            '"source":"%s",'
            '"fetched_at":"2026-04-15T17:00:00",'
            '"fetched_at_text":"2026-04-15 17:00:00",'
            '"schedule_text":"2026-04-15 20:30|美国 CPI|high|XAUUSD",'
            '"item_count":1'
            '}'
        )
        % str(missing_source).replace("\\", "\\\\"),
        encoding="utf-8",
    )

    result = load_event_feed(
        enabled=True,
        source=str(missing_source),
        refresh_min=5,
        now=datetime(2026, 4, 15, 18, 0),
        cache_file=cache_file,
    )

    assert result["status"] == "stale_cache"
    assert "继续使用" in result["status_text"]
    assert "美国 CPI" in result["schedule_text"]


def test_apply_event_feed_to_snapshot_appends_result_summary():
    snapshot = {
        "summary_text": "当前共观察 2 个品种。",
        "market_text": "先看美元方向。",
    }
    result = apply_event_feed_to_snapshot(
        snapshot,
        {
            "items": [
                {
                    "name": "美国 CPI",
                    "has_result": True,
                    "result_summary_text": "美国 CPI：实际 3.4%，预期 3.2%，前值 3.1%，结果解读 偏空",
                }
            ],
            "result_item_count": 1,
            "result_summary_text": "事件结果：美国 CPI：实际 3.4%，预期 3.2%，前值 3.1%，结果解读 偏空。",
        },
    )

    assert "事件结果：" in result["summary_text"]
    assert "美国 CPI" in result["market_text"]
    assert result["event_result_item_count"] == 1


def test_event_feed_cache_write_is_atomic(tmp_path):
    cache_file = tmp_path / "event_cache.json"

    _write_cache(cache_file, {"source": "demo", "item_count": 3})

    assert cache_file.exists()
    assert not cache_file.with_suffix(".json.tmp").exists()
    assert event_feed._read_cache(cache_file)["item_count"] == 3
