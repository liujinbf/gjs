from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app_config import MetalMonitorConfig, PROJECT_DIR
from runtime_utils import parse_time as _parse_time_impl
from knowledge_base import kv_get, kv_set  # 3.2 修复：通知状态持久化走 SQLite KV

RUNTIME_DIR = PROJECT_DIR / ".runtime"
NOTIFY_STATE_FILE = RUNTIME_DIR / "notify_state.json"  # 保留用于存量迁移
_KV_KEY = "notify_state"  # system_state_kv 中的键名


# P-004 修复：姓 _parse_time 内部使用公共实现，保持对本模块内其它调用者透明
def _parse_time(value: str) -> datetime | None:
    return _parse_time_impl(value)


def _read_state(state_file: Path | None = None) -> dict:
    # 当显式传入 state_file 时（测试隔离路径）直接读 JSON，不走 SQLite
    if state_file is not None:
        target = Path(state_file)
        if target.exists():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                pass
        return {}
    # 生产路径：优先读 SQLite KV，不存在时从旧 JSON 文件迁移（兼容升级）
    payload = kv_get(_KV_KEY)
    if payload is not None:
        return payload if isinstance(payload, dict) else {}
    if NOTIFY_STATE_FILE.exists():
        try:
            data = json.loads(NOTIFY_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                kv_set(_KV_KEY, data)  # 一次性迁移至 SQLite
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _purge_expired_notify_records(state: dict, max_age_days: int = 7) -> int:
    """M-006 修复：清理超过 max_age_days 天的 notified:: 冷却记录，防止无限增长。"""
    cutoff = datetime.now() - timedelta(days=max(1, int(max_age_days)))
    expired_keys = []
    for key, value in list(state.items()):
        if not str(key).startswith("notified::") and not str(key).startswith("group::"):
            continue
        # 判断 group:: 的特定字段
        if str(key).endswith("::last_time") or str(key).startswith("notified::"):
            last_time = _parse_time(str(value or ""))
            if last_time is not None and last_time < cutoff:
                expired_keys.append(key)
            elif last_time is None and isinstance(value, str):
                # 字符串值但解析失败，可能是旧格式，保守跳过
                pass
    for key in expired_keys:
        del state[key]
    return len(expired_keys)


def _write_state(state: dict, state_file: Path | None = None) -> None:
    _purge_expired_notify_records(state, max_age_days=7)
    if state_file is not None:
        # 显式传入 state_file 时写 JSON（测试隔离路径）
        Path(state_file).parent.mkdir(parents=True, exist_ok=True)
        Path(state_file).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    # 生产路径：写 SQLite KV，不再碎写 JSON
    kv_set(_KV_KEY, state)




def _update_last_result(state: dict, text: str, normalize_text) -> None:
    state["last_result_text"] = normalize_text(text)
    state["last_result_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _should_notify_entry(entry: dict) -> bool:
    return _get_notify_priority(entry) > 0


def _normalize_event_importance(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "高影响", "高"}:
        return "high"
    if text in {"low", "低影响", "低"}:
        return "low"
    return "medium"


def _get_notify_priority(entry: dict) -> int:
    category = str(entry.get("category", "") or "").strip()
    title = str(entry.get("title", "") or "").strip()
    tone = str(entry.get("tone", "neutral") or "neutral").strip().lower()
    # 保留原始字段局以区分"无事件"和"明确低影响事件"
    raw_importance_text = str(entry.get("event_importance_text", "") or "").strip()
    importance = _normalize_event_importance(raw_importance_text)

    if category == "mt5":
        return 5
    if category == "session":
        return 3
    if category == "source":
        return 4 if tone == "warning" else 2
    if category == "structure":
        rr_ratio = float(entry.get("risk_reward_ratio", 0.0) or 0.0)
        if rr_ratio >= 2.0:
            return 3
        if rr_ratio >= 1.3:
            return 2
        return 0
    if category == "recovery":
        return 3 if importance == "high" else 2
    if category == "macro":
        # 高影响事件 → 高优先级；中等/未知 → 中优先级（不再静默过滤）
        return 4 if importance == "high" else 2
    if category == "spread" or "点差" in title:
        if tone == "warning":
            return 5 if importance == "high" else 4
        if tone == "accent":
            if importance == "high":
                return 4
            if importance == "medium":
                return 2
            if importance == "low":
                # 明确标注低影响事件 → 不推，防刷屏
                return 0
            # raw 为空（无活跃事件）→ 低优先级 1，不再完全屏蔽点差提醒
            return 1 if not raw_importance_text else 0
        return 1
    return 0



def _extract_entry_symbol(entry: dict) -> str:
    symbol = str(entry.get("symbol", "") or "").strip().upper()
    if symbol:
        return symbol
    title = str(entry.get("title", "") or "").strip()
    if "点差" in title and title:
        return title.split(" ", 1)[0].strip().upper()
    return ""


def _build_notify_group_key(entry: dict) -> str:
    category = str(entry.get("category", "") or "").strip().lower()
    symbol = _extract_entry_symbol(entry)
    event_name = str(entry.get("event_name", "") or "").strip()
    title = str(entry.get("title", "") or "").strip()
    if category == "spread" or "点差" in title:
        return f"spread::{symbol or title}"
    if category == "structure":
        return f"structure::{symbol or title or 'setup'}"
    if category == "source":
        source_name = str(entry.get("source_name", "") or "").strip().lower()
        return f"source::{source_name or title or 'source'}"
    if category == "macro":
        return f"macro::{event_name or title or 'macro'}"
    if category == "recovery":
        return f"recovery::{symbol or title or 'recovery'}"
    if category == "session":
        return f"session::{symbol or title or 'session'}"
    if category == "mt5":
        return "mt5::terminal"
    return f"{category or 'general'}::{symbol or title or 'entry'}"


def _build_group_state_key(channel_key: str, group_key: str, field: str) -> str:
    return f"group::{channel_key}::{group_key}::{field}"


def _read_group_state(state: dict, channel_key: str, group_key: str) -> dict:
    return {
        "last_time": _parse_time(state.get(_build_group_state_key(channel_key, group_key, "last_time"), "")),
        "last_priority": int(state.get(_build_group_state_key(channel_key, group_key, "last_priority"), 0) or 0),
        "pending_count": int(state.get(_build_group_state_key(channel_key, group_key, "pending_count"), 0) or 0),
    }


def _increase_group_pending(state: dict, channel_key: str, group_key: str, amount: int = 1) -> int:
    key = _build_group_state_key(channel_key, group_key, "pending_count")
    next_value = int(state.get(key, 0) or 0) + max(1, int(amount or 1))
    state[key] = next_value
    return next_value


def _mark_group_sent(
    state: dict,
    channel_key: str,
    group_key: str,
    priority: int,
    sent_at: datetime | None = None,
) -> None:
    current = sent_at or datetime.now()
    state[_build_group_state_key(channel_key, group_key, "last_time")] = current.strftime("%Y-%m-%d %H:%M:%S")
    state[_build_group_state_key(channel_key, group_key, "last_priority")] = int(priority)
    state[_build_group_state_key(channel_key, group_key, "pending_count")] = 0


def _read_learning_digest_state(state: dict) -> dict:
    return {
        "last_time": _parse_time(state.get("learning_digest::last_time", "")),
        "last_hash": str(state.get("learning_digest::last_hash", "") or "").strip(),
    }


def _mark_learning_digest_sent(state: dict, digest_hash: str, sent_at: datetime | None = None) -> None:
    current = sent_at or datetime.now()
    state["learning_digest::last_time"] = current.strftime("%Y-%m-%d %H:%M:%S")
    state["learning_digest::last_hash"] = str(digest_hash or "").strip()


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


def _is_within_cooldown(
    entry: dict,
    state: dict,
    cooldown_min: int,
    now: datetime | None = None,
    channel_key: str | None = None,
) -> bool:
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
