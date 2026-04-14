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


def test_load_macro_data_feed_yfinance_adapter(monkeypatch, tmp_path):
    """yfinance adapter: mock Yahoo Finance chart API，验证 DXY/VIX 等实时市场指标可正确解析。"""
    spec_inline = """
[
  {
    "provider": "yfinance",
    "name": "美元指数 (DXY)",
    "symbol": "DX-Y.NYB",
    "symbols": ["XAUUSD", "XAGUSD", "EURUSD"],
    "importance": "high",
    "bias_mode": "higher_bearish"
  },
  {
    "provider": "yfinance",
    "name": "VIX 恐慌指数",
    "symbol": "^VIX",
    "symbols": ["XAUUSD"],
    "importance": "medium",
    "bias_mode": "higher_bullish"
  }
]
"""

    import time as _time

    fake_now_ts = int(_time.mktime((2026, 4, 13, 10, 0, 0, 0, 0, 0)))

    def fake_fetch(url, payload=None, headers=None, timeout=10):
        # 根据 URL 中的 symbol 判断返回哪支
        if "DX-Y" in url or "DX%2DY" in url:
            close_val = 104.5
            prev_val = 104.2
        else:
            close_val = 18.3
            prev_val = 17.9
        return {
            "chart": {
                "result": [
                    {
                        "timestamp": [fake_now_ts - 86400, fake_now_ts],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [prev_val, close_val],
                                }
                            ]
                        },
                    }
                ]
            }
        }

    monkeypatch.setattr("macro_data_feed._fetch_json", fake_fetch)

    result = load_macro_data_feed(
        enabled=True,
        spec_source=spec_inline,
        refresh_min=60,
        symbols=["XAUUSD"],
        now=datetime(2026, 4, 13, 18, 0, 0),
        cache_file=tmp_path / "yf_cache.json",
    )

    assert result["status"] == "fresh"
    assert result["item_count"] == 2
    assert all(item["source"] == "Yahoo Finance" for item in result["items"])
    # DXY 上涨 → higher_bearish → direction=bearish
    dxy_item = next(i for i in result["items"] if "DXY" in i["name"])
    assert dxy_item["direction"] == "bearish"
    # VIX 上涨 → higher_bullish → direction=bullish
    vix_item = next(i for i in result["items"] if "VIX" in i["name"])
    assert vix_item["direction"] == "bullish"


def test_load_macro_data_feed_alphavantage_adapter(monkeypatch, tmp_path):
    """alphavantage adapter: mock Alpha Vantage data 列表，验证 CPI/NFP 月频数据可正确解析。"""
    spec_inline = """
[
  {
    "provider": "alphavantage",
    "name": "美国核心 CPI（月频）",
    "function": "CPI",
    "interval": "monthly",
    "api_key_env": "ALPHAVANTAGE_API_KEY",
    "symbols": ["XAUUSD", "EURUSD"],
    "importance": "high",
    "bias_mode": "higher_bearish"
  },
  {
    "provider": "alphavantage",
    "name": "美国非农就业人数 (NFP)",
    "function": "NONFARM_PAYROLL",
    "api_key_env": "ALPHAVANTAGE_API_KEY",
    "symbols": ["XAUUSD", "USDJPY"],
    "importance": "high",
    "bias_mode": "higher_bearish"
  }
]
"""

    def fake_fetch(url, payload=None, headers=None, timeout=10):
        if "CPI" in url:
            return {
                "name": "Consumer Price Index for all Urban Consumers",
                "data": [
                    {"date": "2026-03-01", "value": "312.3"},
                    {"date": "2026-02-01", "value": "311.1"},
                ],
            }
        # NFP
        return {
            "name": "Total Nonfarm Payroll",
            "data": [
                {"date": "2026-03-01", "value": "160500"},
                {"date": "2026-02-01", "value": "159800"},
            ],
        }

    monkeypatch.setattr("macro_data_feed._fetch_json", fake_fetch)

    result = load_macro_data_feed(
        enabled=True,
        spec_source=spec_inline,
        refresh_min=60,
        symbols=["XAUUSD"],
        now=datetime(2026, 4, 13, 18, 0, 0),
        cache_file=tmp_path / "av_cache.json",
        env={"ALPHAVANTAGE_API_KEY": "demo-key"},
    )

    assert result["status"] == "fresh"
    assert result["item_count"] == 2
    assert all(item["source"] == "Alpha Vantage" for item in result["items"])
    # CPI 上涨 → higher_bearish → direction=bearish（利空黄金）
    cpi_item = next(i for i in result["items"] if "CPI" in i["name"])
    assert cpi_item["direction"] == "bearish"
    assert cpi_item["latest_value"] == 312.3
    # NFP 上涨 → higher_bearish → direction=bearish
    nfp_item = next(i for i in result["items"] if "NFP" in i["name"])
    assert nfp_item["direction"] == "bearish"


def test_official_json_file_is_valid_and_has_new_providers():
    """官方 JSON 数据源文件格式验证：确保 yfinance 和 alphavantage 条目已正确加入。"""
    import json
    from pathlib import Path

    official_file = Path(__file__).resolve().parent.parent / "macro_data_sources.official.json"
    assert official_file.exists(), "macro_data_sources.official.json 不存在"

    specs = json.loads(official_file.read_text(encoding="utf-8"))
    assert isinstance(specs, list) and len(specs) >= 8, f"期望至少 8 条数据源，实际 {len(specs)}"

    providers = {str(s.get("provider", "")).lower() for s in specs}
    assert "yfinance" in providers, "官方 JSON 缺少 yfinance 条目"
    assert "alphavantage" in providers, "官方 JSON 缺少 alphavantage 条目"
    assert "fred" in providers, "官方 JSON 缺少 fred 条目"

    # 每条记录必须有 name / symbols / importance / bias_mode
    required_keys = {"name", "symbols", "importance", "bias_mode"}
    for spec in specs:
        missing = required_keys - set(spec.keys())
        assert not missing, f"条目 {spec.get('name', '?')} 缺少字段 {missing}"
