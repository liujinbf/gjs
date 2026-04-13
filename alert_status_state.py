from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app_config import PROJECT_DIR

RUNTIME_DIR = PROJECT_DIR / ".runtime"
ALERT_STATUS_STATE_FILE = RUNTIME_DIR / "alert_status_state.json"
STATE_KEY = "_states"
TIMELINE_KEY = "_timeline"
MAX_TIMELINE_ITEMS = 200


def _read_state(state_file: Path | None = None) -> dict:
    target = Path(state_file) if state_file else ALERT_STATUS_STATE_FILE
    if not target.exists():
        return {STATE_KEY: {}, TIMELINE_KEY: []}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {STATE_KEY: {}, TIMELINE_KEY: []}
    return _normalize_payload(payload)


def _write_state(state: dict, state_file: Path | None = None) -> None:
    target = Path(state_file) if state_file else ALERT_STATUS_STATE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_normalize_payload(state), ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_payload(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {STATE_KEY: {}, TIMELINE_KEY: []}

    if STATE_KEY in payload or TIMELINE_KEY in payload:
        states = payload.get(STATE_KEY, {})
        timeline = payload.get(TIMELINE_KEY, [])
    else:
        states = payload
        timeline = []

    normalized_states = {}
    if isinstance(states, dict):
        for symbol, item in states.items():
            symbol_key = str(symbol or "").strip().upper()
            if not symbol_key or not isinstance(item, dict):
                continue
            normalized_states[symbol_key] = {
                "state_text": str(item.get("state_text", "") or "").strip(),
                "updated_at": str(item.get("updated_at", "") or "").strip(),
            }

    normalized_timeline = []
    if isinstance(timeline, list):
        for item in timeline:
            if not isinstance(item, dict):
                continue
            normalized_timeline.append(
                {
                    "symbol": str(item.get("symbol", "") or "").strip().upper(),
                    "from_state": str(item.get("from_state", "") or "").strip(),
                    "to_state": str(item.get("to_state", "") or "").strip(),
                    "changed_at": str(item.get("changed_at", "") or "").strip(),
                }
            )

    return {
        STATE_KEY: normalized_states,
        TIMELINE_KEY: normalized_timeline[-MAX_TIMELINE_ITEMS:],
    }


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def apply_alert_state_transitions(items: list[dict], state_file: Path | None = None, now: datetime | None = None) -> list[dict]:
    if state_file is None:
        return list(items or [])

    current = now or datetime.now()
    payload = _read_state(state_file=state_file)
    state = dict(payload.get(STATE_KEY, {}) or {})
    timeline = list(payload.get(TIMELINE_KEY, []) or [])
    result = []
    for item in list(items or []):
        payload = dict(item or {})
        symbol = str(payload.get("symbol", "") or "").strip().upper()
        current_state = str(payload.get("alert_state_text", "") or "").strip()
        previous = state.get(symbol, {}) if symbol else {}
        previous_state = str(previous.get("state_text", "") or "").strip()
        previous_time = str(previous.get("updated_at", "") or "").strip()

        payload["alert_state_previous_text"] = previous_state
        payload["alert_state_transition_text"] = ""
        payload["alert_state_changed"] = False
        if symbol and current_state and previous_state and current_state != previous_state:
            payload["alert_state_changed"] = True
            payload["alert_state_transition_text"] = f"{previous_state} -> {current_state}"
            if previous_time:
                payload["alert_state_transition_text"] += f"（上一状态时间：{previous_time}）"
            timeline.append(
                {
                    "symbol": symbol,
                    "from_state": previous_state,
                    "to_state": current_state,
                    "changed_at": current.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

        result.append(payload)
        if symbol and current_state:
            state[symbol] = {
                "state_text": current_state,
                "updated_at": current.strftime("%Y-%m-%d %H:%M:%S"),
            }

    _write_state(
        {
            STATE_KEY: state,
            TIMELINE_KEY: timeline[-MAX_TIMELINE_ITEMS:],
        },
        state_file=state_file,
    )
    return result


def read_recent_transitions(
    state_file: Path | None = None,
    now: datetime | None = None,
    window_min: int = 30,
    limit: int = 6,
) -> list[dict]:
    if state_file is None:
        return []

    payload = _read_state(state_file=state_file)
    current = now or datetime.now()
    result = []
    for item in list(payload.get(TIMELINE_KEY, []) or []):
        changed_at = _parse_time(str(item.get("changed_at", "") or "").strip())
        if changed_at is None:
            continue
        delta_seconds = (current - changed_at).total_seconds()
        if delta_seconds < 0 or delta_seconds > max(int(window_min or 0), 0) * 60:
            continue
        result.append(
            {
                "symbol": str(item.get("symbol", "") or "").strip().upper(),
                "from_state": str(item.get("from_state", "") or "").strip(),
                "to_state": str(item.get("to_state", "") or "").strip(),
                "changed_at": changed_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    result.sort(key=lambda item: str(item.get("changed_at", "") or "").strip(), reverse=True)
    return result[: max(int(limit or 0), 0)]
