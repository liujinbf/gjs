"""
市场环境分类器：为每个品种给出当前所处的轻量行情环境标签。

第一版目标：
1. 保持可解释，不引入黑箱模型；
2. 直接复用现有 ATR、点差、事件窗口、多周期和突破字段；
3. 为知识库、规则书和 AI 提示词提供可统计的 regime 标签。
"""
from __future__ import annotations


def _text(value: object) -> str:
    return str(value or "").strip().lower()


def _symbol_atr_threshold_pct(symbol: str) -> float:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith("XAU"):
        return 0.35
    if symbol_key.startswith("XAG"):
        return 0.65
    return 0.18


def _spread_warning_threshold(symbol: str) -> float:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith("XAU"):
        return 80.0
    if symbol_key.startswith("XAG"):
        return 120.0
    return 28.0


def classify_market_regime(symbol: str, row: dict, tone: str, event_meta: dict | None = None) -> dict:
    symbol_key = str(symbol or "").strip().upper()
    event_meta = dict(event_meta or {})
    has_live_quote = bool(row.get("has_live_quote", False))
    latest_price = float(row.get("latest_price", 0.0) or 0.0)
    spread_points = float(row.get("spread_points", 0.0) or 0.0)
    atr14 = float(row.get("atr14", 0.0) or 0.0)
    atr_pct = (atr14 / latest_price * 100.0) if latest_price > 0 and atr14 > 0 else 0.0

    intraday_volatility = _text(row.get("intraday_volatility", ""))
    intraday_bias = _text(row.get("intraday_bias", ""))
    intraday_location = _text(row.get("intraday_location", ""))
    alignment = _text(row.get("multi_timeframe_alignment", ""))
    multi_bias = _text(row.get("multi_timeframe_bias", ""))
    breakout_state = _text(row.get("breakout_state", ""))
    retest_state = _text(row.get("retest_state", ""))
    event_applies = bool(event_meta.get("event_applies", False))
    event_importance_text = str(event_meta.get("event_importance_text", "") or "").strip()
    event_name = str(event_meta.get("event_active_name", "") or "").strip()

    if not has_live_quote:
        return {
            "regime_tag": "inactive",
            "regime_text": "休市/无报价",
            "regime_reason": f"{symbol_key} 当前没有活跃报价，先不做环境判断。",
            "regime_rank": 1,
        }

    if event_applies and event_name:
        return {
            "regime_tag": "event_driven",
            "regime_text": "事件驱动",
            "regime_reason": f"{event_importance_text or '事件'}窗口主导，{event_name} 正在影响当前定价。",
            "regime_rank": 6,
        }

    if tone == "warning" or spread_points >= _spread_warning_threshold(symbol_key):
        return {
            "regime_tag": "liquidity_risk",
            "regime_text": "流动性脆弱",
            "regime_reason": f"当前点差约 {spread_points:.0f} 点，流动性偏差，容易放大滑点与假动作。",
            "regime_rank": 5,
        }

    if (
        alignment == "aligned"
        and multi_bias in {"bullish", "bearish"}
        and intraday_bias in {"bullish", "bearish"}
        and intraday_bias == multi_bias
        and intraday_volatility not in {"low", "quiet"}
        and breakout_state in {"confirmed_above", "confirmed_below"}
        and retest_state in {"confirmed_support", "confirmed_resistance", ""}
    ):
        direction_text = "偏多" if multi_bias == "bullish" else "偏空"
        return {
            "regime_tag": "trend_expansion",
            "regime_text": "趋势扩张",
            "regime_reason": f"多周期同向{direction_text}，且突破/回踩结构较完整，适合只看顺势机会。",
            "regime_rank": 4,
        }

    if atr_pct >= _symbol_atr_threshold_pct(symbol_key) or intraday_volatility in {"high", "expanded"}:
        return {
            "regime_tag": "high_volatility_repricing",
            "regime_text": "高波重定价",
            "regime_reason": f"ATR 约占现价 {atr_pct:.2f}%，短线波动偏大，先防重新定价扫损。",
            "regime_rank": 4,
        }

    if (
        intraday_volatility in {"low", "quiet"}
        or alignment in {"mixed", "sideways", ""}
        or multi_bias in {"mixed", "sideways", "neutral", ""}
        or intraday_bias in {"sideways", "neutral", ""}
        or intraday_location == "middle"
    ):
        return {
            "regime_tag": "low_volatility_range",
            "regime_text": "低波震荡",
            "regime_reason": "波动偏静或多周期分歧，当前更像区间来回定价，追单性价比偏低。",
            "regime_rank": 3,
        }

    return {
        "regime_tag": "transition_mixed",
        "regime_text": "过渡混合",
        "regime_reason": "结构有苗头，但趋势、波动和关键位还没形成足够统一的环境。",
        "regime_rank": 2,
    }


_REGIME_PRIORITY = {
    "event_driven": 6,
    "liquidity_risk": 5,
    "trend_expansion": 4,
    "high_volatility_repricing": 4,
    "low_volatility_range": 3,
    "transition_mixed": 2,
    "inactive": 1,
}


def build_snapshot_regime_summary(items: list[dict]) -> dict:
    valid_items = [dict(item or {}) for item in list(items or []) if str(item.get("regime_tag", "") or "").strip()]
    if not valid_items:
        return {
            "regime_tag": "",
            "regime_text": "环境未知",
            "regime_summary_text": "当前环境样本不足，先服从点差、事件窗口和结构纪律。",
        }

    counts: dict[str, int] = {}
    exemplar: dict[str, dict] = {}
    for item in valid_items:
        tag = str(item.get("regime_tag", "") or "").strip()
        if not tag:
            continue
        counts[tag] = counts.get(tag, 0) + 1
        exemplar.setdefault(tag, item)

    chosen_tag = max(
        counts.keys(),
        key=lambda key: (int(counts.get(key, 0)), int(_REGIME_PRIORITY.get(key, 0))),
    )
    if "event_driven" in counts:
        chosen_tag = "event_driven"
    elif "liquidity_risk" in counts:
        chosen_tag = "liquidity_risk"

    chosen = exemplar.get(chosen_tag, valid_items[0])
    text = str(chosen.get("regime_text", "") or "").strip() or "环境未知"
    reason = str(chosen.get("regime_reason", "") or "").strip()
    summary = f"当前主导环境：{text}。{reason}" if counts.get(chosen_tag, 0) > 1 else f"当前优先环境：{text}。{reason}"
    return {
        "regime_tag": chosen_tag,
        "regime_text": text,
        "regime_summary_text": summary.strip(),
    }
