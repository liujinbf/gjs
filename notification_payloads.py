"""
消息推送：支持钉钉 Webhook 与 PushPlus。
"""
from __future__ import annotations

from datetime import datetime

from app_config import MetalMonitorConfig


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


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
    aggregate_count       = int(entry.get("aggregate_count", 0) or 0)
    notify_mode_text      = _normalize_text(entry.get("notify_mode_text", ""))

    # 技术指标字段（来自 technical_indicators.py 注入到 quote row）
    rsi14       = entry.get("rsi14")
    ma20        = entry.get("ma20")
    ma50        = entry.get("ma50")
    boll_mid    = entry.get("bollinger_mid")
    boll_upper  = entry.get("bollinger_upper")
    boll_lower  = entry.get("bollinger_lower")
    change_pct  = entry.get("change_pct_24h")
    latest_price = entry.get("baseline_latest_price")
    risk_reward_ratio = entry.get("risk_reward_ratio")
    stop_loss_price = entry.get("stop_loss_price")
    take_profit_1   = entry.get("take_profit_1")
    take_profit_2   = entry.get("take_profit_2")

    if markdown_body:
        return markdown_body

    # ── 分类 emoji 映射 ──
    CATEGORY_EMOJI = {
        "spread": "⚠️", "recovery": "✅", "structure": "📐",
        "event": "📅", "ai": "🤖", "general": "📊",
    }
    emoji = CATEGORY_EMOJI.get(category, "📊")

    lines = [f"## {emoji}【{title}】", "", f"- 时间：{occurred_at}"]

    if latest_price:
        try:
            price_line = f"- 当前价格：{float(latest_price):,.2f}"
            if change_pct is not None:
                sign = "+" if float(change_pct) >= 0 else ""
                price_line += f"（24h涨跌 {sign}{change_pct}%）"
            lines.append(price_line)
        except (TypeError, ValueError):
            pass

    if detail:
        lines.extend(["", f"**内容：** {detail}"])

    # ── 技术面 ──
    tech_lines = []
    if rsi14 is not None:
        try:
            rsi_val = float(rsi14)
            rsi_tag = "超买区" if rsi_val > 70 else ("超卖区" if rsi_val < 30 else "中性区")
            tech_lines.append(f"  - RSI(14)：**{rsi_val}**（{rsi_tag}）")
        except (TypeError, ValueError):
            pass
    if ma20 and ma50:
        try:
            trend = "多头排列" if float(ma20) > float(ma50) else "空头排列(死叉)"
            tech_lines.append(f"  - 均线：MA20={float(ma20):.2f} MA50={float(ma50):.2f}，{trend}")
        except (TypeError, ValueError):
            pass
    if boll_mid:
        try:
            tech_lines.append(
                f"  - 布林带：上轨={float(boll_upper):.2f} 中轨={float(boll_mid):.2f} 下轨={float(boll_lower):.2f}"
            )
        except (TypeError, ValueError):
            pass
    if tech_lines:
        lines.extend(["", "**📊 技术面**"])
        lines.extend(tech_lines)

    # ── 宏观事件 ──
    if event_name:
        event_parts = [p for p in (event_name, event_time_text, event_importance_text, event_scope_text) if p]
        lines.extend(["", "**📅 事件窗口**", f"  - {' | '.join(event_parts)}"])
        if event_note:
            lines.append(f"  - 提醒：{event_note}")
    elif event_mode_text:
        lines.extend(["", f"**📋 纪律：** {event_mode_text}"])

    # ── 操作建议 ──
    action_lines = []
    if trade_grade:
        action_lines.append(f"  - 建议：**{trade_grade}**")
    if trade_grade_detail:
        action_lines.append(f"  - 原因：{trade_grade_detail}")
    if risk_reward_ratio:
        try:
            action_lines.append(f"  - 预算盈亏比：1:{float(risk_reward_ratio):.2f}")
        except (TypeError, ValueError):
            pass
    if stop_loss_price:
        try:
            action_lines.append(f"  - 止损位：{float(stop_loss_price):,.2f}")
        except (TypeError, ValueError):
            pass
    if take_profit_1:
        try:
            action_lines.append(f"  - 目标位1：{float(take_profit_1):,.2f}")
        except (TypeError, ValueError):
            pass
    if take_profit_2:
        try:
            action_lines.append(f"  - 目标位2：{float(take_profit_2):,.2f}")
        except (TypeError, ValueError):
            pass
    if trade_next_review:
        action_lines.append(f"  - 下次复核：{trade_next_review}")
    if action_lines:
        lines.extend(["", "**💡 操作建议**"])
        lines.extend(action_lines)

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
