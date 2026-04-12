from datetime import datetime

from app_config import EVENT_RISK_MODES
from macro_focus import build_global_market_focus, build_symbol_macro_focus
from mt5_gateway import fetch_quotes, initialize_connection


def _format_quote_price(value: float, point: float = 0.0) -> str:
    decimals = 2
    point_value = max(float(point or 0.0), 0.0)
    if point_value > 0:
        point_text = f"{point_value:.10f}".rstrip("0").rstrip(".")
        if "." in point_text:
            decimals = max(2, min(6, len(point_text.split(".")[1])))
    return f"{float(value or 0.0):.{decimals}f}"


def _get_quote_risk_thresholds(symbol: str) -> dict[str, float]:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith("XAU"):
        return {"warn_points": 45.0, "alert_points": 70.0, "warn_pct": 0.018, "alert_pct": 0.030}
    if symbol_key.startswith("XAG"):
        return {"warn_points": 80.0, "alert_points": 120.0, "warn_pct": 0.040, "alert_pct": 0.065}
    return {"warn_points": 25.0, "alert_points": 40.0, "warn_pct": 0.020, "alert_pct": 0.035}


def _symbol_family(symbol: str) -> str:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith(("XAU", "XAG")):
        return "metal"
    return "fx"


def build_quote_structure_text(row: dict) -> str:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    point = float(row.get("point", 0.0) or 0.0)
    if bid <= 0 or ask <= 0 or ask < bid:
        return "暂无有效 Bid / Ask 报价"
    reference_price = float(row.get("latest_price", 0.0) or 0.0) or ((bid + ask) / 2.0)
    spread_price = max(ask - bid, 0.0)
    spread_points = float(row.get("spread_points", 0.0) or 0.0)
    if spread_points <= 0 and point > 0:
        spread_points = spread_price / point
    spread_pct = (spread_price / reference_price * 100.0) if reference_price > 0 else 0.0
    return (
        f"Bid {_format_quote_price(bid, point)} | "
        f"Ask {_format_quote_price(ask, point)} | "
        f"点差 {spread_points:.0f}点 / {_format_quote_price(spread_price, point)} ({spread_pct:.3f}%)"
    )


def build_quote_risk_note(symbol: str, row: dict) -> tuple[str, str]:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    point = float(row.get("point", 0.0) or 0.0)
    latest = float(row.get("latest_price", 0.0) or 0.0)
    if bid <= 0 or ask <= 0 or ask < bid:
        return "neutral", "当前暂无完整报价，先确认 MT5 终端和品种报价状态。"

    spread_price = max(ask - bid, 0.0)
    spread_points = float(row.get("spread_points", 0.0) or 0.0)
    if spread_points <= 0 and point > 0:
        spread_points = spread_price / point
    spread_pct = (spread_price / latest * 100.0) if latest > 0 else 0.0
    thresholds = _get_quote_risk_thresholds(symbol)
    spread_text = _format_quote_price(spread_price, point)
    if spread_points >= thresholds["alert_points"] or spread_pct >= thresholds["alert_pct"]:
        return "warning", f"点差明显放大（{spread_points:.0f}点 / {spread_text}），先等报价收敛再考虑追单。"
    if spread_points >= thresholds["warn_points"] or spread_pct >= thresholds["warn_pct"]:
        return "accent", f"点差偏宽（{spread_points:.0f}点 / {spread_text}），顺势单也先等点差回落再跟。"
    return "success", f"报价相对平稳（点差 {spread_points:.0f}点 / {spread_text}），适合继续观察关键位。"


def _build_event_mode_adjustment(event_risk_mode: str, family: str) -> dict[str, str] | None:
    mode = str(event_risk_mode or "normal").strip().lower()
    if mode == "pre_event":
        return {
            "grade": "等待事件落地",
            "detail": "当前处于事件前高敏阶段，第一脚波动和点差都更容易失真，先别抢。",
            "next_review": "等事件公布后 15 分钟，并确认点差明显收敛后再复核。",
            "tone": "warning",
        }
    if mode == "post_event":
        return {
            "grade": "只适合观察",
            "detail": "事件刚落地，方向还在重新定价阶段，先等波动和报价稳定下来。",
            "next_review": "建议 10-15 分钟后再复核方向、点差和关键位。",
            "tone": "accent",
        }
    if mode == "illiquid":
        return {
            "grade": "当前不宜出手",
            "detail": "当前人为标记为流动性偏弱阶段，点差和执行成本都不适合普通用户硬做。",
            "next_review": "等进入正常观察模式后再复核。",
            "tone": "warning",
        }
    return None


def build_trade_grade(symbol: str, row: dict, tone: str, connected: bool, event_risk_mode: str = "normal") -> dict[str, str]:
    symbol_key = str(symbol or "").strip().upper()
    family = _symbol_family(symbol_key)
    status_text = str(row.get("status", "") or "").strip()
    has_live_quote = bool(row.get("has_live_quote", False))

    if not connected:
        return {
            "grade": "当前不宜出手",
            "detail": "MT5 终端当前未连通，先恢复报价链路，再讨论任何入场时机。",
            "next_review": "先恢复终端连接后立即复核。",
            "tone": "warning",
        }
    if not has_live_quote or "休市" in status_text or "暂无" in status_text:
        return {
            "grade": "当前不宜出手",
            "detail": f"{symbol_key} 当前没有活跃报价，静态价格不适合做临场判断。",
            "next_review": "等待下一个活跃时段或 MT5 报价恢复后再看。",
            "tone": "warning",
        }
    event_adjustment = _build_event_mode_adjustment(event_risk_mode, family)
    if event_adjustment is not None:
        return event_adjustment
    if tone == "warning":
        return {
            "grade": "当前不宜出手",
            "detail": "点差已经明显放大，执行成本偏高，强行追单很容易被反向扫掉。",
            "next_review": "至少等点差回到正常区间后再复核。",
            "tone": "warning",
        }
    if tone == "accent":
        if family == "metal":
            return {
                "grade": "只适合观察",
                "detail": "报价还在，但点差已经偏宽，黄金/白银这时候容易出现假动作，先别急着伸手。",
                "next_review": "建议 10-15 分钟后复核一次点差和报价节奏。",
                "tone": "accent",
            }
        return {
            "grade": "等待事件落地",
            "detail": "外汇品种本来就更吃消息和美元方向，点差又在变宽，先等波动收敛再判断更稳。",
            "next_review": "先等 15 分钟后或消息波动落地后再复核。",
            "tone": "accent",
        }
    if family == "metal":
        return {
            "grade": "可轻仓试仓",
            "detail": "执行层面当前较干净，点差稳定、报价活跃，可以把它视作候选机会，但仍要配合 MT5 图表确认关键位。",
            "next_review": "如果准备出手，建议先以轻仓试探，并在 10-15 分钟内复核节奏。",
            "tone": "success",
        }
    return {
        "grade": "只适合观察",
        "detail": "外汇报价虽然稳定，但更容易受央行和美元方向扰动，普通用户先观察会更稳。",
        "next_review": "建议等美元方向更清楚或下一轮复核后再决定。",
        "tone": "neutral",
    }


def build_portfolio_trade_grade(items: list[dict], connected: bool, event_risk_mode: str = "normal") -> dict[str, str]:
    mode = str(event_risk_mode or "normal").strip().lower()
    if not connected:
        return {
            "grade": "当前不宜出手",
            "detail": "MT5 连接尚未稳定，当前只能做状态检查，不适合做任何临场执行判断。",
            "next_review": "先恢复终端连接后立即复核。",
            "tone": "warning",
        }

    item_grades = list(items or [])
    if not item_grades:
        return {
            "grade": "当前不宜出手",
            "detail": "观察池还没有有效快照，先等第一轮报价回来。",
            "next_review": "等到至少 1 个品种出现活跃报价后再复核。",
            "tone": "warning",
        }

    if mode == "pre_event":
        return {
            "grade": "等待事件落地",
            "detail": "当前被标记为事件前高敏阶段，整个观察池都应先防假突破和点差放大，不抢第一脚。",
            "next_review": "等事件落地后 15 分钟，并确认点差回到正常区间后再看。",
            "tone": "warning",
        }
    if mode == "post_event":
        return {
            "grade": "只适合观察",
            "detail": "当前被标记为事件落地观察阶段，方向正在重新定价，先观察再决定更稳。",
            "next_review": "建议 10-15 分钟后再复核。",
            "tone": "accent",
        }
    if mode == "illiquid":
        return {
            "grade": "当前不宜出手",
            "detail": "当前被标记为流动性偏弱阶段，执行面整体不干净，先不建议主动出手。",
            "next_review": "等回到正常观察模式后再复核。",
            "tone": "warning",
        }

    if any(str(item.get("trade_grade", "") or "") == "当前不宜出手" for item in item_grades):
        risk_symbols = [str(item.get("symbol", "") or "").strip() for item in item_grades if str(item.get("trade_grade", "") or "") == "当前不宜出手"]
        return {
            "grade": "当前不宜出手",
            "detail": f"当前观察池里 {'、'.join(risk_symbols[:3])} 已经触发高风险条件，先把重点放在控制节奏，而不是抢第一脚。",
            "next_review": "等点差回落、报价恢复或休市结束后再看。",
            "tone": "warning",
        }

    if any(str(item.get("trade_grade", "") or "") == "等待事件落地" for item in item_grades):
        event_symbols = [str(item.get("symbol", "") or "").strip() for item in item_grades if str(item.get("trade_grade", "") or "") == "等待事件落地"]
        return {
            "grade": "等待事件落地",
            "detail": f"{'、'.join(event_symbols[:3])} 当前更受宏观和美元方向影响，先等波动落地比强行猜方向更划算。",
            "next_review": "优先在 15 分钟后或事件波动明显收敛后复核。",
            "tone": "accent",
        }

    if any(str(item.get("trade_grade", "") or "") == "可轻仓试仓" for item in item_grades):
        candidate_symbols = [str(item.get("symbol", "") or "").strip() for item in item_grades if str(item.get("trade_grade", "") or "") == "可轻仓试仓"]
        return {
            "grade": "可轻仓试仓",
            "detail": f"{'、'.join(candidate_symbols[:3])} 当前执行面相对干净，可作为候选机会，但仍建议轻仓、短周期复核。",
            "next_review": "建议 10-15 分钟内复核关键位、点差和美元方向。",
            "tone": "success",
        }

    observe_symbols = [str(item.get("symbol", "") or "").strip() for item in item_grades if str(item.get("trade_grade", "") or "") == "只适合观察"]
    return {
        "grade": "只适合观察",
        "detail": f"{'、'.join(observe_symbols[:3]) or '当前观察池'} 还没有形成足够干净的执行环境，先观察更稳。",
        "next_review": "建议下一轮轮询后结合 MT5 图表再评估。",
        "tone": "neutral",
    }


def _build_spread_focus_cards(items: list[dict]) -> list[dict]:
    cards = []
    for item in items:
        tone = str(item.get("tone", "neutral") or "neutral")
        symbol = str(item.get("symbol", "--") or "--")
        status_text = str(item.get("status_text", "") or "").strip()
        if tone == "warning":
            cards.append(
                {
                    "title": f"{symbol} 点差高警戒",
                    "detail": str(item.get("execution_note", "") or "当前点差明显放大，先暂停追单。").strip(),
                    "tone": "warning",
                }
            )
        elif tone == "accent":
            cards.append(
                {
                    "title": f"{symbol} 点差偏宽",
                    "detail": str(item.get("execution_note", "") or "当前点差偏宽，先等报价回落。").strip(),
                    "tone": "accent",
                }
            )
        elif "休市" in status_text:
            cards.append(
                {
                    "title": f"{symbol} 暂无活跃报价",
                    "detail": "当前品种休市或流动性不足，先以观察为主，不做临场追单判断。",
                    "tone": "neutral",
                }
            )
    if not cards:
        cards.append(
            {
                "title": "点差状态稳定",
                "detail": "当前观察池没有出现明显的点差异常，可继续盯关键位、美元方向和事件窗口。",
                "tone": "success",
            }
        )
    return cards[:3]


def _build_event_window_cards(symbols: list[str], event_context: dict | None = None) -> list[dict]:
    cards = []
    context = event_context or {}
    context_reason = str(context.get("reason", "") or "").strip()
    context_mode_text = str(context.get("mode_text", "") or "").strip()
    context_source_text = str(context.get("source_text", "") or "").strip()
    next_event_name = str(context.get("next_event_name", "") or "").strip()
    next_event_time_text = str(context.get("next_event_time_text", "") or "").strip()
    context_mode = str(context.get("mode", "normal") or "normal").strip().lower()
    should_show_context_card = bool(context.get("auto_enabled")) or context_mode != "normal" or bool(str(context.get("active_event_name", "") or "").strip())
    if should_show_context_card and (context_mode_text or context_reason):
        detail_parts = []
        if context_reason:
            detail_parts.append(context_reason)
        if next_event_name and next_event_time_text and not bool(str(context.get("active_event_name", "") or "").strip()):
            detail_parts.append(f"下一个已登记事件：{next_event_name}（{next_event_time_text}）。")
        cards.append(
            {
                "title": f"纪律模式：{context_mode_text or '正常观察'}{f'（{context_source_text}）' if context_source_text else ''}",
                "detail": " ".join(detail_parts).strip() or "当前暂无额外事件纪律说明。",
                "tone": "warning" if context_mode == "pre_event" else ("accent" if context_mode == "post_event" else "neutral"),
            }
        )

    normalized = [str(item or "").strip().upper() for item in symbols or [] if str(item or "").strip()]
    if "XAUUSD" in normalized or "XAGUSD" in normalized:
        cards.append(
            {
                "title": "黄金 / 白银事件窗口",
                "detail": "重点盯非农、CPI、联储讲话与美元指数。事件前后先看点差，再看突破是否站稳。",
                "tone": "warning",
            }
        )
    if "EURUSD" in normalized:
        cards.append(
            {
                "title": "EURUSD 观察重点",
                "detail": "先看联储和欧央行口径差，再看美元强弱。消息前后第一脚波动容易是假突破。",
                "tone": "accent",
            }
        )
    if "USDJPY" in normalized:
        cards.append(
            {
                "title": "USDJPY 观察重点",
                "detail": "先盯日央行表态、美债收益率和美元方向。急拉急杀后优先等二次确认。",
                "tone": "accent",
            }
        )
    if not cards:
        cards.append(
            {
                "title": "事件窗口提醒",
                "detail": "当前面板展示的是结构性提醒，不是实时经济日历；实战时仍要结合当日事件表确认。",
                "tone": "neutral",
            }
        )
    else:
        cards.append(
            {
                "title": "使用说明",
                "detail": "当前面板给的是结构性提醒，不等同于实时经济日历。真正动手前，仍要复核当日数据时间。",
                "tone": "neutral",
            }
        )
    return cards[:3]


def _build_runtime_status_cards(
    connected: bool,
    connection_message: str,
    items: list[dict],
    watch_count: int,
    live_count: int,
    inactive_count: int,
) -> list[dict]:
    status_detail = str(connection_message or "").strip()
    if connected:
        first_card = {
            "title": "MT5 终端已连通",
            "detail": (
                f"{status_detail or '当前可以正常读取 MT5 本地报价。'} "
                f"观察池共 {watch_count} 个品种，当前 {live_count} 个有活跃报价。"
            ).strip(),
            "tone": "success",
        }
    else:
        first_card = {
            "title": "MT5 终端未连通",
            "detail": (
                f"{status_detail or '当前无法连接 MT5 终端。'} "
                "先确认客户端已启动、账号已登录、路径和服务器配置正确。"
            ).strip(),
            "tone": "negative",
        }

    inactive_symbols = []
    for item in items:
        status_text = str(item.get("status_text", "") or "").strip()
        symbol = str(item.get("symbol", "") or "").strip()
        if not symbol:
            continue
        if "休市" in status_text or "暂无" in status_text:
            inactive_symbols.append(symbol)

    if not connected:
        second_card = {
            "title": "等待连接后再判断时段",
            "detail": "终端恢复后，系统会继续区分休市、流动性偏弱和点差异常，不会把静态报价误判成可执行机会。",
            "tone": "neutral",
        }
    elif inactive_count >= watch_count > 0:
        second_card = {
            "title": "当前观察池暂无活跃报价",
            "detail": "观察池内品种当前都处于休市或流动性不足阶段，更适合等待下一个活跃时段，不要拿静态报价做临场判断。",
            "tone": "warning",
        }
    elif inactive_symbols:
        second_card = {
            "title": "休市 / 暂停提醒",
            "detail": (
                f"{'、'.join(inactive_symbols)} 当前休市或流动性不足。"
                "先盯有活跃报价的品种，事件窗口前后也别拿静态报价追单。"
            ),
            "tone": "accent",
        }
    else:
        second_card = {
            "title": "市场活跃度正常",
            "detail": "当前观察池都有活跃报价，可继续盯点差、美元方向和事件窗口，再结合关键位做观察。",
            "tone": "success",
        }

    return [first_card, second_card]


def build_snapshot_from_rows(
    symbols: list[str],
    rows: list[dict],
    connected: bool,
    connection_message: str,
    event_risk_mode: str = "normal",
    event_context: dict | None = None,
) -> dict:
    rows_by_symbol = {str(item.get("symbol", "")).strip().upper(): item for item in rows or []}
    items = []
    live_count = 0
    inactive_count = 0
    for symbol in symbols:
        row = rows_by_symbol.get(str(symbol).strip().upper(), {})
        has_live_quote = bool(row.get("has_live_quote", False))
        if has_live_quote:
            live_count += 1
        else:
            inactive_count += 1
        latest_price = float(row.get("latest_price", 0.0) or 0.0)
        tone, execution_note = build_quote_risk_note(symbol, row)
        trade_grade = build_trade_grade(symbol, row, tone, connected, event_risk_mode=event_risk_mode)
        items.append(
            {
                "symbol": str(symbol).strip().upper(),
                "latest_price": latest_price,
                "spread_points": float(row.get("spread_points", 0.0) or 0.0),
                "point": float(row.get("point", 0.0) or 0.0),
                "has_live_quote": has_live_quote,
                "bid": float(row.get("bid", 0.0) or 0.0),
                "ask": float(row.get("ask", 0.0) or 0.0),
                "tick_time": int(row.get("tick_time", 0) or 0),
                "latest_text": _format_quote_price(latest_price, float(row.get("point", 0.0) or 0.0)) if latest_price > 0 else "--",
                "quote_text": build_quote_structure_text(row),
                "status_text": str(row.get("status", "暂无快照") or "暂无快照"),
                "macro_focus": build_symbol_macro_focus(symbol),
                "execution_note": f"{trade_grade['grade']}：{execution_note}",
                "trade_grade": trade_grade["grade"],
                "trade_grade_detail": trade_grade["detail"],
                "trade_next_review": trade_grade["next_review"],
                "tone": tone,
            }
        )

    market_focus = build_global_market_focus(symbols)
    portfolio_grade = build_portfolio_trade_grade(items, connected, event_risk_mode=event_risk_mode)
    context = event_context or {}
    status_badge = "MT5 已连接" if connected else "MT5 未连接"
    status_tone = "success" if connected else "negative"
    summary_lines = [
        f"当前共观察 {len(symbols)} 个品种，实时报价 {live_count} 个，休市或暂无报价 {inactive_count} 个。",
        f"事件纪律：{EVENT_RISK_MODES.get(str(event_risk_mode or 'normal').strip().lower(), '正常观察')}。",
        f"出手分级：{portfolio_grade['grade']}。{portfolio_grade['detail']}",
        market_focus.get("hint_text", "") or "先看点差、美元方向和宏观事件窗口。",
    ]
    if (bool(context.get("auto_enabled")) or str(event_risk_mode or "normal").strip().lower() != "normal") and str(context.get("reason", "") or "").strip():
        summary_lines.append(f"纪律说明：{str(context.get('reason', '') or '').strip()}")
    if connection_message:
        summary_lines.append(connection_message)

    live_digest = []
    for item in items:
        if item["latest_text"] != "--":
            live_digest.append(f"{item['symbol']} {item['latest_text']}")

    return {
        "status_badge": status_badge,
        "status_tone": status_tone,
        "status_hint": connection_message or "可继续观察点差、关键位和宏观窗口。",
        "summary_text": "\n".join(line for line in summary_lines if str(line).strip()),
        "alert_text": market_focus.get("alert_text", ""),
        "market_text": market_focus.get("market_text", ""),
        "trade_grade": portfolio_grade["grade"],
        "trade_grade_detail": portfolio_grade["detail"],
        "trade_next_review": portfolio_grade["next_review"],
        "trade_grade_tone": portfolio_grade["tone"],
        "event_risk_mode": str(event_risk_mode or "normal").strip().lower(),
        "event_risk_mode_text": EVENT_RISK_MODES.get(str(event_risk_mode or "normal").strip().lower(), "正常观察"),
        "event_risk_mode_source": str(context.get("source", "manual") or "manual").strip(),
        "event_risk_mode_source_text": str(context.get("source_text", "手动模式") or "手动模式").strip(),
        "event_risk_reason": str(context.get("reason", "") or "").strip(),
        "event_active_name": str(context.get("active_event_name", "") or "").strip(),
        "event_active_time_text": str(context.get("active_event_time_text", "") or "").strip(),
        "event_next_name": str(context.get("next_event_name", "") or "").strip(),
        "event_next_time_text": str(context.get("next_event_time_text", "") or "").strip(),
        "items": items,
        "runtime_status_cards": _build_runtime_status_cards(
            connected=connected,
            connection_message=connection_message,
            items=items,
            watch_count=len(symbols),
            live_count=live_count,
            inactive_count=inactive_count,
        ),
        "spread_focus_cards": _build_spread_focus_cards(items),
        "event_window_cards": _build_event_window_cards(symbols, event_context=context),
        "watch_count": len(symbols),
        "live_count": live_count,
        "inactive_count": inactive_count,
        "live_digest": " | ".join(live_digest[:4]) if live_digest else "暂无有效实时报价",
        "last_refresh_text": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def run_monitor_cycle(symbols: list[str], event_risk_mode: str = "normal", event_context: dict | None = None) -> dict:
    connected, connection_message = initialize_connection()
    rows = fetch_quotes(symbols, include_inactive=True) if connected else []
    return build_snapshot_from_rows(symbols, rows, connected, connection_message, event_risk_mode=event_risk_mode, event_context=event_context)
