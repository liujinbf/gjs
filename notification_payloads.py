"""
消息推送：支持钉钉 Webhook 与 PushPlus。
"""
from __future__ import annotations

from datetime import datetime

from app_config import MetalMonitorConfig


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _format_price(value: float, point: float = 0.0) -> str:
    decimals = 2
    point_value = max(float(point or 0.0), 0.0)
    if point_value > 0:
        point_text = f"{point_value:.10f}".rstrip("0").rstrip(".")
        if "." in point_text:
            decimals = max(2, min(6, len(point_text.split(".")[1])))
    return f"{float(value or 0.0):,.{decimals}f}"


def _clip_text(value: str, limit: int = 68) -> str:
    text = _normalize_text(value)
    if len(text) <= max(12, int(limit)):
        return text
    return text[: max(12, int(limit)) - 1].rstrip() + "…"


def _append_block(lines: list[str], title: str, items: list[str]) -> None:
    payload = [str(item or "").strip() for item in items if str(item or "").strip()]
    if not payload:
        return
    lines.extend(["", f"**{title}**"])
    lines.extend(payload)


def _format_spread_points(value: float) -> str:
    spread = float(value or 0.0)
    if abs(spread - round(spread)) < 1e-6:
        return f"{int(round(spread))}点"
    return f"{spread:.1f}点"


def _build_execution_card_line(entry: dict, price_point: float) -> str:
    parts: list[str] = []
    risk_reward_ratio = entry.get("risk_reward_ratio")
    entry_zone_text = _normalize_text(entry.get("entry_zone_text", ""))
    entry_zone_side_text = _normalize_text(entry.get("entry_zone_side_text", ""))
    stop_loss_price = entry.get("stop_loss_price")
    take_profit_1 = entry.get("take_profit_1")
    take_profit_2 = entry.get("take_profit_2")

    if entry_zone_text:
        zone_label = f"观察{entry_zone_side_text}" if entry_zone_side_text else "观察"
        parts.append(f"{zone_label} {_clip_text(entry_zone_text, 24)}")
    if stop_loss_price:
        try:
            parts.append(f"止损 {_format_price(float(stop_loss_price), price_point)}")
        except (TypeError, ValueError):
            pass
    target_parts = []
    if take_profit_1:
        try:
            target_parts.append(_format_price(float(take_profit_1), price_point))
        except (TypeError, ValueError):
            pass
    if take_profit_2:
        try:
            target_parts.append(_format_price(float(take_profit_2), price_point))
        except (TypeError, ValueError):
            pass
    if target_parts:
        parts.append(f"目标 {' / '.join(target_parts)}")
    if risk_reward_ratio:
        try:
            parts.append(f"盈亏比 1:{float(risk_reward_ratio):.2f}")
        except (TypeError, ValueError):
            pass
    return "；".join(parts[:4])


def _build_structure_decision_lines(entry: dict) -> list[str]:
    lines: list[str] = []
    regime_text = _normalize_text(entry.get("regime_text", ""))
    regime_reason = _normalize_text(entry.get("regime_reason", ""))
    trade_grade_source = _normalize_text(entry.get("trade_grade_source", ""))
    model_ready = bool(entry.get("model_ready", False))
    model_win_probability = entry.get("model_win_probability")
    model_confidence_text = _normalize_text(entry.get("model_confidence_text", ""))
    external_bias_note = _normalize_text(entry.get("external_bias_note", ""))
    event_note = _normalize_text(entry.get("event_note", ""))

    if regime_text:
        regime_line = f"- 环境：{regime_text}"
        if regime_reason:
            regime_line += f"；{_clip_text(regime_reason, 42)}"
        lines.append(regime_line)

    if model_ready and model_win_probability is not None:
        try:
            probability = float(model_win_probability) * 100.0
            model_line = f"- 模型：参考胜率 {probability:.0f}%"
            if model_confidence_text:
                model_line += f"（{model_confidence_text}）"
            if trade_grade_source == "model":
                model_line += "，当前已按模型结果降级"
            elif probability >= 68:
                model_line += "，与当前结构基本一致"
            elif probability < 50:
                model_line += "，延续率偏低，先别急着追"
            lines.append(model_line)
        except (TypeError, ValueError):
            pass

    if external_bias_note:
        lines.append(f"- 外部：{_clip_text(external_bias_note, 50)}")
    elif event_note:
        lines.append(f"- 事件：{_clip_text(event_note, 50)}")

    return lines[:3]


def _build_markdown(entry: dict) -> str:
    title         = _normalize_text(entry.get("title", "贵金属监控提醒"))
    markdown_body = str(entry.get("markdown_body", "") or "").strip()
    detail        = _normalize_text(entry.get("detail", ""))
    occurred_at   = str(entry.get("occurred_at", "--") or "--").strip()
    category      = str(entry.get("category", "general") or "general").strip()
    trade_grade   = _normalize_text(entry.get("trade_grade", ""))
    trade_grade_detail    = _normalize_text(entry.get("trade_grade_detail", ""))
    trade_next_review     = _normalize_text(entry.get("trade_next_review", ""))
    event_mode_text       = _normalize_text(entry.get("event_mode_text", ""))
    event_name            = _normalize_text(entry.get("event_name", ""))
    event_time_text       = _normalize_text(entry.get("event_time_text", ""))
    event_importance_text = _normalize_text(entry.get("event_importance_text", ""))
    event_scope_text      = _normalize_text(entry.get("event_scope_text", ""))
    event_note            = _normalize_text(entry.get("event_note", ""))
    external_bias_note    = _normalize_text(entry.get("external_bias_note", ""))
    model_note            = _normalize_text(entry.get("model_note", ""))
    model_confidence_text = _normalize_text(entry.get("model_confidence_text", ""))
    position_plan_text    = _normalize_text(entry.get("position_plan_text", ""))
    entry_invalidation_text = _normalize_text(entry.get("entry_invalidation_text", ""))
    entry_zone_text       = _normalize_text(entry.get("entry_zone_text", ""))
    aggregate_count       = int(entry.get("aggregate_count", 0) or 0)
    notify_mode_text      = _normalize_text(entry.get("notify_mode_text", ""))
    symbol                = _normalize_text(entry.get("symbol", ""))
    price_point           = float(entry.get("price_point", 0.0) or 0.0)

    change_pct  = entry.get("change_pct_24h")
    latest_price = entry.get("baseline_latest_price")
    bid_price = entry.get("baseline_bid")
    ask_price = entry.get("baseline_ask")
    spread_points = entry.get("baseline_spread_points")
    risk_reward_ratio = entry.get("risk_reward_ratio")
    stop_loss_price = entry.get("stop_loss_price")
    take_profit_1   = entry.get("take_profit_1")
    take_profit_2   = entry.get("take_profit_2")
    model_ready = bool(entry.get("model_ready", False))
    model_win_probability = entry.get("model_win_probability")

    if markdown_body:
        return markdown_body

    # ── 分类 emoji 映射 ──
    CATEGORY_EMOJI = {
        "spread": "⚠️", "recovery": "✅", "structure": "📐",
        "event": "📅", "source": "🛰️", "ai": "🤖", "general": "📊",
    }
    emoji = CATEGORY_EMOJI.get(category, "📊")

    lines = [f"## {emoji}【{title}】", ""]
    headline_lines = [f"- 时间：{occurred_at}"]
    if trade_grade:
        headline_lines.append(f"- 结论：**{trade_grade}**")
    if symbol:
        headline_lines.append(f"- 品种：{symbol}")
    if latest_price:
        try:
            price_line = f"- 价格：{_format_price(float(latest_price), price_point)}"
            if change_pct is not None:
                sign = "+" if float(change_pct) >= 0 else ""
                price_line += f"（24h涨跌 {sign}{change_pct}%）"
            headline_lines.append(price_line)
        except (TypeError, ValueError):
            pass
    if bid_price or ask_price or spread_points:
        quote_parts = []
        try:
            if bid_price and float(bid_price) > 0:
                quote_parts.append(f"Bid {_format_price(float(bid_price), price_point)}")
            if ask_price and float(ask_price) > 0:
                quote_parts.append(f"Ask {_format_price(float(ask_price), price_point)}")
            if spread_points and float(spread_points) > 0:
                quote_parts.append(f"点差 {_format_spread_points(float(spread_points))}")
        except (TypeError, ValueError):
            quote_parts = []
        if quote_parts:
            headline_lines.append(f"- 盘口：{' / '.join(quote_parts[:2])}" + (f" · {quote_parts[2]}" if len(quote_parts) > 2 else ""))
    lines.extend(headline_lines)

    category_key = category.lower()
    quick_lines = []
    if detail:
        quick_lines.append(f"- 内容：{_clip_text(detail, 84 if category_key == 'structure' else 90)}")
    if trade_grade_detail:
        quick_lines.append(f"- 原因：{_clip_text(trade_grade_detail, 78)}")
    if trade_next_review:
        quick_lines.append(f"- 复核：{_clip_text(trade_next_review, 60)}")
    _append_block(lines, "先看这个", quick_lines)

    if category_key == "structure":
        _append_block(lines, "决策速览", _build_structure_decision_lines(entry))
        execution_card_line = _build_execution_card_line(entry, price_point)
        if execution_card_line:
            _append_block(lines, "执行卡片", [f"- {execution_card_line}"])

    action_lines = []
    if category_key == "structure":
        if entry_zone_text:
            action_lines.append(f"- 观察区间：{_clip_text(entry_zone_text, 64)}")
        if stop_loss_price:
            try:
                action_lines.append(f"- 止损：{_format_price(float(stop_loss_price), price_point)}")
            except (TypeError, ValueError):
                pass
        if take_profit_1:
            try:
                action_lines.append(f"- 目标1：{_format_price(float(take_profit_1), price_point)}")
            except (TypeError, ValueError):
                pass
        if take_profit_2:
            try:
                action_lines.append(f"- 目标2：{_format_price(float(take_profit_2), price_point)}")
            except (TypeError, ValueError):
                pass
        if position_plan_text:
            action_lines.append(f"- 仓位：{_clip_text(position_plan_text, 58)}")
        if entry_invalidation_text:
            action_lines.append(f"- 失效：{_clip_text(entry_invalidation_text, 66)}")
    _append_block(lines, "执行参数", action_lines)

    background_lines = []
    if event_name:
        event_parts = [p for p in (event_name, event_time_text, event_importance_text, event_scope_text) if p]
        background_lines.append(f"- 事件：{' | '.join(event_parts)}")
    elif event_mode_text and category_key in {"macro", "spread", "structure"}:
        background_lines.append(f"- 纪律：{event_mode_text}")
    if event_note:
        background_lines.append(f"- 提醒：{_clip_text(event_note, 74)}")
    if external_bias_note:
        background_lines.append(f"- 外部背景：{_clip_text(external_bias_note, 74)}")
    if category_key != "structure" and model_note:
        background_lines.append(f"- 模型参考：{_clip_text(model_note, 74)}")
    _append_block(lines, "背景", background_lines)

    # ── 合并提醒说明 ──
    if aggregate_count > 1:
        lines.extend(["", f"> 同类提醒近一轮累计 **{aggregate_count}** 条，已合并发送"])

    if notify_mode_text:
        lines.extend(["", f"- 推送策略：{notify_mode_text}"])

    lines.append("")
    lines.append("> ⚠️ AI生成内容仅供参考，不构成投资建议，请严格执行止损纪律。")
    return "\n".join(lines)


def _build_ai_brief_entry(result: dict, snapshot: dict, config: MetalMonitorConfig) -> dict:
    items = list((snapshot or {}).get("items", []) or [])
    symbols = [str(item.get("symbol", "") or "").strip().upper() for item in items if str(item.get("symbol", "") or "").strip()]
    title = "AI 研判已生成"
    if symbols:
        title = f"AI 研判：{' / '.join(symbols[:3])}"

    content = str((result or {}).get("content", "") or "").strip()
    if bool(config.ai_push_summary_only):
        for line in content.splitlines():
            text = line.strip()
            if text:
                content = text
                break

    occurred_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    markdown_lines = [
        content or "模型未返回有效结论。",
        "",
        "> ⚠️ AI研判仅供参考，不构成投资建议。",
        f"> _推送时间：{occurred_at} | 研判模型：{str((result or {}).get('model', '--') or '--').strip()}_"
    ]

    return {
        "occurred_at": occurred_at,
        "category": "ai",
        "title": title,
        "detail": content or "模型未返回有效结论。",
        "tone": "accent",
        "signature": f"ai::{title}::{occurred_at}",
        "markdown_body": "\n".join(markdown_lines),
    }


def _build_learning_report_entry(report: dict) -> dict:
    summary_text = _normalize_text((report or {}).get("summary_text", "") or "")
    created_at = _normalize_text((report or {}).get("created_at", "") or "") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    governance_summary = _normalize_text(((report or {}).get("governance_summary", {}) or {}).get("summary_text", "") or "")
    active_rules = [str(item).strip() for item in list((report or {}).get("active_rules", []) or []) if str(item).strip()]
    watch_rules = [str(item).strip() for item in list((report or {}).get("watch_rules", []) or []) if str(item).strip()]
    frozen_rules = [str(item).strip() for item in list((report or {}).get("frozen_rules", []) or []) if str(item).strip()]
    promoted_rules = [str(item).strip() for item in list((report or {}).get("promoted_rules", []) or []) if str(item).strip()]
    new_watch_rules = [str(item).strip() for item in list((report or {}).get("new_watch_rules", []) or []) if str(item).strip()]
    new_frozen_rules = [str(item).strip() for item in list((report or {}).get("new_frozen_rules", []) or []) if str(item).strip()]
    recovered_rules = [str(item).strip() for item in list((report or {}).get("recovered_rules", []) or []) if str(item).strip()]
    feedback_summary_text = _normalize_text(((report or {}).get("feedback_summary", {}) or {}).get("summary_text", "") or "")
    top_positive_rules = [str(item).strip() for item in list((((report or {}).get("feedback_summary", {}) or {}).get("top_positive_rules", [])) or []) if str(item).strip()]
    top_negative_rules = [str(item).strip() for item in list((((report or {}).get("feedback_summary", {}) or {}).get("top_negative_rules", [])) or []) if str(item).strip()]

    markdown_lines = [
        "### 知识库学习摘要",
        "",
        f"- 时间：{created_at}",
        f"- 总结：{summary_text or '当前暂无可推送的学习结论。'}",
    ]
    if governance_summary:
        markdown_lines.append(f"- 治理概况：{governance_summary}")
    if feedback_summary_text:
        markdown_lines.append(f"- 用户反馈：{feedback_summary_text}")
    if promoted_rules or new_watch_rules or new_frozen_rules or recovered_rules:
        markdown_lines.extend(["", "#### 本轮状态变化", ""])
        if promoted_rules:
            markdown_lines.append(f"- 新增启用：{len(promoted_rules)} 条")
            markdown_lines.extend(promoted_rules[:3])
        if new_watch_rules:
            markdown_lines.append(f"- 新增观察：{len(new_watch_rules)} 条")
            markdown_lines.extend(new_watch_rules[:3])
        if new_frozen_rules:
            markdown_lines.append(f"- 新冻结：{len(new_frozen_rules)} 条")
            markdown_lines.extend(new_frozen_rules[:3])
        if recovered_rules:
            markdown_lines.append(f"- 冻结恢复：{len(recovered_rules)} 条")
            markdown_lines.extend(recovered_rules[:3])
    if active_rules:
        markdown_lines.extend(["", "#### 当前有效规则", ""])
        markdown_lines.extend(active_rules[:3])
    if watch_rules:
        markdown_lines.extend(["", "#### 候选观察规则", ""])
        markdown_lines.extend(watch_rules[:3])
    if frozen_rules:
        markdown_lines.extend(["", "#### 暂不采用规则", ""])
        markdown_lines.extend(frozen_rules[:3])
    if top_positive_rules:
        markdown_lines.extend(["", "#### 用户认可度较高", ""])
        markdown_lines.extend(top_positive_rules[:3])
    if top_negative_rules:
        markdown_lines.extend(["", "#### 用户负反馈较多", ""])
        markdown_lines.extend(top_negative_rules[:3])

    return {
        "occurred_at": created_at,
        "category": "learning",
        "title": "知识库学习摘要",
        "detail": summary_text or "当前暂无可推送的学习结论。",
        "tone": "accent",
        "markdown_body": "\n".join(markdown_lines),
    }
