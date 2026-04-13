from __future__ import annotations

from datetime import datetime

from app_config import EVENT_RISK_MODES, normalize_event_risk_mode

SCHEDULE_TIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S")
EVENT_IMPORTANCE_LABELS = {
    "high": "高影响",
    "medium": "中影响",
    "low": "低影响",
}
EVENT_IMPORTANCE_RANKS = {"high": 3, "medium": 2, "low": 1}


def normalize_event_importance(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "h", "3", "高", "高影响", "重要"}:
        return "high"
    if text in {"low", "l", "1", "低", "低影响", "次要"}:
        return "low"
    return "medium"


def parse_event_symbols(value: str) -> list[str]:
    text = str(value or "").replace("；", ",").replace("，", ",").replace(" ", ",").strip()
    if not text or text.lower() in {"all", "global", "*", "全部", "全品种"}:
        return []
    result = []
    seen = set()
    for part in text.split(","):
        symbol = str(part or "").strip().upper()
        if not symbol or symbol in {"ALL", "GLOBAL", "*", "全部", "全品种"} or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _format_event_symbols(symbols: list[str]) -> str:
    cleaned = [str(item or "").strip().upper() for item in list(symbols or []) if str(item or "").strip()]
    return ",".join(cleaned) if cleaned else "全部"


def _event_scope_text(symbols: list[str]) -> str:
    cleaned = [str(item or "").strip().upper() for item in list(symbols or []) if str(item or "").strip()]
    return "全部观察品种" if not cleaned else "、".join(cleaned)


def _event_priority(entry: dict) -> tuple[int, datetime]:
    importance = normalize_event_importance(entry.get("importance", "medium"))
    return EVENT_IMPORTANCE_RANKS.get(importance, 2), entry["time"]


def _event_applies_to_symbols(entry: dict, watched_symbols: list[str]) -> bool:
    watched = {str(item or "").strip().upper() for item in list(watched_symbols or []) if str(item or "").strip()}
    targets = {str(item or "").strip().upper() for item in list(entry.get("symbols", []) or []) if str(item or "").strip()}
    if not targets or not watched:
        return True
    return bool(watched.intersection(targets))


def _resolve_event_windows(entry: dict, pre_event_lead_min: int, post_event_window_min: int) -> tuple[int, int]:
    importance = normalize_event_importance(entry.get("importance", "medium"))
    pre_base = max(1, int(pre_event_lead_min))
    post_base = max(1, int(post_event_window_min))
    if importance == "high":
        return max(pre_base, int(round(pre_base * 1.5))), max(post_base, int(round(post_base * 2.0)))
    if importance == "low":
        return max(5, int(round(pre_base * 0.5))), max(5, int(round(post_base * 0.75)))
    return pre_base, post_base


def _describe_event(entry: dict) -> str:
    if not entry:
        return "未命名事件"
    name = str(entry.get("name", "未命名事件") or "未命名事件").strip()
    time_text = str(entry.get("time_text", "") or "").strip()
    importance_text = str(entry.get("importance_text", EVENT_IMPORTANCE_LABELS["medium"]) or EVENT_IMPORTANCE_LABELS["medium"]).strip()
    scope_text = str(entry.get("scope_text", "全部观察品种") or "全部观察品种").strip()
    pieces = [name]
    if time_text:
        pieces.append(f"（{time_text}）")
    pieces.append(importance_text)
    pieces.append(scope_text)
    return "，".join(piece for piece in pieces if piece)


def parse_event_schedules(raw_text: str) -> list[dict]:
    text = str(raw_text or "").replace("\r\n", "\n").replace("；", ";")
    chunks = []
    for line in text.splitlines():
        for part in line.split(";"):
            piece = str(part or "").strip()
            if piece:
                chunks.append(piece)

    result = []
    seen = set()
    for chunk in chunks:
        if "|" in chunk:
            fields = [str(part or "").strip() for part in chunk.split("|")]
            time_text = fields[0] if fields else ""
            name = fields[1] if len(fields) > 1 else "未命名事件"
            importance = normalize_event_importance(fields[2] if len(fields) > 2 else "")
            symbols = parse_event_symbols(fields[3] if len(fields) > 3 else "")
        elif "," in chunk:
            time_text, name = chunk.split(",", 1)
            importance = "medium"
            symbols = []
        else:
            time_text, name = chunk, "未命名事件"
            importance = "medium"
            symbols = []
        event_time = _parse_event_time(time_text)
        event_name = str(name or "未命名事件").strip() or "未命名事件"
        if event_time is None:
            continue
        importance_text = EVENT_IMPORTANCE_LABELS.get(importance, EVENT_IMPORTANCE_LABELS["medium"])
        symbols_text = _format_event_symbols(symbols)
        signature = f"{event_time.strftime('%Y-%m-%d %H:%M')}|{event_name}|{importance}|{symbols_text}"
        if signature in seen:
            continue
        seen.add(signature)
        result.append(
            {
                "name": event_name,
                "time": event_time,
                "time_text": event_time.strftime("%Y-%m-%d %H:%M"),
                "importance": importance,
                "importance_text": importance_text,
                "symbols": symbols,
                "symbols_text": symbols_text,
                "scope_text": _event_scope_text(symbols),
            }
        )

    result.sort(key=lambda item: item["time"])
    return result


def normalize_event_schedule_text(raw_text: str) -> str:
    entries = parse_event_schedules(raw_text)
    chunks = []
    for item in entries:
        importance = str(item.get("importance", "medium") or "medium").strip()
        symbols = list(item.get("symbols", []) or [])
        if importance == "medium" and not symbols:
            chunks.append(f"{item['time_text']}|{item['name']}")
        else:
            chunks.append(f"{item['time_text']}|{item['name']}|{importance}|{_format_event_symbols(symbols)}")
    return ";".join(chunks)


def format_event_schedule_for_editor(raw_text: str) -> str:
    entries = parse_event_schedules(raw_text)
    lines = []
    for item in entries:
        importance = str(item.get("importance", "medium") or "medium").strip()
        symbols = list(item.get("symbols", []) or [])
        if importance == "medium" and not symbols:
            lines.append(f"{item['time_text']}|{item['name']}")
        else:
            lines.append(f"{item['time_text']}|{item['name']}|{importance}|{_format_event_symbols(symbols)}")
    return "\n".join(lines)


def resolve_event_risk_context(
    base_mode: str,
    auto_enabled: bool,
    schedule_text: str,
    pre_event_lead_min: int,
    post_event_window_min: int,
    now: datetime | None = None,
    symbols: list[str] | None = None,
) -> dict:
    current = now or datetime.now()
    manual_mode = normalize_event_risk_mode(base_mode)
    entries = parse_event_schedules(schedule_text)
    applicable_entries = [item for item in entries if _event_applies_to_symbols(item, symbols or [])]
    next_event = next((item for item in applicable_entries if item["time"] >= current), None)

    context = {
        "mode": manual_mode,
        "mode_text": EVENT_RISK_MODES.get(manual_mode, "正常观察"),
        "source": "manual",
        "source_text": "手动模式",
        "reason": f"当前按手动纪律执行：{EVENT_RISK_MODES.get(manual_mode, '正常观察')}。",
        "auto_enabled": bool(auto_enabled),
        "schedule_count": len(entries),
        "active_event_name": "",
        "active_event_time_text": "",
        "active_event_importance": "",
        "active_event_importance_text": "",
        "active_event_scope_text": "",
        "active_event_symbols": [],
        "next_event_name": str(next_event.get("name", "") if next_event else "").strip(),
        "next_event_time_text": str(next_event.get("time_text", "") if next_event else "").strip(),
        "next_event_importance": str(next_event.get("importance", "") if next_event else "").strip(),
        "next_event_importance_text": str(next_event.get("importance_text", "") if next_event else "").strip(),
        "next_event_scope_text": str(next_event.get("scope_text", "") if next_event else "").strip(),
        "next_event_symbols": list(next_event.get("symbols", []) or []) if next_event else [],
    }

    if not bool(auto_enabled):
        if next_event:
            context["reason"] += f" 下一个已登记事件是 {_describe_event(next_event)}。"
        return context

    if manual_mode == "illiquid":
        context["reason"] = "当前手动基准模式为流动性偏弱，自动事件计划不会覆盖这一保护模式。"
        if next_event:
            context["reason"] += f" 下一个已登记事件是 {_describe_event(next_event)}。"
        return context

    if not entries:
        context["source"] = "auto"
        context["source_text"] = "自动模式"
        context["reason"] = "已开启自动事件模式，但当前还没有登记事件计划，暂按手动基准模式执行。"
        return context
    if not applicable_entries:
        context["source"] = "auto"
        context["source_text"] = "自动模式"
        context["reason"] = "已开启自动事件模式，但当前登记事件里暂无与你的观察品种直接相关的事件，暂按手动基准模式执行。"
        return context

    post_candidates = []
    pre_candidates = []
    for entry in applicable_entries:
        pre_window, post_window = _resolve_event_windows(entry, pre_event_lead_min, post_event_window_min)
        delta_minutes = (entry["time"] - current).total_seconds() / 60.0
        if -float(post_window) <= delta_minutes <= 0:
            post_candidates.append((EVENT_IMPORTANCE_RANKS.get(entry["importance"], 2), entry["time"], entry))
        elif 0 < delta_minutes <= float(pre_window):
            pre_candidates.append((EVENT_IMPORTANCE_RANKS.get(entry["importance"], 2), -delta_minutes, entry))

    active_post = max(post_candidates, key=lambda item: (item[0], item[1]))[2] if post_candidates else None
    active_pre = max(pre_candidates, key=lambda item: (item[0], item[1]))[2] if pre_candidates else None

    if active_post is not None:
        context["mode"] = "post_event"
        context["mode_text"] = EVENT_RISK_MODES["post_event"]
        context["source"] = "auto"
        context["source_text"] = "自动模式"
        context["active_event_name"] = active_post["name"]
        context["active_event_time_text"] = active_post["time_text"]
        context["active_event_importance"] = active_post["importance"]
        context["active_event_importance_text"] = active_post["importance_text"]
        context["active_event_scope_text"] = active_post["scope_text"]
        context["active_event_symbols"] = list(active_post.get("symbols", []) or [])
        context["reason"] = (
            f"{active_post['name']} 已在 {active_post['time_text']} 落地，"
            f"该事件属于{active_post['importance_text']}，影响范围：{active_post['scope_text']}，"
            "当前自动进入事件落地观察阶段。"
        )
        return context

    if active_pre is not None:
        context["mode"] = "pre_event"
        context["mode_text"] = EVENT_RISK_MODES["pre_event"]
        context["source"] = "auto"
        context["source_text"] = "自动模式"
        context["active_event_name"] = active_pre["name"]
        context["active_event_time_text"] = active_pre["time_text"]
        context["active_event_importance"] = active_pre["importance"]
        context["active_event_importance_text"] = active_pre["importance_text"]
        context["active_event_scope_text"] = active_pre["scope_text"]
        context["active_event_symbols"] = list(active_pre.get("symbols", []) or [])
        context["reason"] = (
            f"{active_pre['name']} 将在 {active_pre['time_text']} 落地，"
            f"该事件属于{active_pre['importance_text']}，影响范围：{active_pre['scope_text']}，"
            "当前自动进入事件前高敏阶段。"
        )
        return context

    context["source"] = "auto"
    context["source_text"] = "自动模式"
    if next_event:
        context["reason"] = (
            f"当前不在自动事件窗口内，暂按手动基准模式 {EVENT_RISK_MODES.get(manual_mode, '正常观察')} 执行；"
            f"下一个事件是 {_describe_event(next_event)}。"
        )
    else:
        context["reason"] = "当前已登记且与你观察品种相关的事件都已过期，暂按手动基准模式执行。"
    return context


def _parse_event_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in SCHEDULE_TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
