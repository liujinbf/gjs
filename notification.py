"""
消息推送：支持钉钉 Webhook 与 PushPlus。
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from urllib import error, request

from app_config import MetalMonitorConfig
from notification_payloads import _build_ai_brief_entry, _build_learning_report_entry, _build_markdown, _normalize_text
from notification_state import (
    NOTIFY_STATE_FILE,
    RUNTIME_DIR,
    _build_channel_state_key,
    _build_notify_group_key,
    _configured_channels,
    _get_notify_priority,
    _increase_group_pending,
    _is_within_cooldown,
    _mark_group_sent,
    _mark_learning_digest_sent,
    _parse_time,
    _read_learning_digest_state,
    _read_group_state,
    _read_state,
    _should_notify_entry,
    _update_last_result,
    _write_state,
)


def pick_notify_entries(
    entries: list[dict],
    config: MetalMonitorConfig,
    state_file: Path | None = None,
    now: datetime | None = None,
) -> list[dict]:
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
    result.sort(
        key=lambda item: (
            -int(_get_notify_priority(item)),
            str(item.get("occurred_at", "") or "").strip(),
            str(item.get("title", "") or "").strip(),
        )
    )
    return result


def _aggregate_notify_entries(entries: list[dict]) -> list[dict]:
    grouped = {}
    order = []
    for entry in entries or []:
        group_key = _build_notify_group_key(entry)
        priority = int(_get_notify_priority(entry))
        current = grouped.get(group_key)
        if current is None:
            payload = dict(entry)
            payload["group_key"] = group_key
            payload["aggregate_count"] = int(entry.get("aggregate_count", 0) or 1)
            payload["_priority"] = priority
            grouped[group_key] = payload
            order.append(group_key)
            continue

        total_count = int(current.get("aggregate_count", 1) or 1) + int(entry.get("aggregate_count", 0) or 1)
        current_title = str(current.get("title", "") or "").strip()
        incoming_title = str(entry.get("title", "") or "").strip()
        current_occurred = str(current.get("occurred_at", "") or "").strip()
        incoming_occurred = str(entry.get("occurred_at", "") or "").strip()
        if priority > int(current.get("_priority", 0) or 0) or (
            priority == int(current.get("_priority", 0) or 0) and incoming_occurred >= current_occurred
        ):
            for key, value in dict(entry).items():
                current[key] = value
            current["group_key"] = group_key
            current["_priority"] = priority
            current["aggregate_count"] = total_count
        elif incoming_title and incoming_title != current_title:
            current["detail"] = f"{str(current.get('detail', '') or '').strip()} 同类提醒还包括：{incoming_title}。".strip()
            current["aggregate_count"] = total_count
        else:
            current["aggregate_count"] = total_count

    result = []
    for key in order:
        payload = grouped[key]
        payload.pop("_priority", None)
        result.append(payload)
    return result


def _build_send_entry(entry: dict, aggregate_count: int, notify_mode: str) -> dict:
    payload = dict(entry or {})
    safe_count = max(1, int(aggregate_count or payload.get("aggregate_count", 1) or 1))
    payload["aggregate_count"] = safe_count
    title = _normalize_text(payload.get("title", "提醒"))
    detail = _normalize_text(payload.get("detail", ""))
    if notify_mode == "escalation":
        payload["title"] = f"{title}（升级提醒）"
        if detail:
            payload["detail"] = f"{detail} 同类提醒已连续出现 {safe_count} 次，当前按升级提醒处理。"
        payload["notify_mode_text"] = "同类提醒在冷却窗口内出现升级，已按升级提醒立即推送"
    elif safe_count > 1:
        payload["title"] = f"{title}（持续）"
        if detail:
            payload["detail"] = f"{detail} 同类提醒近一轮累计 {safe_count} 次，已合并发送。"
        payload["notify_mode_text"] = "同类提醒已聚合，避免短时间内重复刷屏"
    else:
        payload["notify_mode_text"] = ""
    return payload


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
    pending = _aggregate_notify_entries(pick_notify_entries(entries, config, state_file=state_file))
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
        group_key = str(entry.get("group_key", "") or _build_notify_group_key(entry)).strip()
        current_priority = int(_get_notify_priority(entry))
        current_occured_at = _parse_time(entry.get("occurred_at", "")) or datetime.now()
        observed_count = int(entry.get("aggregate_count", 0) or 1)
        for channel_key, channel_name, channel_value in channels:
            if _is_within_cooldown(entry, state, config.notify_cooldown_min, channel_key=channel_key):
                continue
            group_state = _read_group_state(state, channel_key, group_key)
            pending_count = int(group_state.get("pending_count", 0) or 0)
            total_count = max(1, observed_count + pending_count)
            last_group_time = group_state.get("last_time")
            within_group_cooldown = (
                last_group_time is not None
                and current_occured_at - last_group_time < timedelta(minutes=max(1, int(config.notify_cooldown_min)))
            )
            notify_mode = "normal"
            if within_group_cooldown:
                last_priority = int(group_state.get("last_priority", 0) or 0)
                if current_priority > last_priority:
                    notify_mode = "escalation"
                elif total_count >= 3 and current_priority >= 4:
                    notify_mode = "aggregate"
                else:
                    _increase_group_pending(state, channel_key, group_key, observed_count)
                    continue
            elif total_count > 1:
                notify_mode = "aggregate"

            send_entry = _build_send_entry(entry, total_count, notify_mode)
            if channel_key == "dingtalk":
                ok, detail = send_dingtalk(send_entry, channel_value)
            else:
                ok, detail = send_pushplus(send_entry, channel_value)

            if ok:
                if signature:
                    state[_build_channel_state_key(channel_key, signature)] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _mark_group_sent(state, channel_key, group_key, current_priority)
                entry_sent = True
                sent_channel_count += 1
                if notify_mode == "escalation":
                    messages.append(f"{entry_title} 已作为升级提醒推送到{channel_name}")
                elif total_count > 1:
                    messages.append(f"{entry_title} 已合并 {total_count} 条同类提醒后推送到{channel_name}")
                else:
                    messages.append(f"{entry_title} 已推送到{channel_name}")
            else:
                errors.append(f"{entry_title} 推送到{channel_name}失败：{detail}")
        if entry_sent:
            sent_count += 1

    if messages:
        _update_last_result(state, "；".join(messages), _normalize_text)
    elif errors:
        _update_last_result(state, "；".join(errors), _normalize_text)

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
        _update_last_result(state, "；".join(messages), _normalize_text)
    elif errors:
        _update_last_result(state, "；".join(errors), _normalize_text)
    _write_state(state, state_file=state_file)
    return {"messages": messages, "errors": errors}


def send_ai_brief_notification(result: dict, snapshot: dict, config: MetalMonitorConfig, state_file: Path | None = None) -> dict:
    if not bool(config.ai_push_enabled):
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "ai_push_disabled"}

    # ── S-004 修复：AI 研判推送冷却保护 ──
    # 最小间隔 = max(自动研判间隔/2, 20分钟)，防止频繁触发刷屏
    auto_interval_min = int(getattr(config, "ai_auto_interval_min", 60) or 60)
    ai_brief_cooldown_min = max(20, auto_interval_min // 2)
    state = _read_state(state_file=state_file)
    last_ai_brief_time = _parse_time(state.get("ai_brief::last_push_time", ""))
    now = datetime.now()
    if last_ai_brief_time is not None and now - last_ai_brief_time < timedelta(minutes=ai_brief_cooldown_min):
        remaining = ai_brief_cooldown_min - int((now - last_ai_brief_time).total_seconds() / 60)
        return {
            "sent_count": 0,
            "messages": [],
            "errors": [],
            "skipped_reason": f"ai_brief_cooldown（冷却中，还需 {remaining} 分钟）",
        }

    entry = _build_ai_brief_entry(result, snapshot, config)
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
        # 推送成功后记录时间，供下次冷却判断
        state["ai_brief::last_push_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
        _update_last_result(state, "；".join(messages), _normalize_text)
    elif errors:
        _update_last_result(state, "；".join(errors), _normalize_text)
    _write_state(state, state_file=state_file)
    return {"sent_count": len(messages), "messages": messages, "errors": errors}


def _build_learning_digest_hash(report: dict) -> str:
    payload = {
        "governance_summary_text": _normalize_text(((report or {}).get("governance_summary", {}) or {}).get("summary_text", "") or ""),
        "feedback_summary_text": _normalize_text(((report or {}).get("feedback_summary", {}) or {}).get("summary_text", "") or ""),
        "active_rules": list((report or {}).get("active_rules", []) or [])[:3],
        "watch_rules": list((report or {}).get("watch_rules", []) or [])[:3],
        "frozen_rules": list((report or {}).get("frozen_rules", []) or [])[:3],
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _has_meaningful_learning_report(report: dict) -> bool:
    active_rules = [str(item).strip() for item in list((report or {}).get("active_rules", []) or []) if str(item).strip()]
    watch_rules = [str(item).strip() for item in list((report or {}).get("watch_rules", []) or []) if str(item).strip()]
    frozen_rules = [str(item).strip() for item in list((report or {}).get("frozen_rules", []) or []) if str(item).strip()]
    summary_text = _normalize_text((report or {}).get("summary_text", "") or "")
    feedback_total_count = int((((report or {}).get("feedback_summary", {}) or {}).get("total_count", 0) or 0))
    if active_rules or watch_rules or frozen_rules:
        return True
    if feedback_total_count > 0:
        return True
    if not summary_text or "当前还没有学习摘要" in summary_text:
        return False
    # Q-003 audit 回滚：经过验证，原 or 逻辑是正确的——
    # "只要有任意一个类别的规则数量不是 0 条，就认为报告有内容"。
    # 改为 and 是错误的（过于严格，导致合法报告被过滤）。
    return "启用 0 条" not in summary_text or "观察 0 条" not in summary_text or "冻结 0 条" not in summary_text



def send_learning_report_notification(
    report: dict,
    config: MetalMonitorConfig,
    state_file: Path | None = None,
    now: datetime | None = None,
) -> dict:
    if not bool(getattr(config, "learning_push_enabled", False)):
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "learning_push_disabled"}
    if not _has_meaningful_learning_report(report):
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "learning_report_empty"}

    state = _read_state(state_file=state_file)
    digest_hash = _build_learning_digest_hash(report)
    digest_state = _read_learning_digest_state(state)
    current = now or datetime.now()
    min_interval_hour = max(1, int(getattr(config, "learning_push_min_interval_hour", 12) or 12))
    last_time = digest_state.get("last_time")
    last_hash = str(digest_state.get("last_hash", "") or "").strip()
    if last_hash == digest_hash:
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "learning_report_unchanged"}
    if last_time is not None and current - last_time < timedelta(hours=min_interval_hour):
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "learning_report_rate_limited"}

    entry = _build_learning_report_entry(report)
    entry["signature"] = f"learning::{digest_hash[:16]}"
    messages = []
    errors = []
    if str(config.dingtalk_webhook or "").strip():
        ok, detail = send_dingtalk(entry, config.dingtalk_webhook)
        if ok:
            messages.append("知识库学习摘要已推送到钉钉")
        else:
            errors.append(f"知识库学习摘要推送到钉钉失败：{detail}")
    if str(config.pushplus_token or "").strip():
        ok, detail = send_pushplus(entry, config.pushplus_token)
        if ok:
            messages.append("知识库学习摘要已推送到 PushPlus")
        else:
            errors.append(f"知识库学习摘要推送到 PushPlus 失败：{detail}")
    if messages:
        _mark_learning_digest_sent(state, digest_hash, sent_at=current)
        _update_last_result(state, "；".join(messages), _normalize_text)
    elif errors:
        _update_last_result(state, "；".join(errors), _normalize_text)
    _write_state(state, state_file=state_file)
    return {"sent_count": len(messages), "messages": messages, "errors": errors}


def get_notification_status(config: MetalMonitorConfig, state_file: Path | None = None) -> dict:
    state = _read_state(state_file=state_file)
    channels = []
    channels.append("钉钉已配置" if str(config.dingtalk_webhook or "").strip() else "钉钉未配置")
    channels.append("PushPlus已配置" if str(config.pushplus_token or "").strip() else "PushPlus未配置")
    return {
        "channels_text": " | ".join(channels),
        "cooldown_text": f"冷却 {int(config.notify_cooldown_min)} 分钟",
        "last_result_text": str(state.get("last_result_text", "最近还没有推送记录。") or "最近还没有推送记录。").strip(),
        "last_result_time": str(state.get("last_result_time", "--") or "--").strip(),
    }
