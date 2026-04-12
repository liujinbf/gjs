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


def build_global_market_focus(symbols: list[str]) -> dict[str, str]:
    normalized = [normalize_symbol(item) for item in symbols if normalize_symbol(item)]
    has_metal = any(is_precious_metal_symbol(symbol) for symbol in normalized)
    has_fx = any(is_macro_fx_symbol(symbol) for symbol in normalized)

    market_parts = []
    hint_parts = []
    alert_parts = []
    if has_metal:
        market_parts.append("非农/CPI/联储窗口前后优先防点差放大，不追瞬时突破")
        hint_parts.append("黄金/白银先看非农、CPI、联储和美元方向，点差放大时只做提醒不追单")
        alert_parts.append("贵金属提醒：非农、CPI、联储前后先盯点差和美元方向，别追瞬时突破。")
    if has_fx:
        market_parts.append("外汇先盯央行窗口和美元方向，急拉急杀时先等回落")
        hint_parts.append("EURUSD、USDJPY 这类宏观品种更容易假突破，先等事件后波动收敛")
        alert_parts.append("外汇提醒：央行窗口前后先等波动收敛，再确认方向。")

    return {
        "market_text": "；".join(part for part in market_parts if str(part).strip()),
        "hint_text": "；".join(part for part in hint_parts if str(part).strip()),
        "alert_text": " ".join(part for part in alert_parts if str(part).strip()),
    }
