from __future__ import annotations

from external_feed_models import MacroDataItem
from quote_models import SnapshotItem
from signal_enums import AlertTone, EventMode, QuoteStatus


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    """统一卡片层消费的快照项字段契约。"""
    return SnapshotItem.from_payload(item).to_dict()


def _is_inactive_quote_item(item: dict | SnapshotItem | None) -> bool:
    normalized = _normalize_snapshot_item(item)
    status_code = str(normalized.get("quote_status_code", "") or "").strip().lower()
    return status_code in {
        QuoteStatus.INACTIVE,
        QuoteStatus.UNKNOWN_SYMBOL,
        QuoteStatus.NOT_SELECTED,
        QuoteStatus.ERROR,
    }


def _normalize_macro_data_item(item: dict | MacroDataItem | None) -> dict:
    """统一卡片层消费的结构化宏观数据条目契约。"""
    return MacroDataItem.from_payload(item).to_dict()


def build_spread_focus_cards(items: list[dict]) -> list[dict]:
    cards = []
    for item in items:
        tone = str(item.get("tone", AlertTone.NEUTRAL.value) or AlertTone.NEUTRAL.value)
        symbol = str(item.get("symbol", "--") or "--")
        if tone == AlertTone.WARNING.value:
            cards.append(
                {
                    "title": f"{symbol} 点差高警戒",
                    "detail": str(item.get("execution_note", "") or "当前点差明显放大，先暂停追单。").strip(),
                    "tone": AlertTone.WARNING.value,
                }
            )
        elif tone == AlertTone.ACCENT.value:
            cards.append(
                {
                    "title": f"{symbol} 点差偏宽",
                    "detail": str(item.get("execution_note", "") or "当前点差偏宽，先等报价回落。").strip(),
                    "tone": AlertTone.ACCENT.value,
                }
            )
        elif _is_inactive_quote_item(item):
            cards.append(
                {
                    "title": f"{symbol} 暂无活跃报价",
                    "detail": "当前品种休市或流动性不足，先以观察为主，不做临场追单判断。",
                    "tone": AlertTone.NEUTRAL.value,
                }
            )

    if not cards:
        cards.append(
            {
                "title": "点差状态稳定",
                "detail": "当前观察池没有出现明显的点差异常，可继续盯关键位、美元方向和事件窗口。",
                "tone": AlertTone.SUCCESS.value,
            }
        )
    return cards[:3]


def build_event_window_cards(symbols: list[str], event_context: dict | None = None) -> list[dict]:
    cards = []
    context = event_context or {}
    context_reason = str(context.get("reason", "") or "").strip()
    context_mode_text = str(context.get("mode_text", "") or "").strip()
    context_source_text = str(context.get("source_text", "") or "").strip()
    next_event_name = str(context.get("next_event_name", "") or "").strip()
    next_event_time_text = str(context.get("next_event_time_text", "") or "").strip()
    feed_status_text = str(context.get("feed_status_text", "") or "").strip()
    context_mode = str(context.get("mode", EventMode.NORMAL.value) or EventMode.NORMAL.value).strip().lower()
    should_show_context_card = (
        bool(context.get("auto_enabled"))
        or context_mode != EventMode.NORMAL.value
        or bool(str(context.get("active_event_name", "") or "").strip())
    )
    if should_show_context_card and (context_mode_text or context_reason):
        detail_parts = []
        if context_reason:
            detail_parts.append(context_reason)
        if feed_status_text:
            detail_parts.append(f"事件源状态：{feed_status_text}")
        if next_event_name and next_event_time_text and not bool(str(context.get("active_event_name", "") or "").strip()):
            detail_parts.append(f"下一个已登记事件：{next_event_name}（{next_event_time_text}）。")
        cards.append(
            {
                "title": f"纪律模式：{context_mode_text or '正常观察'}{f'（{context_source_text}）' if context_source_text else ''}",
                "detail": " ".join(detail_parts).strip() or "当前暂无额外事件纪律说明。",
                "tone": (
                    AlertTone.WARNING.value
                    if context_mode == EventMode.PRE_EVENT.value
                    else (
                        AlertTone.ACCENT.value
                        if context_mode == EventMode.POST_EVENT.value
                        else AlertTone.NEUTRAL.value
                    )
                ),
            }
        )

    normalized = [str(item or "").strip().upper() for item in symbols or [] if str(item or "").strip()]
    if "XAUUSD" in normalized or "XAGUSD" in normalized:
        cards.append(
            {
                "title": "黄金 / 白银事件窗口",
                "detail": "重点盯非农、CPI、联储讲话与美元指数。事件前后先看点差，再看突破是否站稳。",
                "tone": AlertTone.WARNING.value,
            }
        )
    if "EURUSD" in normalized:
        cards.append(
            {
                "title": "EURUSD 观察重点",
                "detail": "先看联储和欧央行口径差，再看美元强弱。消息前后第一脚波动容易是假突破。",
                "tone": AlertTone.ACCENT.value,
            }
        )
    if "USDJPY" in normalized:
        cards.append(
            {
                "title": "USDJPY 观察重点",
                "detail": "先盯日央行表态、美债收益率和美元方向。急拉急杀后优先等二次确认。",
                "tone": AlertTone.ACCENT.value,
            }
        )

    if not cards:
        cards.append(
            {
                "title": "事件窗口提醒",
                "detail": "当前面板展示的是结构性提醒，不是实时经济日历；实战时仍要结合当日事件表确认。",
                "tone": AlertTone.NEUTRAL.value,
            }
        )
    else:
        cards.append(
            {
                "title": "使用说明",
                "detail": "当前面板给的是结构性提醒，不等同于实时经济日历。真正动手前，仍要复核当日数据时间。",
                "tone": AlertTone.NEUTRAL.value,
            }
        )
    return cards[:3]


def build_alert_status_cards(items: list[dict], transitions: list[dict] | None = None) -> list[dict]:
    ranked = sorted(
        list(items or []),
        key=lambda item: (
            -int(item.get("alert_state_rank", 0) or 0),
            str(item.get("symbol", "") or "").strip(),
        ),
    )
    cards = []
    timeline = list(transitions or [])
    if timeline:
        detail_parts = []
        warning_states = {"异常", "事件前"}
        for item in timeline[:3]:
            changed_at = str(item.get("changed_at", "") or "").strip()
            symbol = str(item.get("symbol", "--") or "--").strip()
            from_state = str(item.get("from_state", "") or "").strip()
            to_state = str(item.get("to_state", "") or "").strip()
            if not symbol or not to_state:
                continue
            prefix = f"{changed_at} " if changed_at else ""
            detail_parts.append(f"{prefix}{symbol}：{from_state} -> {to_state}")
        if detail_parts:
            cards.append(
                {
                    "title": "最近30分钟状态迁移",
                    "detail": "；".join(detail_parts),
                    "tone": (
                        AlertTone.WARNING.value
                        if any(any(marker in str(item.get("to_state", "") or "") for marker in warning_states) for item in timeline[:3])
                        else AlertTone.ACCENT.value
                    ),
                }
            )

    for item in ranked:
        rank = int(item.get("alert_state_rank", 0) or 0)
        if rank <= 1:
            continue
        title = str(item.get("alert_state_text", "") or "").strip()
        detail = str(item.get("alert_state_detail", "") or "").strip()
        transition_text = str(item.get("alert_state_transition_text", "") or "").strip()
        symbol = str(item.get("symbol", "--") or "--").strip()
        if not title or not detail:
            continue
        if transition_text:
            detail = f"状态迁移：{transition_text}。 {detail}"
        cards.append(
            {
                "title": f"{symbol} {title}",
                "detail": detail,
                "tone": str(item.get("alert_state_tone", AlertTone.NEUTRAL.value) or AlertTone.NEUTRAL.value).strip(),
            }
        )

    if not cards:
        cards.append(
            {
                "title": "提醒状态稳定",
                "detail": "当前观察池没有处于异常进行中或恢复跟踪中的品种，可继续看结构和事件窗口。",
                "tone": AlertTone.SUCCESS.value,
            }
        )
    return cards[:3]


def build_runtime_status_cards(
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
            "tone": AlertTone.SUCCESS.value,
        }
    else:
        first_card = {
            "title": "MT5 终端未连通",
            "detail": (
                f"{status_detail or '当前无法连接 MT5 终端。'} "
                "先确认客户端已启动、账号已登录、路径和服务器配置正确。"
            ).strip(),
            "tone": AlertTone.NEGATIVE.value,
        }

    inactive_symbols = []
    for item in items:
        symbol = str(item.get("symbol", "") or "").strip()
        if not symbol:
            continue
        if _is_inactive_quote_item(item):
            inactive_symbols.append(symbol)

    if not connected:
        second_card = {
            "title": "等待连接后再判断时段",
            "detail": "终端恢复后，系统会继续区分休市、流动性偏弱和点差异常，不会把静态报价误判成可执行机会。",
            "tone": AlertTone.NEUTRAL.value,
        }
    elif inactive_count >= watch_count > 0:
        second_card = {
            "title": "当前观察池暂无活跃报价",
            "detail": "观察池内品种当前都处于休市或流动性不足阶段，更适合等待下一个活跃时段，不要拿静态报价做临场判断。",
            "tone": AlertTone.WARNING.value,
        }
    elif inactive_symbols:
        second_card = {
            "title": "休市 / 暂停提醒",
            "detail": (
                f"{'、'.join(inactive_symbols)} 当前休市或流动性不足。"
                "先盯有活跃报价的品种，事件窗口前后也别拿静态报价追单。"
            ),
            "tone": AlertTone.ACCENT.value,
        }
    else:
        second_card = {
            "title": "市场活跃度正常",
            "detail": "当前观察池都有活跃报价，可继续盯点差、美元方向和事件窗口，再结合关键位做观察。",
            "tone": AlertTone.SUCCESS.value,
        }

    return [first_card, second_card]


def build_macro_data_status_card(
    macro_data_status_text: str,
    macro_data_items: list[dict | MacroDataItem] | None = None,
) -> list[dict]:
    """生成宏观数据刷新状态卡片，展示 FRED/BLS/Treasury 数据同步结果。"""
    items = [_normalize_macro_data_item(item) for item in list(macro_data_items or [])]
    status = str(macro_data_status_text or "").strip()

    if not status or "未开启" in status or "未配置" in status:
        return [
            {
                "title": "宏观数据层",
                "detail": status or "结构化宏观数据层未开启，可在设置中配置 FRED/BLS 等数据源规格。",
                "tone": AlertTone.NEUTRAL.value,
            }
        ]

    # 根据状态词判断首卡样式
    if "拉取失败" in status or "error" in status.lower():
        tone = AlertTone.WARNING.value
    elif "缓存" in status or "stale" in status.lower():
        tone = AlertTone.ACCENT.value
    else:
        tone = AlertTone.SUCCESS.value

    cards = [{"title": "宏观数据同步状态", "detail": status, "tone": tone}]

    # 展示前 2 条最高优先级指标
    for item in items[:2]:
        name = str(item.get("name", "") or "").strip()
        value_text = str(item.get("value_text", "--") or "--").strip()
        delta_text = str(item.get("delta_text", "") or "").strip()
        direction = str(item.get("direction", "neutral") or "neutral").strip()
        bias_text = str(item.get("bias_text", "") or "").strip()
        if not name:
            continue
        detail = f"当前值 {value_text}，{delta_text}。{bias_text}"
        item_tone = AlertTone.ACCENT.value if direction in {"bullish", "bearish"} else AlertTone.NEUTRAL.value
        cards.append({"title": name, "detail": detail.strip(), "tone": item_tone})

    return cards[:3]
