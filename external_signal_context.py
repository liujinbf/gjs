from __future__ import annotations

from monitor_rules import build_portfolio_trade_grade


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_importance(value: object) -> str:
    text = _normalize_text(value).lower()
    if text in {"high", "高影响", "高"}:
        return "high"
    if text in {"low", "低影响", "低"}:
        return "low"
    return "medium"


def _importance_rank(value: object) -> int:
    importance = _normalize_importance(value)
    if importance == "high":
        return 3
    if importance == "medium":
        return 2
    return 1


def _symbol_matches(symbol: str, symbols: object) -> bool:
    target = _normalize_text(symbol).upper()
    if not target:
        return False
    if isinstance(symbols, str):
        symbols = [part.strip() for part in str(symbols or "").split(",")]
    candidates = {
        _normalize_text(item).upper()
        for item in list(symbols or [])
        if _normalize_text(item)
    }
    if not candidates:
        return True
    return target in candidates


def _resolve_signal_bias(item: dict) -> str:
    signal_side = _normalize_text(item.get("signal_side", "")).lower()
    if signal_side == "long":
        return "bullish"
    if signal_side == "short":
        return "bearish"

    votes = []
    for key in ("breakout_direction", "multi_timeframe_bias", "intraday_bias"):
        value = _normalize_text(item.get(key, "")).lower()
        if value in {"bullish", "bearish"}:
            votes.append(value)
    if votes.count("bullish") > votes.count("bearish"):
        return "bullish"
    if votes.count("bearish") > votes.count("bullish"):
        return "bearish"
    return "neutral"


def _pick_event_result_item(symbol: str, snapshot: dict) -> dict | None:
    candidates = []
    for item in list(snapshot.get("event_feed_items", []) or []):
        bias = _normalize_text(item.get("result_bias", "")).lower()
        if bias not in {"bullish", "bearish"}:
            continue
        if not bool(item.get("has_result", False)):
            continue
        if not _symbol_matches(symbol, item.get("symbols", [])):
            continue
        candidates.append(dict(item))
    if not candidates:
        return None
    candidates.sort(
        key=lambda current: (
            _importance_rank(current.get("importance", "")),
            _normalize_text(current.get("time_text", "")),
            _normalize_text(current.get("name", "")),
        ),
        reverse=True,
    )
    return candidates[0]


def _pick_macro_data_item(symbol: str, snapshot: dict) -> dict | None:
    candidates = []
    for item in list(snapshot.get("macro_data_items", []) or []):
        direction = _normalize_text(item.get("direction", "")).lower()
        if direction not in {"bullish", "bearish"}:
            continue
        if not _symbol_matches(symbol, item.get("symbols", [])):
            continue
        candidates.append(dict(item))
    if not candidates:
        return None
    candidates.sort(
        key=lambda current: (
            _importance_rank(current.get("importance", "")),
            _normalize_text(current.get("published_at", "")),
            _normalize_text(current.get("name", "")),
        ),
        reverse=True,
    )
    return candidates[0]


def _build_event_note(event_item: dict) -> str:
    summary = _normalize_text(event_item.get("result_summary_text", ""))
    if summary:
        return f"事件结果：{summary}"
    name = _normalize_text(event_item.get("name", "")) or "外部事件"
    bias = _normalize_text(event_item.get("result_bias", "")).lower()
    bias_text = "偏多" if bias == "bullish" else ("偏空" if bias == "bearish" else "中性")
    return f"事件结果：{name} 当前更偏{bias_text}。"


def _build_macro_note(macro_item: dict) -> str:
    name = _normalize_text(macro_item.get("name", "")) or "结构化宏观数据"
    value_text = _normalize_text(macro_item.get("value_text", "--")) or "--"
    delta_text = _normalize_text(macro_item.get("delta_text", ""))
    direction = _normalize_text(macro_item.get("direction", "")).lower()
    direction_text = "偏多" if direction == "bullish" else ("偏空" if direction == "bearish" else "中性")
    parts = [f"宏观数据：{name} 当前值 {value_text}"]
    if delta_text:
        parts.append(delta_text)
    parts.append(f"背景{direction_text}")
    return "，".join(parts)


def _replace_summary_line(summary_text: str, prefix: str, line: str) -> str:
    lines = [str(current or "") for current in str(summary_text or "").splitlines()]
    replaced = False
    for index, current in enumerate(lines):
        if current.startswith(prefix):
            lines[index] = line
            replaced = True
            break
    if not replaced:
        lines.append(line)
    return "\n".join(current for current in lines if _normalize_text(current))


def apply_external_signal_context(snapshot: dict, event_context: dict | None = None) -> dict:
    payload = dict(snapshot or {})
    items = []
    conflict_notes = []
    alignment_notes = []
    for raw_item in list(payload.get("items", []) or []):
        item = dict(raw_item or {})
        symbol = _normalize_text(item.get("symbol", "")).upper()
        signal_bias = _resolve_signal_bias(item)
        event_item = _pick_event_result_item(symbol, payload)
        macro_item = _pick_macro_data_item(symbol, payload)

        external_notes = []
        strongest_conflict_rank = 0
        strongest_conflict_note = ""
        strongest_alignment_note = ""
        for source_item, source_bias, note_text in (
            (
                event_item,
                _normalize_text((event_item or {}).get("result_bias", "")).lower(),
                _build_event_note(event_item) if event_item else "",
            ),
            (
                macro_item,
                _normalize_text((macro_item or {}).get("direction", "")).lower(),
                _build_macro_note(macro_item) if macro_item else "",
            ),
        ):
            if not source_item or source_bias not in {"bullish", "bearish"} or not note_text:
                continue
            if signal_bias in {"bullish", "bearish"}:
                if source_bias != signal_bias:
                    rank = _importance_rank(source_item.get("importance", ""))
                    if rank >= strongest_conflict_rank:
                        strongest_conflict_rank = rank
                        strongest_conflict_note = note_text
                    external_notes.append(f"{note_text}，与当前结构方向相反。")
                else:
                    if not strongest_alignment_note:
                        strongest_alignment_note = note_text
                    external_notes.append(f"{note_text}，与当前结构方向基本一致。")
            else:
                external_notes.append(note_text)

        if external_notes:
            item["external_bias_note"] = " ".join(external_notes)
            execution_note = _normalize_text(item.get("execution_note", ""))
            item["execution_note"] = " ".join(part for part in (execution_note, item["external_bias_note"]) if part)

        grade = _normalize_text(item.get("trade_grade", ""))
        if grade == "可轻仓试仓" and strongest_conflict_rank >= 2:
            item["trade_grade"] = "只适合观察"
            item["trade_grade_source"] = "macro"
            item["trade_grade_detail"] = (
                "外部宏观结果与当前结构方向相反，先别逆着最新数据硬做，等价格重新消化后再判断。"
            )
            if strongest_conflict_note:
                item["trade_grade_detail"] = f"{item['trade_grade_detail']} {strongest_conflict_note}"
            item["trade_next_review"] = "建议等 10-15 分钟，确认价格对结果的消化方向后再复核。"
            item["alert_state_text"] = "宏观结果冲突"
            item["alert_state_detail"] = item["trade_grade_detail"]
            item["alert_state_tone"] = "warning" if strongest_conflict_rank >= 3 else "accent"
            item["alert_state_rank"] = max(int(item.get("alert_state_rank", 0) or 0), 4)
            conflict_notes.append(f"{symbol} 外部结果与结构冲突，已降级为观察。")
        elif grade == "可轻仓试仓" and strongest_alignment_note:
            detail = _normalize_text(item.get("trade_grade_detail", ""))
            if strongest_alignment_note not in detail:
                item["trade_grade_detail"] = f"{detail} 同时，{strongest_alignment_note}。".strip()
            alignment_notes.append(f"{symbol} 外部背景与当前结构同向。")

        items.append(item)

    payload["items"] = items
    connected = str(payload.get("status_tone", "") or "").strip().lower() == "success"
    portfolio_grade = build_portfolio_trade_grade(
        items,
        connected,
        event_risk_mode=str(payload.get("event_risk_mode", "normal") or "normal"),
        event_context=event_context,
    )
    payload["trade_grade"] = portfolio_grade["grade"]
    payload["trade_grade_detail"] = portfolio_grade["detail"]
    payload["trade_next_review"] = portfolio_grade["next_review"]
    payload["trade_grade_tone"] = portfolio_grade["tone"]
    payload["summary_text"] = _replace_summary_line(
        str(payload.get("summary_text", "") or ""),
        "出手分级：",
        f"出手分级：{portfolio_grade['grade']}。{portfolio_grade['detail']}",
    )
    if conflict_notes or alignment_notes:
        payload["summary_text"] = _replace_summary_line(
            str(payload.get("summary_text", "") or ""),
            "外部结果：",
            f"外部结果：{'；'.join(conflict_notes[:2] + alignment_notes[:2])}",
        )
    return payload
