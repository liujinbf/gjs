from datetime import datetime


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()



def is_precious_metal_symbol(symbol: str) -> bool:
    return normalize_symbol(symbol) in {"XAUUSD", "XAGUSD"}


def is_macro_fx_symbol(symbol: str) -> bool:
    return normalize_symbol(symbol) in {"EURUSD", "USDJPY"}


def build_symbol_macro_focus(symbol: str) -> str:
    text = normalize_symbol(symbol)
    if text == "XAUUSD":
        return "重点看非农、CPI、联储与美元方向，点差突然放大时先观察。"
    if text == "XAGUSD":
        return "白银弹性更高，跟黄金方向时也要额外留意波动和点差。"
    if text == "EURUSD":
        return "重点看联储与欧央行窗口，消息前后先防假突破。"
    if text == "USDJPY":
        return "重点看日央行与联储窗口，急拉急杀时先等二次确认。"
    return ""


def build_global_market_focus(symbols: list[str], event_context: dict | None = None) -> dict[str, str]:
    context = dict(event_context or {})
    normalized = [normalize_symbol(item) for item in symbols if normalize_symbol(item)]
    has_metal = any(is_precious_metal_symbol(symbol) for symbol in normalized)
    has_fx = any(is_macro_fx_symbol(symbol) for symbol in normalized)

    # —— 实时事件信息 ——
    mode = str(context.get("mode", "normal") or "normal").strip().lower()
    active_event_name = str(context.get("active_event_name", "") or "").strip()
    active_time_text = str(context.get("active_event_time_text", "") or "").strip()
    active_importance = str(context.get("active_event_importance_text", "") or "").strip()
    next_event_name = str(context.get("next_event_name", "") or "").strip()
    next_time_text = str(context.get("next_event_time_text", "") or "").strip()
    now_text = datetime.now().strftime("%m月%d日 %H:%M")

    # —— 动态事件提示 ——
    live_event_lines = []
    if active_event_name and mode in {"pre_event", "post_event"}:
        if mode == "pre_event":
            live_event_lines.append(
                f"⚡ 高敏窗口：『{active_event_name}』将于 {active_time_text or '稍后'} 落地"
                + (f"（{active_importance}）" if active_importance else "")
                + "，当前先守住止损不新开仓。"
            )
        else:
            live_event_lines.append(
                f"⚡ 事件已落地：『{active_event_name}』于 {active_time_text or '剛才'} 公布"
                + (f"（{active_importance}）" if active_importance else "")
                + "，先等重新定价波动收敛再看方向。"
            )
    elif next_event_name and next_time_text:
        live_event_lines.append(
            f"📅 下个关注事件：『{next_event_name}』（预计 {next_time_text}  公布），接近时间段先控制投机力度。"
        )

    market_parts = []
    hint_parts = []
    alert_parts = []
    if has_metal:
        metal_base = "贵金属：非农/CPI/联储窗口前后优先防点差放大，不追瞬时突破"
        metal_hint = "黄金/白銀先看非农、CPI、联储和美元方向，点差放大时只做提醒不追单"
        market_parts.append(metal_base)
        hint_parts.append(metal_hint)
        metal_alert = "贵金属提醒：非农/CPI/联储前后先盯点差和美元方向，别追瞬时突破。"
        if live_event_lines:
            metal_alert += " " + " ".join(live_event_lines)
        alert_parts.append(metal_alert)
    if has_fx:
        fx_base = "外汇先盯央行窗口和美元方向，急拉急杀时先等回落"
        fx_hint = "EURUSD、USDJPY 这类宏观品种更容易假突破，先等事件后波动收敛"
        market_parts.append(fx_base)
        hint_parts.append(fx_hint)
        fx_alert = "外汇提醒：央行窗口前后先等波动收敛，再确认方向。"
        if live_event_lines:
            fx_alert += " " + " ".join(live_event_lines)
        alert_parts.append(fx_alert)

    market_text_parts = market_parts[:]
    if live_event_lines:
        market_text_parts.extend(live_event_lines)

    return {
        "market_text": "；".join(part for part in market_text_parts if str(part).strip()),
        "hint_text": "；".join(part for part in hint_parts if str(part).strip()),
        "alert_text": " ".join(part for part in alert_parts if str(part).strip()),
    }
