"""
消息推送：支持钉钉 Webhook 与 PushPlus。

网络 I/O 架构说明
-----------------
所有对外的 HTTP 推送（send_dingtalk / send_pushplus）均通过
NotificationWorker 后台线程执行，调用方（MonitorWorker / AiBriefWorker）
不再被网络延迟或 time.sleep 阻塞。

冷却状态（notify_state）仍然在调用方线程同步写入（乐观写入），
保证下一个刷新周期的去重判断立即生效，无需等待 HTTP 请求完成。
"""
from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib import error, request

from notification_worker import get_notification_worker

logger = logging.getLogger(__name__)

_LEARNING_HEALTH_STATUS_COOLDOWN_HOURS = 24

from app_config import MetalMonitorConfig
from knowledge_feedback import get_feedback_push_policy
from notification_payloads import (
    _build_ai_brief_entry,
    _build_learning_health_entry,
    _build_learning_report_entry,
    _build_markdown,
    _build_user_facing_title,
    _normalize_text,
)
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
    _mark_learning_health_sent,
    _parse_time,
    _read_learning_digest_state,
    _read_learning_health_state,
    _read_group_state,
    _read_state,
    _should_notify_entry,
    _update_last_result,
    _write_state,
)
from signal_enums import AlertTone
from signal_protocol import normalize_signal_meta


_DND_ALLOWED_CATEGORIES = {"mt5", "source"}
_TRANSITION_ONLY_WINDOW_MINUTES = {
    "macro": 180,
    "session": 240,
    "source": 240,
    "mt5": 240,
}
_STATE_TRANSITION_WINDOW_MINUTES = {
    "spread": 180,
    "recovery": 180,
}
_FEEDBACK_POLICY_CATEGORIES = {"structure", "opportunity", "ai"}


def _is_hour_in_window(current: datetime, start_hour: int, end_hour: int) -> bool:
    current_hour = int(current.hour)
    start = max(0, min(23, int(start_hour)))
    end = max(0, min(23, int(end_hour)))
    if start == end:
        return False
    if start < end:
        return start <= current_hour < end
    return current_hour >= start or current_hour < end


def _entry_occured_at(entry: dict, now: datetime | None = None) -> datetime:
    return now or _parse_time(entry.get("occurred_at", "")) or datetime.now()


def _is_structure_entry_expired(entry: dict, config: MetalMonitorConfig, evaluated_at: datetime) -> bool:
    category = str(entry.get("category", "") or "").strip().lower()
    if category != "structure":
        return False
    occurred_at = _parse_time(entry.get("occurred_at", ""))
    if occurred_at is None:
        return False
    validity_sec = max(60, int(getattr(config, "refresh_interval_sec", 30) or 30) * 3)
    return evaluated_at - occurred_at > timedelta(seconds=validity_sec)


def _is_dnd_suppressed(entry: dict, config: MetalMonitorConfig, current: datetime) -> bool:
    if not bool(getattr(config, "notify_dnd_enabled", True)):
        return False
    if not _is_hour_in_window(
        current,
        int(getattr(config, "notify_dnd_start_hour", 0) or 0),
        int(getattr(config, "notify_dnd_end_hour", 7) or 7),
    ):
        return False
    category = str(entry.get("category", "") or "").strip().lower()
    return category not in _DND_ALLOWED_CATEGORIES


def _is_overnight_spread_suppressed(entry: dict, config: MetalMonitorConfig, current: datetime) -> bool:
    if not bool(getattr(config, "overnight_spread_guard_enabled", True)):
        return False
    category = str(entry.get("category", "") or "").strip().lower()
    title = str(entry.get("title", "") or "").strip()
    if category != "spread" and "点差" not in title:
        return False
    if not _is_hour_in_window(
        current,
        int(getattr(config, "overnight_spread_guard_start_hour", 5) or 5),
        int(getattr(config, "overnight_spread_guard_end_hour", 7) or 7),
    ):
        return False
    importance_text = str(entry.get("event_importance_text", "") or "").strip()
    if "高影响" in importance_text:
        return False
    return True


def _is_transition_only_entry(entry: dict) -> bool:
    category = str(entry.get("category", "") or "").strip().lower()
    if category == "macro":
        return not bool(entry.get("macro_has_result", False))
    return category in {"session", "source", "mt5"}


def _is_transition_only_suppressed(
    entry: dict,
    state: dict,
    channel_key: str,
    current: datetime,
) -> bool:
    if not _is_transition_only_entry(entry):
        return False
    category = str(entry.get("category", "") or "").strip().lower()
    window_min = int(_TRANSITION_ONLY_WINDOW_MINUTES.get(category, 0) or 0)
    if window_min <= 0:
        return False
    group_key = _build_notify_group_key(entry)
    group_state = _read_group_state(state, channel_key, group_key)
    last_time = group_state.get("last_time")
    if last_time is None:
        return False
    if current - last_time >= timedelta(minutes=window_min):
        return False
    current_priority = int(_get_notify_priority(entry))
    last_priority = int(group_state.get("last_priority", 0) or 0)
    return current_priority <= last_priority


def _build_state_fingerprint(entry: dict) -> str:
    category = str(entry.get("category", "") or "").strip().lower()
    title = _normalize_text(entry.get("title", ""))
    detail = _normalize_text(entry.get("detail", ""))
    tone = _normalize_text(entry.get("tone", ""))
    trade_grade = _normalize_text(entry.get("trade_grade", ""))
    alert_state_text = _normalize_text(entry.get("alert_state_text", ""))
    event_note = _normalize_text(entry.get("event_note", ""))
    return " | ".join(part for part in (category, title, detail, tone, trade_grade, alert_state_text, event_note) if part)


def _is_same_state_transition_suppressed(
    entry: dict,
    state: dict,
    channel_key: str,
    current: datetime,
) -> bool:
    category = str(entry.get("category", "") or "").strip().lower()
    window_min = int(_STATE_TRANSITION_WINDOW_MINUTES.get(category, 0) or 0)
    if window_min <= 0:
        return False
    group_key = _build_notify_group_key(entry)
    group_state = _read_group_state(state, channel_key, group_key)
    last_time = group_state.get("last_time")
    if last_time is None:
        return False
    if current - last_time >= timedelta(minutes=window_min):
        return False
    current_priority = int(_get_notify_priority(entry))
    last_priority = int(group_state.get("last_priority", 0) or 0)
    if current_priority > last_priority:
        return False
    current_fingerprint = _build_state_fingerprint(entry)
    if not current_fingerprint:
        return False
    return current_fingerprint == str(group_state.get("last_fingerprint", "") or "").strip()


def _to_float(value) -> float:
    try:
        if value in ("", None):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load_feedback_push_policy(state_file: Path | None = None) -> dict:
    # 测试传入隔离 state_file 时不读取生产知识库，避免测试之间互相污染。
    if state_file is not None:
        return {}
    try:
        policy = get_feedback_push_policy()
    except Exception:  # noqa: BLE001
        return {}
    return dict(policy) if isinstance(policy, dict) else {}


def _entry_signal_score(entry: dict) -> float:
    return max(
        _to_float(entry.get("opportunity_score")),
        _to_float(entry.get("signal_score")),
        _to_float(entry.get("model_win_probability")) * 100.0,
    )


def _entry_rr(entry: dict) -> float:
    return max(
        _to_float(entry.get("opportunity_risk_reward_ratio")),
        _to_float(entry.get("risk_reward_ratio")),
    )


def _has_feedback_policy_event_risk(entry: dict) -> bool:
    importance = str(entry.get("event_importance_text", "") or "").strip()
    return bool(entry.get("event_applies", False)) or importance in {"高影响", "high", "高"}


def _is_feedback_policy_promoted(entry: dict, policy: dict) -> bool:
    if not bool((policy or {}).get("advance_warning", False)):
        return False
    category = str(entry.get("category", "") or "").strip().lower()
    if category != "structure":
        return False
    stage = str(entry.get("structure_entry_stage", "") or "").strip().lower()
    side = str(entry.get("signal_side", "") or "").strip().lower()
    if stage != "near_zone" or side not in {"long", "short"}:
        return False
    if _has_feedback_policy_event_risk(entry):
        return False
    rr = _entry_rr(entry)
    score = _entry_signal_score(entry)
    threshold = 82.0 + float((policy or {}).get("min_score_boost", 0) or 0)
    return rr >= 1.6 and score >= max(72.0, threshold)


def _is_feedback_policy_suppressed(entry: dict, policy: dict) -> bool:
    if not bool((policy or {}).get("active", False)):
        return False
    category = str(entry.get("category", "") or "").strip().lower()
    if category not in _FEEDBACK_POLICY_CATEGORIES:
        return False
    stage = str(entry.get("structure_entry_stage", "") or "").strip().lower()
    score = _entry_signal_score(entry)
    rr = _entry_rr(entry)
    min_score = 72.0 + float((policy or {}).get("min_score_boost", 0) or 0)

    if bool(policy.get("tighten_risk", False)):
        if 0 < rr < 1.6:
            return True
        if _has_feedback_policy_event_risk(entry) and stage != "inside_zone":
            return True

    if bool(policy.get("reduce_noise", False)):
        if category == "structure" and stage != "inside_zone" and score < min_score:
            return True
        if category == "opportunity" and score > 0 and score < min_score:
            return True
        if category == "ai" and entry.get("ai_rule_eligible") is False and score < min_score:
            return True
    return False


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
    evaluated_at = now or datetime.now()
    feedback_policy = _load_feedback_push_policy(state_file=state_file)
    result = []
    for entry in entries or []:
        if not _should_notify_entry(entry) and not _is_feedback_policy_promoted(entry, feedback_policy):
            continue
        if _is_feedback_policy_suppressed(entry, feedback_policy):
            continue
        current = _entry_occured_at(entry, now=now)
        if _is_structure_entry_expired(entry, config, evaluated_at):
            continue
        if _is_dnd_suppressed(entry, config, current):
            continue
        if _is_overnight_spread_suppressed(entry, config, current):
            continue
        due_channels = [
            channel_key
            for channel_key, _channel_name, _channel_value in channels
            if not _is_within_cooldown(entry, state, config.notify_cooldown_min, now=current, channel_key=channel_key)
            and not _is_transition_only_suppressed(entry, state, channel_key, current)
            and not _is_same_state_transition_suppressed(entry, state, channel_key, current)
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
    payload["raw_title"] = _normalize_text(payload.get("title", "提醒"))
    title = _build_user_facing_title(payload)
    payload["title"] = title
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


def send_notifications(
    entries: list[dict],
    config: MetalMonitorConfig,
    state_file: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """筛选、聚合并异步投递推送任务。

    冷却/去重状态在本函数内同步写入（乐观写入），HTTP 发送由
    NotificationWorker 后台线程执行，调用方不会被网络 I/O 阻塞。
    """
    pending = _aggregate_notify_entries(pick_notify_entries(entries, config, state_file=state_file))
    if not pending:
        return {"sent_count": 0, "sent_channel_count": 0, "messages": [], "errors": []}

    state = _read_state(state_file=state_file)
    sent_count = 0
    sent_channel_count = 0
    messages = []
    errors = []
    channels = _configured_channels(config)
    worker = get_notification_worker()
    effective_now = now
    if effective_now is None and pending:
        effective_now = max((_entry_occured_at(entry) for entry in pending), default=datetime.now())
    effective_now = effective_now or datetime.now()

    for entry in pending:
        entry_title = _normalize_text(entry.get("title", "提醒"))
        signature = str(entry.get("signature", "") or "").strip()
        entry_sent = False
        group_key = str(entry.get("group_key", "") or _build_notify_group_key(entry)).strip()
        current_priority = int(_get_notify_priority(entry))
        current_occured_at = now or _parse_time(entry.get("occurred_at", "")) or datetime.now()
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

            # ── 乐观写入：推送任务入队前立即更新冷却状态 ─────────────────
            # 不等待 HTTP 实际完成，保证下一个刷新周期能正确判断冷却。
            # 若后台线程最终推送失败，仅记录日志，不回滚冷却状态
            # （冷却期内少推一次，优于因重试失败导致的刷屏）。
            now_str = current_occured_at.strftime("%Y-%m-%d %H:%M:%S")
            if signature:
                state[_build_channel_state_key(channel_key, signature)] = now_str
            _mark_group_sent(
                state,
                channel_key,
                group_key,
                current_priority,
                fingerprint=_build_state_fingerprint(entry),
                sent_at=current_occured_at,
            )
            entry_sent = True
            sent_channel_count += 1

            # ── 拼装推送函数和入队 ────────────────────────────────────────
            send_fn = send_dingtalk if channel_key == "dingtalk" else send_pushplus
            _entry_title_cap = entry_title  # 闭包捕获
            _channel_name_cap = channel_name
            _notify_mode_cap = notify_mode
            _total_count_cap = total_count

            def _on_result(
                ok: bool,
                detail: str,
                _title: str = _entry_title_cap,
                _ch: str = _channel_name_cap,
                _mode: str = _notify_mode_cap,
                _cnt: int = _total_count_cap,
            ) -> None:
                if ok:
                    if _mode == "escalation":
                        logger.info("[推送] %s 已作为升级提醒推送到%s", _title, _ch)
                    elif _cnt > 1:
                        logger.info("[推送] %s 已合并 %d 条后推送到%s", _title, _cnt, _ch)
                    else:
                        logger.info("[推送] %s 已推送到%s", _title, _ch)
                else:
                    logger.warning("[推送] %s 推送到%s失败：%s", _title, _ch, detail)

            worker.enqueue({"send_fn": send_fn, "args": (send_entry, channel_value), "on_result": _on_result})

            # 给调用方返回乐观结果（与实际推送结果一致的概率极高）
            if notify_mode == "escalation":
                messages.append(f"{entry_title} 已作为升级提醒投递到{channel_name}")
            elif total_count > 1:
                messages.append(f"{entry_title} 已合并 {total_count} 条同类提醒后投递到{channel_name}")
            else:
                messages.append(f"{entry_title} 已投递到{channel_name}")

        if entry_sent:
            sent_count += 1

    if messages:
        _update_last_result(state, "；".join(messages), _normalize_text, now=effective_now)

    _write_state(state, state_file=state_file, now=effective_now)
    return {"sent_count": sent_count, "sent_channel_count": sent_channel_count, "messages": messages, "errors": errors}


def send_test_notification(config: MetalMonitorConfig, state_file: Path | None = None) -> dict:
    state = _read_state(state_file=state_file)
    entry = {
        "occurred_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "category": "test",
        "title": "贵金属监控推送测试",
        "detail": "这是一条测试消息。若你收到此提醒，说明独立项目的消息推送链已经打通。",
        "tone": AlertTone.ACCENT.value,
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


def send_ai_brief_notification(
    result: dict,
    snapshot: dict,
    config: MetalMonitorConfig,
    state_file: Path | None = None,
    is_opportunity: bool = False,
) -> dict:
    """推送 AI 研判。

    is_opportunity=True 时走「机会快速通道」：
    - 使用独立的冷却 key（ai_brief::opportunity_push_time）
    - 冷却阈值固定为 5 分钟，不受普通研判冷却影响
    - 适用于 R/R≥2.0 的高质量出手信号
    """
    if not bool(config.ai_push_enabled):
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "ai_push_disabled"}

    normalized_signal_meta = normalize_signal_meta((result or {}).get("signal_meta"))
    if str(normalized_signal_meta.get("action", "neutral") or "neutral").strip().lower() == "neutral":
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "ai_neutral_suppressed"}

    # ── 机会快速通道 vs 普通冷却 ──────────────────────────────────────
    auto_interval_min = int(getattr(config, "ai_auto_interval_min", 60) or 60)
    if is_opportunity:
        # 高机会信号：5 分钟短冷却，独立 key，不影响普通研判节奏
        cooldown_key = "ai_brief::opportunity_push_time"
        ai_brief_cooldown_min = 5
        cooldown_label = "机会快速通道"
    else:
        # 普通定时研判：max(20, interval/2) 长冷却，防刷屏
        cooldown_key = "ai_brief::last_push_time"
        ai_brief_cooldown_min = max(20, auto_interval_min // 2)
        cooldown_label = "常规研判"

    state = _read_state(state_file=state_file)
    last_ai_brief_time = _parse_time(state.get(cooldown_key, ""))
    now = datetime.now()
    if last_ai_brief_time is not None and now - last_ai_brief_time < timedelta(minutes=ai_brief_cooldown_min):
        remaining = ai_brief_cooldown_min - int((now - last_ai_brief_time).total_seconds() / 60)
        return {
            "sent_count": 0,
            "messages": [],
            "errors": [],
            "skipped_reason": f"{cooldown_label}_cooldown（冷却中，还需 {remaining} 分钟）",
        }

    entry = _build_ai_brief_entry(result, snapshot, config)
    worker = get_notification_worker()
    enqueued_count = 0

    def _make_ai_result_cb(channel_label: str) -> object:
        def _cb(ok: bool, detail: str) -> None:
            if ok:
                logger.info("[AI研判] 已推送到%s", channel_label)
            else:
                logger.warning("[AI研判] 推送到%s失败：%s", channel_label, detail)
        return _cb

    if str(config.dingtalk_webhook or "").strip():
        worker.enqueue({"send_fn": send_dingtalk, "args": (entry, config.dingtalk_webhook), "on_result": _make_ai_result_cb("钉钉")})
        enqueued_count += 1
    if str(config.pushplus_token or "").strip():
        worker.enqueue({"send_fn": send_pushplus, "args": (entry, config.pushplus_token), "on_result": _make_ai_result_cb("PushPlus")})
        enqueued_count += 1

    if enqueued_count > 0:
        # 乐观写入：入队即视为将要推送，立即更新冷却时间戳
        state[cooldown_key] = now.strftime("%Y-%m-%d %H:%M:%S")
        if is_opportunity:
            state["ai_brief::last_push_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
        _update_last_result(state, f"AI 研判已投递（{cooldown_label}）", _normalize_text)
        _write_state(state, state_file=state_file)
        messages = [f"AI 研判已投递到 {enqueued_count} 个渠道"]
        return {"sent_count": enqueued_count, "messages": messages, "errors": [], "is_opportunity": is_opportunity}

    _write_state(state, state_file=state_file)
    return {"sent_count": 0, "messages": [], "errors": ["未配置任何推送渠道"], "is_opportunity": is_opportunity}


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
    worker = get_notification_worker()
    enqueued_count = 0

    def _make_lr_result_cb(channel_label: str) -> object:
        def _cb(ok: bool, detail: str) -> None:
            if ok:
                logger.info("[学习摘要] 已推送到%s", channel_label)
            else:
                logger.warning("[学习摘要] 推送到%s失败：%s", channel_label, detail)
        return _cb

    if str(config.dingtalk_webhook or "").strip():
        worker.enqueue({"send_fn": send_dingtalk, "args": (entry, config.dingtalk_webhook), "on_result": _make_lr_result_cb("钉钉")})
        enqueued_count += 1
    if str(config.pushplus_token or "").strip():
        worker.enqueue({"send_fn": send_pushplus, "args": (entry, config.pushplus_token), "on_result": _make_lr_result_cb("PushPlus")})
        enqueued_count += 1

    if enqueued_count > 0:
        # 乐观写入：入队即视为将要推送，防止重复触发
        _mark_learning_digest_sent(state, digest_hash, sent_at=current)
        _update_last_result(state, "知识库学习摘要已投递", _normalize_text)

    _write_state(state, state_file=state_file)
    messages = [f"知识库学习摘要已投递到 {enqueued_count} 个渠道"] if enqueued_count > 0 else []
    return {"sent_count": enqueued_count, "messages": messages, "errors": []}


def _build_learning_health_hash(report: dict) -> str:
    payload = {
        "status_key": _normalize_text((report or {}).get("status_key", "") or ""),
        "summary_text": _normalize_text((report or {}).get("summary_text", "") or ""),
        "latest_rule_text": _normalize_text((report or {}).get("latest_rule_text", "") or ""),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _learning_health_group(status_key: str) -> str:
    clean = _normalize_text(status_key)
    if clean == "productive":
        return "productive"
    if clean == "deep_mining_error":
        return "error"
    return "degraded"


def send_learning_health_notification(
    report: dict,
    config: MetalMonitorConfig,
    state_file: Path | None = None,
    now: datetime | None = None,
) -> dict:
    if not bool(getattr(config, "learning_push_enabled", False)):
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "learning_push_disabled"}

    status_key = str((report or {}).get("status_key", "") or "").strip()
    summary_text = _normalize_text((report or {}).get("summary_text", "") or "")
    if not status_key or not summary_text:
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "learning_health_empty"}

    state = _read_state(state_file=state_file)
    digest_hash = _build_learning_health_hash(report)
    health_state = _read_learning_health_state(state)
    current = now or datetime.now()
    last_hash = str(health_state.get("last_hash", "") or "").strip()
    last_status_key = str(health_state.get("last_status_key", "") or "").strip()
    last_time = health_state.get("last_time")
    current_group = _learning_health_group(status_key)
    last_group = _learning_health_group(last_status_key) if last_status_key else ""
    if last_hash == digest_hash:
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "learning_health_unchanged"}
    if last_status_key == status_key:
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "learning_health_same_status"}
    if (
        last_time is not None
        and current - last_time < timedelta(hours=_LEARNING_HEALTH_STATUS_COOLDOWN_HOURS)
        and current_group == "degraded"
        and last_group == "degraded"
    ):
        return {"sent_count": 0, "messages": [], "errors": [], "skipped_reason": "learning_health_transition_cooldown"}

    entry = _build_learning_health_entry(report)
    entry["signature"] = f"learning_health::{digest_hash[:16]}"
    worker = get_notification_worker()
    enqueued_count = 0

    def _make_lr_result_cb(channel_label: str) -> object:
        def _cb(ok: bool, detail: str) -> None:
            if ok:
                logger.info("[学习状态] 已推送到%s", channel_label)
            else:
                logger.warning("[学习状态] 推送到%s失败：%s", channel_label, detail)
        return _cb

    if str(config.dingtalk_webhook or "").strip():
        worker.enqueue({"send_fn": send_dingtalk, "args": (entry, config.dingtalk_webhook), "on_result": _make_lr_result_cb("钉钉")})
        enqueued_count += 1
    if str(config.pushplus_token or "").strip():
        worker.enqueue({"send_fn": send_pushplus, "args": (entry, config.pushplus_token), "on_result": _make_lr_result_cb("PushPlus")})
        enqueued_count += 1

    if enqueued_count > 0:
        _mark_learning_health_sent(state, digest_hash, status_key, sent_at=current)
        _update_last_result(state, f"自动学习状态变化已投递：{status_key}", _normalize_text, now=current)
    _write_state(state, state_file=state_file, now=current)
    messages = [f"自动学习状态变化已投递到 {enqueued_count} 个渠道"] if enqueued_count > 0 else []
    return {"sent_count": enqueued_count, "messages": messages, "errors": []}


def get_notification_status(config: MetalMonitorConfig, state_file: Path | None = None) -> dict:
    state = _read_state(state_file=state_file)
    channels = []
    channels.append("钉钉已配置" if str(config.dingtalk_webhook or "").strip() else "钉钉未配置")
    channels.append("PushPlus已配置" if str(config.pushplus_token or "").strip() else "PushPlus未配置")
    return {
        "channels_text": " | ".join(channels),
        "cooldown_text": (
            f"冷却 {int(config.notify_cooldown_min)} 分钟"
            f" | DND {'开' if bool(getattr(config, 'notify_dnd_enabled', True)) else '关'}"
            f" {int(getattr(config, 'notify_dnd_start_hour', 0) or 0):02d}:00-"
            f"{int(getattr(config, 'notify_dnd_end_hour', 7) or 7):02d}:00"
        ),
        "last_result_text": str(state.get("last_result_text", "最近还没有推送记录。") or "最近还没有推送记录。").strip(),
        "last_result_time": str(state.get("last_result_time", "--") or "--").strip(),
    }
