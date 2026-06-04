from __future__ import annotations

from app_config import get_runtime_config, normalize_strategic_gold_plan_levels


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _to_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_strategic_gold_plan_entries(snapshot: dict, config=None) -> list[dict]:
    """生成黄金中长线分批计划提醒；只提醒，不触发自动交易。"""
    cfg = config or get_runtime_config()
    if not bool(getattr(cfg, "strategic_gold_plan_enabled", True)):
        return []

    target_symbol = _normalize_text(getattr(cfg, "strategic_gold_plan_symbol", "XAUUSD")).upper() or "XAUUSD"
    levels = normalize_strategic_gold_plan_levels(getattr(cfg, "strategic_gold_plan_levels", []))
    if not levels:
        return []
    band = max(0.0, _to_float(getattr(cfg, "strategic_gold_plan_band", 15.0)))
    occurred_at = _normalize_text((snapshot or {}).get("last_refresh_text", ""))

    result = []
    for item in list((snapshot or {}).get("items", []) or []):
        symbol = _normalize_text(item.get("symbol", "")).upper()
        if symbol != target_symbol or not bool(item.get("has_live_quote", False)):
            continue
        price = _to_float(item.get("latest_price", 0.0))
        if price <= 0:
            continue
        for index, level in enumerate(levels, start=1):
            if abs(price - float(level)) > band and price > float(level):
                continue
            if price < float(level) - band:
                continue
            detail = (
                f"{symbol} 已进入中长线分批观察带：现价 {price:.2f}，目标层 {float(level):.2f}。"
                "这属于战略配置提醒，不进入短线自动开仓；若执行，应使用现货/低杠杆并独立管理仓位。"
            )
            result.append(
                {
                    "occurred_at": occurred_at,
                    "category": "strategic_plan",
                    "title": f"{symbol} 战略分批做多观察",
                    "detail": detail,
                    "tone": "accent",
                    "signature": f"strategic_gold_plan|{symbol}|{int(float(level))}",
                    "symbol": symbol,
                    "strategic_plan": True,
                    "strategic_plan_side": "long",
                    "strategic_plan_level": float(level),
                    "strategic_plan_band": band,
                    "strategic_plan_step": index,
                    "strategic_plan_total_steps": len(levels),
                    "baseline_latest_price": price,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "strategic_plan",
                    "trade_grade_detail": "中长线分批配置提醒，不允许自动试仓。",
                    "signal_side": "long",
                }
            )
            break
    return result
