from __future__ import annotations

from app_config import get_quote_risk_thresholds


def format_quote_price(value: float, point: float = 0.0) -> str:
    decimals = 2
    point_value = max(float(point or 0.0), 0.0)
    if point_value > 0:
        point_text = f"{point_value:.10f}".rstrip("0").rstrip(".")
        if "." in point_text:
            decimals = max(2, min(6, len(point_text.split(".")[1])))
    return f"{float(value or 0.0):.{decimals}f}"


def _symbol_family(symbol: str) -> str:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith(("XAU", "XAG")):
        return "metal"
    return "fx"


def _intraday_context_text(row: dict) -> str:
    return str(row.get("intraday_context_text", "") or "").strip()


def _multi_timeframe_context_text(row: dict) -> str:
    return str(row.get("multi_timeframe_context_text", "") or "").strip()


def _key_level_context_text(row: dict) -> str:
    return str(row.get("key_level_context_text", "") or "").strip()


def _breakout_context_text(row: dict) -> str:
    return str(row.get("breakout_context_text", "") or "").strip()


def _retest_context_text(row: dict) -> str:
    return str(row.get("retest_context_text", "") or "").strip()


def _risk_reward_context_text(row: dict) -> str:
    return str(row.get("risk_reward_context_text", "") or "").strip()


def _normalize_event_importance(value: str) -> str:
    text = str(value or "").strip().lower()
    if text == "high":
        return "high"
    if text == "low":
        return "low"
    return "medium"


def _event_targets_symbol(event_context: dict | None, symbol: str) -> bool:
    context = dict(event_context or {})
    active_name = str(context.get("active_event_name", "") or "").strip()
    if not active_name:
        return True
    targets = {
        str(item or "").strip().upper()
        for item in list(context.get("active_event_symbols", []) or [])
        if str(item or "").strip()
    }
    if not targets:
        return True
    return str(symbol or "").strip().upper() in targets


def _build_event_mode_adjustment(event_risk_mode: str, event_context: dict | None = None, symbol: str = "") -> dict[str, str] | None:
    mode = str(event_risk_mode or "normal").strip().lower()
    context = dict(event_context or {})
    active_name = str(context.get("active_event_name", "") or "").strip()
    active_time_text = str(context.get("active_event_time_text", "") or "").strip()
    importance = _normalize_event_importance(str(context.get("active_event_importance", "") or "").strip())
    importance_text = str(context.get("active_event_importance_text", "") or "").strip() or {
        "high": "高影响",
        "medium": "中影响",
        "low": "低影响",
    }.get(importance, "中影响")
    scope_text = str(context.get("active_event_scope_text", "") or "").strip()

    if mode in {"pre_event", "post_event"} and not _event_targets_symbol(context, symbol):
        return None

    if mode == "pre_event":
        if active_name:
            if importance == "high":
                return {
                    "grade": "当前不宜出手",
                    "detail": (
                        f"{importance_text}窗口：{active_name} 将在 {active_time_text or '稍后'} 落地，"
                        f"{scope_text or '会直接影响当前品种'}，数据前第一脚和点差都更容易失真。"
                    ),
                    "next_review": "至少等事件公布后 15-20 分钟，并确认点差明显收敛后再复核。",
                    "tone": "warning",
                    "source": "event",
                }
            if importance == "low":
                return {
                    "grade": "只适合观察",
                    "detail": (
                        f"{importance_text}窗口：{active_name} 将在 {active_time_text or '稍后'} 落地，"
                        "但短线节奏仍可能被打乱，先观察别抢。"
                    ),
                    "next_review": "等事件落地后 5-10 分钟，再复核短线节奏和点差。",
                    "tone": "accent",
                    "source": "event",
                }
            return {
                "grade": "等待事件落地",
                "detail": (
                    f"{importance_text}窗口：{active_name} 将在 {active_time_text or '稍后'} 落地，"
                    "当前先别抢第一脚波动。"
                ),
                "next_review": "等事件公布后 10-15 分钟，并确认点差开始收敛后再复核。",
                "tone": "warning",
                "source": "event",
            }
        return {
            "grade": "等待事件落地",
            "detail": "当前处于事件前高敏阶段，第一脚波动和点差都更容易失真，先别抢。",
            "next_review": "等事件公布后 15 分钟，并确认点差明显收敛后再复核。",
            "tone": "warning",
            "source": "event",
        }
    if mode == "post_event":
        if active_name:
            if importance == "high":
                return {
                    "grade": "当前不宜出手",
                    "detail": (
                        f"{importance_text}窗口：{active_name} 已在 {active_time_text or '刚才'} 落地，"
                        "市场往往还在重新定价阶段，别急着追第二脚。"
                    ),
                    "next_review": "至少等 15-20 分钟，并确认关键位与点差一起稳定后再复核。",
                    "tone": "warning",
                    "source": "event",
                }
            if importance == "low":
                return {
                    "grade": "只适合观察",
                    "detail": (
                        f"{importance_text}窗口：{active_name} 已在 {active_time_text or '刚才'} 落地，"
                        "但短线还可能有一次回摆，先别急着追。"
                    ),
                    "next_review": "建议 5-10 分钟后再复核方向、点差和关键位。",
                    "tone": "accent",
                    "source": "event",
                }
            return {
                "grade": "只适合观察",
                "detail": (
                    f"{importance_text}窗口：{active_name} 已在 {active_time_text or '刚才'} 落地，"
                    "方向还在重新定价阶段，先观察再决定更稳。"
                ),
                "next_review": "建议 10-15 分钟后再复核方向、点差和关键位。",
                "tone": "accent",
                "source": "event",
            }
        return {
            "grade": "只适合观察",
            "detail": "事件刚落地，方向还在重新定价阶段，先等波动和报价稳定下来。",
            "next_review": "建议 10-15 分钟后再复核方向、点差和关键位。",
            "tone": "accent",
            "source": "event",
        }
    if mode == "illiquid":
        return {
            "grade": "当前不宜出手",
            "detail": "当前人为标记为流动性偏弱阶段，点差和执行成本都不适合普通用户硬做。",
            "next_review": "等进入正常观察模式后再复核。",
            "tone": "warning",
            "source": "event",
        }
    return None


def _build_clean_quote_grade_with_context(symbol_key: str, family: str, row: dict) -> dict[str, str]:
    context_text = _intraday_context_text(row)
    multi_context_text = _multi_timeframe_context_text(row)
    key_level_text = _key_level_context_text(row)
    breakout_text = _breakout_context_text(row)
    retest_text = _retest_context_text(row)
    risk_reward_text = _risk_reward_context_text(row)
    intraday_ready = bool(row.get("intraday_context_ready", False))
    intraday_bias = str(row.get("intraday_bias", "unknown") or "unknown").strip()
    intraday_location = str(row.get("intraday_location", "unknown") or "unknown").strip()
    intraday_volatility = str(row.get("intraday_volatility", "unknown") or "unknown").strip()
    multi_ready = bool(row.get("multi_timeframe_context_ready", False))
    multi_alignment = str(row.get("multi_timeframe_alignment", "unknown") or "unknown").strip()
    multi_bias = str(row.get("multi_timeframe_bias", "unknown") or "unknown").strip()
    key_level_ready = bool(row.get("key_level_ready", False))
    key_level_state = str(row.get("key_level_state", "unknown") or "unknown").strip()
    breakout_ready = bool(row.get("breakout_ready", False))
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
    breakout_direction = str(row.get("breakout_direction", "unknown") or "unknown").strip()
    retest_ready = bool(row.get("retest_ready", False))
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    risk_reward_ready = bool(row.get("risk_reward_ready", False))
    risk_reward_state = str(row.get("risk_reward_state", "unknown") or "unknown").strip()

    bullish_pressure = multi_bias == "bullish" or intraday_bias == "bullish"
    bearish_pressure = multi_bias == "bearish" or intraday_bias == "bearish"

    if retest_ready and retest_state in {"failed_support", "failed_resistance"}:
        detail = retest_text or "突破后的回踩/反抽已经失败，当前更像是假动作。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": "只适合观察",
            "detail": detail,
            "next_review": "至少再等一到两轮 M5 重新站稳关键位后再复核，不要在失败回踩后硬追。",
            "tone": "accent",
        }

    if breakout_ready and breakout_state in {"failed_above", "failed_below"}:
        detail = breakout_text or "刚尝试突破关键位又收回，疑似假突破，先不要追。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": "只适合观察",
            "detail": detail,
            "next_review": "优先等下一到两根 M5 收线确认，别在假突破后第一时间反手硬追。",
            "tone": "accent",
        }

    if breakout_ready and breakout_state in {"pending_above", "pending_below"}:
        detail = breakout_text or "价格正在尝试突破关键位，但当前还没确认。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": "只适合观察",
            "detail": detail,
            "next_review": "至少再看一到两根 M5 收线，确认站稳或失守后再决定。",
            "tone": "accent",
        }

    if key_level_ready and key_level_state in {"near_high", "breakout_above"} and bullish_pressure and breakout_state != "confirmed_above":
        detail = "点差和节奏都不差，但价格已经顶到关键位上沿，直接追多的性价比不高。"
        if key_level_text:
            detail = f"点差和节奏都不差，但{key_level_text}。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": "只适合观察",
            "detail": detail,
            "next_review": "优先等回踩确认或突破后二次站稳，再复核是否还有空间。",
            "tone": "accent",
        }

    if key_level_ready and key_level_state in {"near_low", "breakout_below"} and bearish_pressure and breakout_state != "confirmed_below":
        detail = "点差和节奏都不差，但价格已经压到关键位下沿，直接追空的性价比不高。"
        if key_level_text:
            detail = f"点差和节奏都不差，但{key_level_text}。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": "只适合观察",
            "detail": detail,
            "next_review": "优先等反抽确认或跌破后二次失守，再复核是否还有空间。",
            "tone": "accent",
        }

    if risk_reward_ready and risk_reward_state == "poor":
        detail = risk_reward_text or "当前结构虽然不算差，但这笔盈亏比不划算。"
        return {
            "grade": "只适合观察",
            "detail": detail,
            "next_review": "优先等回踩更深一点，或等目标空间重新拉开后再复核。",
            "tone": "neutral",
        }

    if multi_ready and multi_alignment == "mixed":
        detail = "点差虽然稳定，但多周期方向正在打架，这种环境很容易出现假突破。"
        if multi_context_text:
            detail = f"点差虽然稳定，但{multi_context_text}，这种环境很容易出现假突破。"
        return {
            "grade": "只适合观察",
            "detail": detail,
            "next_review": "建议等 M5 / M15 / H1 至少两档重新同向后再复核。",
            "tone": "neutral",
        }

    if intraday_ready and (intraday_volatility == "low" or intraday_bias == "sideways"):
        detail = "点差虽然稳定，但短线节奏还不够干净，先别为了有报价就硬找机会。"
        if context_text:
            detail = f"点差虽然稳定，但{context_text}，短线边际还不够明显。"
        if multi_context_text and multi_alignment in {"range", "partial"}:
            detail += f" 同时{multi_context_text}。"
        if key_level_text and key_level_state == "mid_range":
            detail += f" {key_level_text}。"
        return {
            "grade": "只适合观察",
            "detail": detail,
            "next_review": "建议 5-10 分钟后再看一次短线节奏和关键位变化。",
            "tone": "neutral",
        }

    if family == "metal":
        detail = "执行层面当前较干净，点差稳定、报价活跃，可以把它视作候选机会，但仍要配合 MT5 图表确认关键位。"
        next_review = "如果准备出手，建议先以轻仓试探，并在 10-15 分钟内复核节奏。"
        if retest_ready and retest_state in {"confirmed_support", "confirmed_resistance"} and multi_ready and multi_alignment == "aligned":
            detail = f"执行层面当前较干净，且{retest_text or '突破后的回踩已经守住'}，同时{multi_context_text or '多周期也在配合'}，可以把它视作更完整的候选机会。"
            next_review = "优先盯突破位/回踩位是否继续守住，5 分钟内复核一次 M5 收线和点差。"
        elif breakout_ready and breakout_state in {"confirmed_above", "confirmed_below"} and multi_ready and multi_alignment == "aligned":
            detail = f"执行层面当前较干净，且{breakout_text or '突破已经确认'}，同时{multi_context_text or '多周期也在配合'}，可以把它视作候选机会，但仍建议等回踩确认。"
            next_review = "优先盯突破位回踩是否守住，5 分钟内复核一次 M5 收线和点差。"
        elif multi_ready and multi_alignment == "aligned" and multi_bias in {"bullish", "bearish"}:
            detail = f"执行层面当前较干净，且{multi_context_text or '多周期已经同向'}，可以把它视作候选机会，但仍要等回踩或二次确认。"
            next_review = "优先等 M5 回踩或二次确认，5-10 分钟内复核一次多周期是否继续同向。"
        elif intraday_ready and intraday_bias in {"bullish", "bearish"}:
            detail = f"执行层面当前较干净，且{context_text or '短线已有方向性'}，可以把它视作候选机会，但仍要等回踩或二次确认。"
            if intraday_location in {"upper", "lower"}:
                next_review = "优先等回踩或二次确认，5-10 分钟内复核一次短线节奏后再决定。"
        if risk_reward_text:
            detail += f" {risk_reward_text}。"
        return {
            "grade": "可轻仓试仓",
            "detail": detail,
            "next_review": next_review,
            "tone": "success",
        }

    detail = "外汇报价虽然稳定，但更容易受央行和美元方向扰动，普通用户先观察会更稳。"
    if retest_ready and retest_state in {"confirmed_support", "confirmed_resistance"} and multi_ready and multi_alignment == "aligned":
        detail = f"外汇报价当前不差，而且{retest_text or '回踩确认已经出现'}，但普通用户仍建议先等美元方向和二次确认。"
    elif breakout_ready and breakout_state in {"confirmed_above", "confirmed_below"} and multi_ready and multi_alignment == "aligned":
        detail = f"外汇报价当前不差，而且{breakout_text or '突破已经确认'}，但普通用户仍建议先等美元方向和二次确认。"
    elif multi_ready and multi_alignment == "aligned" and multi_bias in {"bullish", "bearish"}:
        detail = f"外汇报价当前不差，而且{multi_context_text or '多周期刚形成同向'}，但普通用户仍建议先等美元方向和二次确认。"
    elif intraday_ready and intraday_bias in {"bullish", "bearish"}:
        detail = f"外汇报价当前不差，但{context_text or '短线方向刚形成'}，仍建议先等美元方向和二次确认。"
    if key_level_text and key_level_state == "mid_range":
        detail += f" {key_level_text}。"
    if risk_reward_text and risk_reward_state == "acceptable":
        detail += f" {risk_reward_text}。"
    return {
        "grade": "只适合观察",
        "detail": detail,
        "next_review": "建议等美元方向更清楚或下一轮复核后再决定。",
        "tone": "neutral",
    }


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
        f"Bid {format_quote_price(bid, point)} | "
        f"Ask {format_quote_price(ask, point)} | "
        f"点差 {spread_points:.0f}点 / {format_quote_price(spread_price, point)} ({spread_pct:.3f}%)"
    )


def build_quote_risk_note(symbol: str, row: dict) -> tuple[str, str]:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    point = float(row.get("point", 0.0) or 0.0)
    latest = float(row.get("latest_price", 0.0) or 0.0)
    status_code = str(row.get("quote_status_code", "") or "").strip().lower()
    if status_code in {"inactive", "unknown_symbol", "not_selected", "error"}:
        return "neutral", "当前暂无完整报价，先确认 MT5 终端和品种报价状态。"
    if bid <= 0 or ask <= 0 or ask < bid:
        return "neutral", "当前暂无完整报价，先确认 MT5 终端和品种报价状态。"

    spread_price = max(ask - bid, 0.0)
    spread_points = float(row.get("spread_points", 0.0) or 0.0)
    if spread_points <= 0 and point > 0:
        spread_points = spread_price / point
    spread_pct = (spread_price / latest * 100.0) if latest > 0 else 0.0
    thresholds = get_quote_risk_thresholds(symbol)
    spread_text = format_quote_price(spread_price, point)

    if spread_points >= thresholds["alert_points"] or spread_pct >= thresholds["alert_pct"]:
        return "warning", f"点差明显放大（{spread_points:.0f}点 / {spread_text}），先等报价收敛再考虑追单。"
    if spread_points >= thresholds["warn_points"] or spread_pct >= thresholds["warn_pct"]:
        return "accent", f"点差偏宽（{spread_points:.0f}点 / {spread_text}），顺势单也先等点差回落再跟。"
    return "success", f"报价相对平稳（点差 {spread_points:.0f}点 / {spread_text}），适合继续观察关键位。"

def build_trade_grade(
    symbol: str,
    row: dict,
    tone: str,
    connected: bool,
    event_risk_mode: str = "normal",
    event_context: dict | None = None,
) -> dict[str, str]:
    symbol_key = str(symbol or "").strip().upper()
    family = _symbol_family(symbol_key)
    status_code = str(row.get("quote_status_code", "") or "").strip().lower()
    has_live_quote = bool(row.get("has_live_quote", False))

    if not connected:
        return {
            "grade": "当前不宜出手",
            "detail": "MT5 终端当前未连通，先恢复报价链路，再讨论任何入场时机。",
            "next_review": "先恢复终端连接后立即复核。",
            "tone": "warning",
            "source": "connection",
        }
    if not has_live_quote or status_code in {"inactive", "unknown_symbol", "not_selected", "error"}:
        return {
            "grade": "当前不宜出手",
            "detail": f"{symbol_key} 当前没有活跃报价，静态价格不适合做临场判断。",
            "next_review": "等待下一个活跃时段或 MT5 报价恢复后再看。",
            "tone": "warning",
            "source": "inactive",
        }

    event_adjustment = _build_event_mode_adjustment(event_risk_mode, event_context=event_context, symbol=symbol_key)
    if event_adjustment is not None:
        return event_adjustment

    if tone == "warning":
        return {
            "grade": "当前不宜出手",
            "detail": "点差已经明显放大，执行成本偏高，强行追单很容易被反向扫掉。",
            "next_review": "至少等点差回到正常区间后再复核。",
            "tone": "warning",
            "source": "spread",
        }
    if tone == "accent":
        if family == "metal":
            detail = "报价还在，但点差已经偏宽，黄金/白银这时候容易出现假动作，先别急着伸手。"
            context_text = _intraday_context_text(row)
            multi_context_text = _multi_timeframe_context_text(row)
            if multi_context_text:
                detail = f"报价还在，但点差已经偏宽，而且{multi_context_text}，先别急着伸手。"
            elif context_text:
                detail = f"报价还在，但点差已经偏宽，而且{context_text}，先别急着伸手。"
            return {
                "grade": "只适合观察",
                "detail": detail,
                "next_review": "建议 10-15 分钟后复核一次点差和报价节奏。",
                "tone": "accent",
                "source": "spread",
            }
        detail = "外汇品种本来就更吃消息和美元方向，点差又在变宽，先等波动收敛再判断更稳。"
        context_text = _intraday_context_text(row)
        multi_context_text = _multi_timeframe_context_text(row)
        if multi_context_text:
            detail = f"外汇品种本来就更吃消息和美元方向，点差又在变宽，而且{multi_context_text}，先等波动收敛再判断更稳。"
        elif context_text:
            detail = f"外汇品种本来就更吃消息和美元方向，点差又在变宽，而且{context_text}，先等波动收敛再判断更稳。"
        return {
            "grade": "等待事件落地",
            "detail": detail,
            "next_review": "先等 15 分钟后或消息波动落地后再复核。",
            "tone": "accent",
            "source": "spread",
        }
    result = _build_clean_quote_grade_with_context(symbol_key, family, row)
    result.setdefault("source", "structure")
    return result


def _build_portfolio_event_mode_adjustment(
    items: list[dict],
    connected: bool,
    event_risk_mode: str,
    event_context: dict | None = None,
) -> dict[str, str] | None:
    mode = str(event_risk_mode or "normal").strip().lower()
    if not connected:
        return {
            "grade": "当前不宜出手",
            "detail": "MT5 连接尚未稳定，当前只能做状态检查，不适合做任何临场执行判断。",
            "next_review": "先恢复终端连接后立即复核。",
            "tone": "warning",
            "source": "connection",
        }

    item_grades = list(items or [])
    if not item_grades:
        return {
            "grade": "当前不宜出手",
            "detail": "观察池还没有有效快照，先等第一轮报价回来。",
            "next_review": "等到至少 1 个品种出现活跃报价后再复核。",
            "tone": "warning",
            "source": "inactive",
        }

    if mode == "illiquid":
        return {
            "grade": "当前不宜出手",
            "detail": "当前被标记为流动性偏弱阶段，执行面整体不干净，先不建议主动出手。",
            "next_review": "等回到正常观察模式后再复核。",
            "tone": "warning",
            "source": "event",
        }
    if mode not in {"pre_event", "post_event"}:
        return None

    context = dict(event_context or {})
    active_name = str(context.get("active_event_name", "") or "").strip()
    if active_name:
        targets = {
            str(item or "").strip().upper()
            for item in list(context.get("active_event_symbols", []) or [])
            if str(item or "").strip()
        }
        watched = {
            str(item.get("symbol", "") or "").strip().upper()
            for item in item_grades
            if str(item.get("symbol", "") or "").strip()
        }
        if targets and watched and not watched.issubset(targets):
            return None

    importance = _normalize_event_importance(str(context.get("active_event_importance", "") or "").strip())
    importance_text = str(context.get("active_event_importance_text", "") or "").strip() or {
        "high": "高影响",
        "medium": "中影响",
        "low": "低影响",
    }.get(importance, "中影响")

    if mode == "pre_event":
        if active_name and importance == "high":
            return {
                "grade": "当前不宜出手",
                "detail": f"{active_name} 属于{importance_text}，并且会直接影响当前观察池，先别抢数据前第一脚。",
                "next_review": "至少等事件公布后 15-20 分钟，并确认点差回到正常区间后再看。",
                "tone": "warning",
                "source": "event",
            }
        if active_name and importance == "low":
            return {
                "grade": "只适合观察",
                "detail": f"{active_name} 虽然只是{importance_text}，但当前仍在事件前窗口，先观察更稳。",
                "next_review": "等事件落地后 5-10 分钟，再复核短线节奏和点差。",
                "tone": "accent",
                "source": "event",
            }
        return {
            "grade": "等待事件落地",
            "detail": "当前被标记为事件前高敏阶段，整个观察池都应先防假突破和点差放大，不抢第一脚。",
            "next_review": "等事件落地后 10-15 分钟，并确认点差回到正常区间后再看。",
            "tone": "warning",
            "source": "event",
        }

    if active_name and importance == "high":
        return {
            "grade": "当前不宜出手",
            "detail": f"{active_name} 刚落地且属于{importance_text}，当前观察池更适合先等重新定价完成。",
            "next_review": "至少等 15-20 分钟，并确认关键位与点差一起稳定后再复核。",
            "tone": "warning",
            "source": "event",
        }
    return {
        "grade": "只适合观察",
        "detail": "当前被标记为事件落地观察阶段，方向正在重新定价，先观察再决定更稳。",
        "next_review": "建议 10-15 分钟后再复核。",
        "tone": "accent",
        "source": "event",
    }


def build_portfolio_trade_grade(
    items: list[dict],
    connected: bool,
    event_risk_mode: str = "normal",
    event_context: dict | None = None,
) -> dict[str, str]:
    portfolio_event_adjustment = _build_portfolio_event_mode_adjustment(
        items,
        connected,
        event_risk_mode=event_risk_mode,
        event_context=event_context,
    )
    if portfolio_event_adjustment is not None:
        return portfolio_event_adjustment

    item_grades = list(items or [])
    hard_blockers = [
        item
        for item in item_grades
        if str(item.get("trade_grade", "") or "") == "当前不宜出手"
        and str(item.get("trade_grade_source", item.get("source", "")) or "").strip() != "event"
    ]
    if hard_blockers:
        risk_symbols = [
            str(item.get("symbol", "") or "").strip()
            for item in hard_blockers
        ]
        return {
            "grade": "当前不宜出手",
            "detail": f"当前观察池里 {'、'.join(risk_symbols[:3])} 已经触发高风险条件，先把重点放在控制节奏，而不是抢第一脚。",
            "next_review": "等点差回落、报价恢复或休市结束后再看。",
            "tone": "warning",
            "source": "risk",
        }

    if any(str(item.get("trade_grade", "") or "") == "等待事件落地" for item in item_grades):
        event_symbols = [
            str(item.get("symbol", "") or "").strip()
            for item in item_grades
            if str(item.get("trade_grade", "") or "") == "等待事件落地"
        ]
        return {
            "grade": "等待事件落地",
            "detail": f"{'、'.join(event_symbols[:3])} 当前更受宏观和美元方向影响，先等波动落地比强行猜方向更划算。",
            "next_review": "优先在 15 分钟后或事件波动明显收敛后复核。",
            "tone": "accent",
            "source": "event",
        }

    if any(str(item.get("trade_grade", "") or "") == "可轻仓试仓" for item in item_grades):
        candidate_symbols = [
            str(item.get("symbol", "") or "").strip()
            for item in item_grades
            if str(item.get("trade_grade", "") or "") == "可轻仓试仓"
        ]
        event_symbols = [
            str(item.get("symbol", "") or "").strip()
            for item in item_grades
            if str(item.get("trade_grade_source", item.get("source", "")) or "").strip() == "event"
        ]
        detail = f"{'、'.join(candidate_symbols[:3])} 当前执行面相对干净，可作为候选机会，但仍建议轻仓、短周期复核。"
        if event_symbols:
            detail += f" 同时 {'、'.join(event_symbols[:2])} 仍在事件窗口内，别被它们的节奏带着走。"
        return {
            "grade": "可轻仓试仓",
            "detail": detail,
            "next_review": "建议 10-15 分钟内复核关键位、点差和美元方向。",
            "tone": "success",
            "source": "setup",
        }

    event_blockers = [
        str(item.get("symbol", "") or "").strip()
        for item in item_grades
        if str(item.get("trade_grade_source", item.get("source", "")) or "").strip() == "event"
    ]
    if event_blockers:
        return {
            "grade": "只适合观察",
            "detail": f"{'、'.join(event_blockers[:3])} 当前主要受事件窗口约束，先观察、等节奏重新稳定更稳。",
            "next_review": "建议事件波动收敛后再结合关键位复核。",
            "tone": "accent",
            "source": "event",
        }

    observe_symbols = [
        str(item.get("symbol", "") or "").strip()
        for item in item_grades
        if str(item.get("trade_grade", "") or "") == "只适合观察"
    ]
    return {
        "grade": "只适合观察",
        "detail": f"{'、'.join(observe_symbols[:3]) or '当前观察池'} 还没有形成足够干净的执行环境，先观察更稳。",
        "next_review": "建议下一轮轮询后结合 MT5 图表再评估。",
        "tone": "neutral",
        "source": "structure",
    }
