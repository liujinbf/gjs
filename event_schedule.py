from __future__ import annotations

from datetime import datetime

from app_config import EVENT_RISK_MODES, normalize_event_risk_mode

SCHEDULE_TIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S")


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
            time_text, name = chunk.split("|", 1)
        elif "," in chunk:
            time_text, name = chunk.split(",", 1)
        else:
            time_text, name = chunk, "未命名事件"
        event_time = _parse_event_time(time_text)
        event_name = str(name or "未命名事件").strip() or "未命名事件"
        if event_time is None:
            continue
        signature = f"{event_time.strftime('%Y-%m-%d %H:%M')}|{event_name}"
        if signature in seen:
            continue
        seen.add(signature)
        result.append(
            {
                "name": event_name,
                "time": event_time,
                "time_text": event_time.strftime("%Y-%m-%d %H:%M"),
            }
        )

    result.sort(key=lambda item: item["time"])
    return result


def normalize_event_schedule_text(raw_text: str) -> str:
    entries = parse_event_schedules(raw_text)
    return ";".join(f"{item['time_text']}|{item['name']}" for item in entries)


def format_event_schedule_for_editor(raw_text: str) -> str:
    entries = parse_event_schedules(raw_text)
    return "\n".join(f"{item['time_text']}|{item['name']}" for item in entries)


def resolve_event_risk_context(
    base_mode: str,
    auto_enabled: bool,
    schedule_text: str,
    pre_event_lead_min: int,
    post_event_window_min: int,
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now()
    manual_mode = normalize_event_risk_mode(base_mode)
    entries = parse_event_schedules(schedule_text)
    next_event = next((item for item in entries if item["time"] >= current), None)

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
        "next_event_name": str(next_event.get("name", "") if next_event else "").strip(),
        "next_event_time_text": str(next_event.get("time_text", "") if next_event else "").strip(),
    }

    if not bool(auto_enabled):
        if next_event:
            context["reason"] += f" 下一个已登记事件是 {next_event['name']}（{next_event['time_text']}）。"
        return context

    if manual_mode == "illiquid":
        context["reason"] = "当前手动基准模式为流动性偏弱，自动事件计划不会覆盖这一保护模式。"
        if next_event:
            context["reason"] += f" 下一个已登记事件是 {next_event['name']}（{next_event['time_text']}）。"
        return context

    if not entries:
        context["source"] = "auto"
        context["source_text"] = "自动模式"
        context["reason"] = "已开启自动事件模式，但当前还没有登记事件计划，暂按手动基准模式执行。"
        return context

    pre_window = max(1, int(pre_event_lead_min))
    post_window = max(1, int(post_event_window_min))
    active_post = None
    active_pre = None
    for entry in entries:
        delta_minutes = (entry["time"] - current).total_seconds() / 60.0
        if -float(post_window) <= delta_minutes <= 0:
            if active_post is None or entry["time"] > active_post["time"]:
                active_post = entry
        elif 0 < delta_minutes <= float(pre_window):
            if active_pre is None or entry["time"] < active_pre["time"]:
                active_pre = entry

    if active_post is not None:
        context["mode"] = "post_event"
        context["mode_text"] = EVENT_RISK_MODES["post_event"]
        context["source"] = "auto"
        context["source_text"] = "自动模式"
        context["active_event_name"] = active_post["name"]
        context["active_event_time_text"] = active_post["time_text"]
        context["reason"] = f"{active_post['name']} 已在 {active_post['time_text']} 落地，当前自动进入事件落地观察阶段。"
        return context

    if active_pre is not None:
        context["mode"] = "pre_event"
        context["mode_text"] = EVENT_RISK_MODES["pre_event"]
        context["source"] = "auto"
        context["source_text"] = "自动模式"
        context["active_event_name"] = active_pre["name"]
        context["active_event_time_text"] = active_pre["time_text"]
        context["reason"] = f"{active_pre['name']} 将在 {active_pre['time_text']} 落地，当前自动进入事件前高敏阶段。"
        return context

    context["source"] = "auto"
    context["source_text"] = "自动模式"
    if next_event:
        context["reason"] = (
            f"当前不在自动事件窗口内，暂按手动基准模式 {EVENT_RISK_MODES.get(manual_mode, '正常观察')} 执行；"
            f"下一个事件是 {next_event['name']}（{next_event['time_text']}）。"
        )
    else:
        context["reason"] = "当前已登记事件都已过期，暂按手动基准模式执行。"
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
