import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_briefing import build_snapshot_prompt, request_ai_brief
from app_config import MetalMonitorConfig
from prompt_templates import build_metal_advisor_prompt, build_metal_batch_prompt


def _build_config(api_key: str = "demo-key") -> MetalMonitorConfig:
    return MetalMonitorConfig(
        symbols=["XAUUSD", "EURUSD"],
        refresh_interval_sec=30,
        event_risk_mode="normal",
        mt5_path="",
        mt5_login="",
        mt5_password="",
        mt5_server="",
        dingtalk_webhook="",
        pushplus_token="",
        notify_cooldown_min=30,
        ai_api_key=api_key,
        ai_api_base="https://api.siliconflow.cn/v1",
        ai_model="deepseek-ai/DeepSeek-R1",
        ai_push_enabled=False,
        ai_push_summary_only=True,
    )


def test_build_snapshot_prompt_contains_symbols_and_alerts():
    snapshot = {
        "summary_text": "当前共观察 2 个品种。",
        "alert_text": "贵金属提醒：先盯点差和美元方向。",
        "market_text": "黄金优先看非农和 CPI。",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_text": "4759.82",
                "quote_text": "Bid 4759.74 | Ask 4759.91 | 点差 17点",
                "status_text": "实时报价",
                "macro_focus": "重点看非农、CPI 和联储。",
                "execution_note": "点差稳定，可继续观察。",
                "risk_reward_ready": True,
                "risk_reward_ratio": 2.1,
                "risk_reward_stop_price": 4748.0,
                "risk_reward_target_price": 4788.0,
                "risk_reward_target_price_2": 4810.0,
                "risk_reward_entry_zone_text": "观察进场区间 4760.00 - 4770.00，若价格直接远离该区间，就不建议追。",
                "risk_reward_position_text": "可轻仓试仓，优先分两段止盈。",
                "risk_reward_invalidation_text": "若价格重新跌回 4748.00 下方，结构视为失效。",
                "external_bias_note": "资讯流：Fed Feed《Powell stays hawkish as yields rise》对 XAUUSD 当前更偏空",
            }
        ],
    }
    prompt = build_snapshot_prompt(
        snapshot,
        rulebook={
            "active_rules_text": "- [entry] 回调至关键支撑位企稳后介入（样本 8，成功率 63%，评分 32.0）",
            "candidate_rules_text": "- [trend] 不追第一次突破，优先等回踩确认（样本 4，成功率 50%，评分 12.0）",
            "rejected_rules_text": "- [directional] 连续冲高时直接追多（样本 6，成功率 17%，评分 -40.0）",
        },
    )
    assert "XAUUSD" in prompt
    assert "贵金属提醒" in prompt
    assert "机器人判定" in prompt
    assert "执行建议" in prompt
    assert "当前有效规则集" in prompt
    assert "暂不采用规则" in prompt
    assert "观察进场区间" in prompt
    assert "外部背景修正" in prompt


def test_full_prompt_assets_are_independent():
    snapshot = {
        "summary_text": "当前优先关注黄金与欧元。",
        "alert_text": "点差正常，等待事件窗口。",
        "market_text": "优先等 CPI 落地后再看黄金方向。",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_text": "4759.82",
                "quote_text": "Bid 4759.74 | Ask 4759.91 | 点差 17点",
                "status_text": "实时报价",
                "macro_focus": "重点看非农、CPI 和联储。",
                "execution_note": "事件前先观察。",
            },
            {
                "symbol": "EURUSD",
                "latest_text": "1.17270",
                "quote_text": "Bid 1.17259 | Ask 1.17280 | 点差 21点",
                "status_text": "实时报价",
                "macro_focus": "重点看欧央行和美元方向。",
                "execution_note": "等待方向更清楚。",
            },
        ],
    }
    advisor_prompt = build_metal_advisor_prompt(snapshot)
    batch_prompt = build_metal_batch_prompt(snapshot)
    assert "贵金属监控铁律" in advisor_prompt
    assert "执行建议" in advisor_prompt
    assert "EURUSD" in advisor_prompt
    assert "对比表" in batch_prompt
    assert "优先观察对象" in batch_prompt


def test_request_ai_brief_requires_api_key():
    try:
        request_ai_brief({}, _build_config(api_key=""))
    except RuntimeError as exc:
        assert "AI_API_KEY" in str(exc)
    else:
        raise AssertionError("未配置 AI_API_KEY 时应抛出异常")


def test_request_ai_brief_parses_response(monkeypatch):
    captured = {}

    def fake_post(url, payload, api_key, timeout=30):
        captured["url"] = url
        return {
            "choices": [
                {
                    "message": {
                        "content": "方向判断：黄金偏强。\n风险点：非农前点差可能放大。\n行动建议：先等回踩确认。"
                    }
                }
            ]
        }

    monkeypatch.setattr("ai_briefing._post_json", fake_post)
    monkeypatch.setattr(
        "ai_briefing.build_rulebook",
        lambda: {
            "summary_text": "当前优先遵守 1 条已验证规则。",
            "active_rules_text": "- [entry] 回调至关键支撑位企稳后介入（样本 8，成功率 63%，评分 32.0）",
            "candidate_rules_text": "- [trend] 等回踩确认",
            "rejected_rules_text": "- [directional] 直接追涨",
        },
    )
    snapshot = {
        "summary_text": "当前共观察 2 个品种。",
        "alert_text": "贵金属提醒：先盯点差和美元方向。",
        "market_text": "黄金优先看非农和 CPI。",
        "items": [],
    }
    result = request_ai_brief(snapshot, _build_config())
    assert "方向判断" in result["content"]
    assert result["model"] == "deepseek-ai/DeepSeek-R1"
    assert captured["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert result["rulebook_summary_text"] == "当前优先遵守 1 条已验证规则。"


def test_request_ai_brief_supports_anthropic_messages_api(monkeypatch):
    captured = {}

    def fake_post(url, payload, headers, timeout=30):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = payload
        return {
            "content": [
                {
                    "type": "text",
                    "text": "当前结论：只适合观察。\n方向判断：先等事件窗口过去。",
                }
            ]
        }

    monkeypatch.setattr("ai_briefing._post_json_with_headers", fake_post)
    monkeypatch.setattr(
        "ai_briefing.build_rulebook",
        lambda: {
            "summary_text": "当前规则库样本仍不足，先以当前快照和风控纪律为主。",
            "active_rules_text": "暂无已验证规则，优先服从当前快照。",
            "candidate_rules_text": "暂无候选规则。",
            "rejected_rules_text": "暂无明确淘汰规则。",
        },
    )
    config = _build_config()
    config.ai_api_base = "https://api.anthropic.com/v1"
    config.ai_model = "claude-3-5-sonnet-20241022"
    result = request_ai_brief({"summary_text": "测试快照", "items": []}, config)
    assert "当前结论" in result["content"]
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"]["model"] == "claude-3-5-sonnet-20241022"
    assert "当前有效规则集" in captured["payload"]["messages"][0]["content"]
