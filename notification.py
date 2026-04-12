"""
消息推送：支持钉钉 Webhook 与 PushPlus。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib import error, request

from app_config import MetalMonitorConfig, PROJECT_DIR

RUNTIME_DIR = PROJECT_DIR / ".runtime"
NOTIFY_STATE_FILE = RUNTIME_DIR / "notify_state.json"


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _read_state(state_file: Path | None = None) -> dict:
    target = Path(state_file) if state_file else NOTIFY_STATE_FILE
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(state: dict, state_file: Path | None = None) -> None:
    target = Path(state_file) if state_file else NOTIFY_STATE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_last_result(state: dict, text: str) -> None:
    state["last_result_text"] = _normalize_text(text)
    state["last_result_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _should_notify_entry(entry: dict) -> bool:
    category = str(entry.get("category", "") or "").strip()
    title = str(entry.get("title", "") or "").strip()
    return category in {"spread", "mt5", "session"} or "点差" in title


def _configured_channels(config: MetalMonitorConfig) -> list[tuple[str, str, str]]:
    channels = []
    if str(config.dingtalk_webhook or "").strip():
        channels.append(("dingtalk", "钉钉", str(config.dingtalk_webhook or "").strip()))
    if str(config.pushplus_token or "").strip():
        channels.append(("pushplus", "PushPlus", str(config.pushplus_token or "").strip()))
    return channels


def _build_channel_state_key(channel_key: str, signature: str) -> str:
    return f"notified::{channel_key}::{signature}"


def _read_channel_last_time(state: dict, channel_key: str, signature: str) -> datetime | None:
    last_time = _parse_time(state.get(_build_channel_state_key(channel_key, signature), ""))
    if last_time is not None:
        return last_time
    return _parse_time(state.get(f"notified::{signature}", ""))


def _is_within_cooldown(entry: dict, state: dict, cooldown_min: int, now: datetime | None = None, channel_key: str | None = None) -> bool:
    signature = str(entry.get("signature", "") or "").strip()
    if not signature:
        return True
    if channel_key:
        last_time = _read_channel_last_time(state, channel_key, signature)
    else:
        last_time = _parse_time(state.get(f"notified::{signature}", ""))
    if last_time is None:
        return False
    current = now or _parse_time(entry.get("occurred_at", "")) or datetime.now()
    return current - last_time < timedelta(minutes=max(1, int(cooldown_min)))


def pick_notify_entries(entries: list[dict], config: MetalMonitorConfig, state_file: Path | None = None, now: datetime | None = None) -> list[dict]:
    channels = _configured_channels(config)
    if not channels:
        return []
    state = _read_state(state_file=state_file)
    result = []
    for entry in entries or []:
        if not _should_notify_entry(entry):
            continue
        due_channels = [
            channel_key
            for channel_key, _channel_name, _channel_value in channels
            if not _is_within_cooldown(entry, state, config.notify_cooldown_min, now=now, channel_key=channel_key)
        ]
        if due_channels:
            result.append(entry)
    return result


def _post_json(url: str, payload: dict, timeout: int = 8) -> tuple[bool, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=str(url).strip(),
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="ignore")
        return True, text
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return False, f"HTTP {exc.code}: {detail or exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _build_markdown(entry: dict) -> str:
    title = _normalize_text(entry.get("title", "贵金属监控提醒"))
    markdown_body = str(entry.get("markdown_body", "") or "").strip()
    detail = _normalize_text(entry.get("detail", ""))
    occurred_at = str(entry.get("occurred_at", "--") or "--").strip()
    category = str(entry.get("category", "general") or "general").strip()
    trade_grade = _normalize_text(entry.get("trade_grade", ""))
    trade_grade_detail = _normalize_text(entry.get("trade_grade_detail", ""))
    trade_next_review = _normalize_text(entry.get("trade_next_review", ""))
    if markdown_body:
        return markdown_body
    lines = [
        f"### {title}",
        "",
        f"- 时间：{occurred_at}",
        f"- 分类：{category}",
        f"- 内容：{detail}",
    ]
    if trade_grade:
        lines.append(f"- 当前结论：{trade_grade}")
    if trade_grade_detail:
        lines.append(f"- 原因：{trade_grade_detail}")
    if trade_next_review:
        lines.append(f"- 下一次复核：{trade_next_review}")
    lines.append("")
    return "\n".join(lines)


def send_dingtalk(entry: dict, webhook: str) -> tuple[bool, str]:
    if not str(webhook or "").strip():
        return False, "未配置钉钉 Webhook"
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": _normalize_text(entry.get("title", "贵金属监控提醒")),
            "text": _build_markdown(entry),
        },
    }
    return _post_json(webhook, payload)


def send_pushplus(entry: dict, token: str) -> tuple[bool, str]:
    if not str(token or "").strip():
        return False, "未配置 PushPlus Token"
    payload = {
        "token": str(token).strip(),
        "title": _normalize_text(entry.get("title", "贵金属监控提醒")),
        "content": _build_markdown(entry),
        "template": "markdown",
    }
    return _post_json("https://www.pushplus.plus/send", payload)


def send_notifications(entries: list[dict], config: MetalMonitorConfig, state_file: Path | None = None) -> dict:
    pending = pick_notify_entries(entries, config, state_file=state_file)
    if not pending:
        return {"sent_count": 0, "sent_channel_count": 0, "messages": [], "errors": []}

    state = _read_state(state_file=state_file)
    sent_count = 0
    sent_channel_count = 0
    messages = []
    errors = []
    channels = _configured_channels(config)
    for entry in pending:
        entry_title = _normalize_text(entry.get("title", "提醒"))
        signature = str(entry.get("signature", "") or "").strip()
        entry_sent = False
        for channel_key, channel_name, channel_value in channels:
            if _is_within_cooldown(entry, state, config.notify_cooldown_min, channel_key=channel_key):
                continue
            if channel_key == "dingtalk":
                ok, detail = send_dingtalk(entry, channel_value)
            else:
                ok, detail = send_pushplus(entry, channel_value)

            if ok:
                if signature:
                    state[_build_channel_state_key(channel_key, signature)] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                entry_sent = True
                sent_channel_count += 1
                messages.append(f"{entry_title} 已推送到{channel_name}")
            else:
                errors.append(f"{entry_title} 推送到{channel_name}失败：{detail}")

        if entry_sent:
            sent_count += 1

    if messages:
        _update_last_result(state, "；".join(messages))
    elif errors:
        _update_last_result(state, "；".join(errors))

    _write_state(state, state_file=state_file)
    return {"sent_count": sent_count, "sent_channel_count": sent_channel_count, "messages": messages, "errors": errors}


def send_test_notification(config: MetalMonitorConfig, state_file: Path | None = None) -> dict:
    state = _read_state(state_file=state_file)
    entry = {
        "occurred_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "category": "test",
        "title": "贵金属监控推送测试",
        "detail": "这是一条测试消息。若你收到此提醒，说明独立项目的消息推送链已经打通。",
        "tone": "accent",
        "signature": f"test::{datetime.now().strftime('%Y%m%d%H%M%S')}",
    }
    messages = []
    errors = []
    if str(config.dingtalk_webhook or "").strip():
        ok, detail = send_dingtalk(entry, config.dingtalk_webhook)
        if ok:
            messages.append("钉钉测试推送成功")
        else:
            errors.append(f"钉钉测试推送失败：{detail}")
    if str(config.pushplus_token or "").strip():
        ok, detail = send_pushplus(entry, config.pushplus_token)
        if ok:
            messages.append("PushPlus 测试推送成功")
        else:
            errors.append(f"PushPlus 测试推送失败：{detail}")
    if not messages and not errors:
        errors.append("未配置钉钉 Webhook 或 PushPlus Token")
    if messages:
        _update_last_result(state, "；".join(messages))
    elif errors:
        _update_last_result(state, "；".join(errors))
    _write_state(state, state_file=state_file)
    return {"messages": messages, "errors": errors}


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
    summary_text = str((snapshot or {}).get("summary_text", "") or "").strip()
    occurred_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    markdown_lines = [
        f"### {title}",
        "",
        f"- 时间：{occurred_at}",
        f"- 模型：{str((result or {}).get('model', '--') or '--').strip()}",
    ]
    if symbols:
        markdown_lines.append(f"- 品种：{' / '.join(symbols)}")
    if summary_text:
        markdown_lines.append(f"- 运行概览：{_normalize_text(summary_text)}")
    markdown_lines.extend(["", "#### 研判结论", "", content or "模型未返回有效结论。"])
    return {
        "occurred_at": occurred_at,
        "category": "ai",
        "title": title,
        "detail": content or "模型未返回有效结论。",
        "tone": "accent",
        "signature": f"ai::{title}::{occurred_at}",
        "markdown_body": "\n".join(markdown_lines),
    }


def send_ai_brief_notification(result: dict, snapshot: dict, config: MetalMonitorConfig, state_file: Path | None = None) -> dict:
    if not bool(config.ai_push_enabled):
        return {"sent_count": 0, "messages": [], "errors": []}

    entry = _build_ai_brief_entry(result, snapshot, config)
    state = _read_state(state_file=state_file)
    messages = []
    errors = []
    if str(config.dingtalk_webhook or "").strip():
        ok, detail = send_dingtalk(entry, config.dingtalk_webhook)
        if ok:
            messages.append("AI 研判已推送到钉钉")
        else:
            errors.append(f"AI 研判推送到钉钉失败：{detail}")
    if str(config.pushplus_token or "").strip():
        ok, detail = send_pushplus(entry, config.pushplus_token)
        if ok:
            messages.append("AI 研判已推送到 PushPlus")
        else:
            errors.append(f"AI 研判推送到 PushPlus 失败：{detail}")
    if messages:
        _update_last_result(state, "；".join(messages))
    elif errors:
        _update_last_result(state, "；".join(errors))
    _write_state(state, state_file=state_file)
    return {"sent_count": len(messages), "messages": messages, "errors": errors}


def get_notification_status(config: MetalMonitorConfig, state_file: Path | None = None) -> dict:
    state = _read_state(state_file=state_file)
    channels = []
    if str(config.dingtalk_webhook or "").strip():
        channels.append("钉钉已配置")
    else:
        channels.append("钉钉未配置")
    if str(config.pushplus_token or "").strip():
        channels.append("PushPlus已配置")
    else:
        channels.append("PushPlus未配置")
    return {
        "channels_text": " | ".join(channels),
        "cooldown_text": f"冷却 {int(config.notify_cooldown_min)} 分钟",
        "last_result_text": str(state.get("last_result_text", "最近还没有推送记录。") or "最近还没有推送记录。").strip(),
        "last_result_time": str(state.get("last_result_time", "--") or "--").strip(),
    }
