"""
提醒留痕存储：负责构造、写入和读取本地提醒历史。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app_config import PROJECT_DIR

RUNTIME_DIR = PROJECT_DIR / ".runtime"
HISTORY_FILE = RUNTIME_DIR / "alert_history.jsonl"
MAX_HISTORY_LINES = 500


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _build_price_bucket(price: float, bucket_size: float = 10.0) -> str:
    """将价格映射到固定区间桶号，防止小幅噪音造成过度推送，同时允许行情出现明显移动后重新推送。"""
    if price <= 0:
        return "0"
    return str(int(price / max(0.0001, float(bucket_size))))


def _build_time_bucket() -> str:
    """返回当前「小时」标识，同一小时内相同内容不重复推送，换个小时后允许重推。"""
    return datetime.now().strftime("%Y%m%d%H")


def _build_entry(
    category: str,
    title: str,
    detail: str,
    tone: str,
    occurred_at: str,
    extra: dict | None = None,
    sig_extra: "list[str] | None" = None,
) -> dict:
    """
    构建标准提醒条目。

    sig_extra: 可选的额外签名分量列表。
      - 若提供，将替代默认的"时间桶"作为签名末尾的区分字段。
      - 适合宏观提醒等"内容驱动"的条目：只有当事件/状态真正改变时才触发新推送。
      - 若不提供，退回到原来的小时时间桶行为（保持对点差、结构等条目的兼容）。
    """
    clean_title = _normalize_text(title)
    clean_detail = _normalize_text(detail)
    clean_tone = str(tone or "neutral").strip() or "neutral"
    extra_dict = extra or {}

    # 价格桶：价格变动超过 10 个点位即视为新状态（金/银/汇率通用，按绝对值取整即可）
    time_bucket = _build_time_bucket()
    price = float(extra_dict.get("baseline_latest_price", 0.0) or 0.0)
    spread = float(extra_dict.get("baseline_spread_points", 0.0) or 0.0)
    price_bucket = _build_price_bucket(price) if price > 0 else ""
    spread_bucket = str(int(spread)) if spread > 0 else ""

    if sig_extra is not None:
        # 内容驱动签名：调用方显式指定区分字段，不再使用时间桶
        sig_parts = [clean_title, clean_tone]
        sig_parts.extend(str(item) for item in sig_extra if str(item or "").strip())
    else:
        # 时间驱动签名：默认行为，同一小时内不重推
        sig_parts = [clean_title, clean_tone, time_bucket]
        if price_bucket:
            sig_parts.append(price_bucket)
        if spread_bucket:
            sig_parts.append(f"spd{spread_bucket}")
    signature = "|".join(sig_parts)

    payload = {
        "occurred_at": str(occurred_at or "").strip(),
        "category": str(category or "general").strip() or "general",
        "title": clean_title,
        "detail": clean_detail,
        "tone": clean_tone,
        "signature": signature,
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _snapshot_trade_meta(snapshot: dict) -> dict:
    return {
        "trade_grade": str(snapshot.get("trade_grade", "") or "").strip(),
        "trade_grade_detail": _normalize_text(snapshot.get("trade_grade_detail", "")),
        "trade_next_review": _normalize_text(snapshot.get("trade_next_review", "")),
    }


def _snapshot_event_meta(snapshot: dict) -> dict:
    return {
        "event_mode_text": str(snapshot.get("event_risk_mode_text", "") or "").strip(),
        "event_name": str(snapshot.get("event_active_name", "") or "").strip(),
        "event_time_text": str(snapshot.get("event_active_time_text", "") or "").strip(),
        "event_importance_text": str(snapshot.get("event_active_importance_text", "") or "").strip(),
        "event_scope_text": str(snapshot.get("event_active_scope_text", "") or "").strip(),
        "event_note": "",
    }


def _item_event_meta(item: dict, fallback: dict | None = None) -> dict:
    base = dict(fallback or {})
    item_name = str(item.get("event_active_name", "") or "").strip()
    item_note = _normalize_text(item.get("event_note", ""))
    if item_name:
        base["event_name"] = item_name
    item_time_text = str(item.get("event_active_time_text", "") or "").strip()
    if item_time_text:
        base["event_time_text"] = item_time_text
    item_importance_text = str(item.get("event_importance_text", "") or "").strip()
    if item_importance_text:
        base["event_importance_text"] = item_importance_text
    item_scope_text = str(item.get("event_scope_text", "") or "").strip()
    if item_scope_text:
        base["event_scope_text"] = item_scope_text
    if item_note:
        base["event_note"] = item_note
    item_mode_text = str(item.get("event_mode_text", "") or "").strip()
    if item_mode_text:
        base["event_mode_text"] = item_mode_text
    return base


def _item_action_meta(item: dict, fallback_trade_meta: dict | None = None) -> dict:
    trade_meta = dict(fallback_trade_meta or {})
    result = {
        "baseline_latest_price": float(item.get("latest_price", 0.0) or 0.0),
        "baseline_spread_points": float(item.get("spread_points", 0.0) or 0.0),
        "price_point": float(item.get("point", 0.0) or 0.0),
        "trade_grade": str(item.get("trade_grade", "") or "").strip() or trade_meta.get("trade_grade", ""),
        "trade_grade_detail": _normalize_text(item.get("trade_grade_detail", "")) or trade_meta.get("trade_grade_detail", ""),
        "trade_next_review": _normalize_text(item.get("trade_next_review", "")) or trade_meta.get("trade_next_review", ""),
        "risk_reward_ready": bool(item.get("risk_reward_ready", False)),
        "risk_reward_ratio": float(item.get("risk_reward_ratio", 0.0) or 0.0),
        "stop_loss_price": float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        "take_profit_1": float(item.get("risk_reward_target_price", 0.0) or 0.0),
        "take_profit_2": float(item.get("risk_reward_target_price_2", 0.0) or 0.0),
        "model_ready": bool(item.get("model_ready", False)),
        "model_name": str(item.get("model_name", "") or "").strip(),
        "model_win_probability": float(item.get("model_win_probability", 0.0) or 0.0),
        "model_confidence_text": _normalize_text(item.get("model_confidence_text", "")),
        "model_note": _normalize_text(item.get("model_note", "")),
        "position_plan_text": _normalize_text(item.get("risk_reward_position_text", "")),
        "entry_invalidation_text": _normalize_text(item.get("risk_reward_invalidation_text", "")),
        "entry_zone_low": float(item.get("risk_reward_entry_zone_low", 0.0) or 0.0),
        "entry_zone_high": float(item.get("risk_reward_entry_zone_high", 0.0) or 0.0),
        "entry_zone_text": _normalize_text(item.get("risk_reward_entry_zone_text", "")),
        "external_bias_note": _normalize_text(item.get("external_bias_note", "")),
        "rsi14": item.get("rsi14"),
        "ma20": item.get("ma20"),
        "ma50": item.get("ma50"),
        "change_pct_24h": item.get("change_pct_24h"),
        "bollinger_upper": item.get("bollinger_upper"),
        "bollinger_mid": item.get("bollinger_mid"),
        "bollinger_lower": item.get("bollinger_lower"),
        "signal_side": str(item.get("signal_side", "") or "").strip(),
        "signal_side_text": str(item.get("signal_side_text", "") or "").strip(),
    }
    if result["take_profit_1"] <= 0:
        result.pop("take_profit_1", None)
    if result["take_profit_2"] <= 0:
        result.pop("take_profit_2", None)
    if result["entry_zone_low"] <= 0:
        result.pop("entry_zone_low", None)
    if result["entry_zone_high"] <= 0:
        result.pop("entry_zone_high", None)
    if result["stop_loss_price"] <= 0:
        result.pop("stop_loss_price", None)
    if result["risk_reward_ratio"] <= 0:
        result.pop("risk_reward_ratio", None)
    return result


def _pick_representative_item(items_by_symbol: dict[str, dict]) -> dict:
    candidates = [dict(item or {}) for item in items_by_symbol.values() if bool(item.get("has_live_quote", False))]
    if not candidates:
        return {}

    def _trade_rank(item: dict) -> int:
        grade = _normalize_text(item.get("trade_grade", ""))
        if grade == "可轻仓试仓":
            return 4
        if grade == "当前不宜出手":
            return 3
        if grade == "等待事件落地":
            return 2
        if grade == "只适合观察":
            return 1
        return 0

    candidates.sort(
        key=lambda item: (
            _trade_rank(item),
            float(item.get("risk_reward_ratio", 0.0) or 0.0),
            float(item.get("latest_price", 0.0) or 0.0),
        ),
        reverse=True,
    )
    return candidates[0]


def _find_latest_symbol_event(symbol: str, history_file: Path | None = None) -> dict | None:
    target_symbol = str(symbol or "").strip().upper()
    if not target_symbol:
        return None
    history = read_full_history(history_file=history_file)
    for entry in reversed(history):
        entry_symbol = str(entry.get("symbol", "") or "").strip().upper()
        if entry_symbol != target_symbol:
            continue
        category = str(entry.get("category", "") or "").strip().lower()
        if category in {"spread", "recovery"}:
            return entry
    return None


def _build_spread_recovery_entries(
    snapshot: dict,
    items_by_symbol: dict[str, dict],
    trade_meta: dict,
    snapshot_event_meta: dict,
    history_file: Path | None = None,
    lookback_hours: int = 12,
) -> list[dict]:
    occurred_at = str(snapshot.get("last_refresh_text", "") or "").strip()
    current_time = _parse_occurred_at(occurred_at)
    if current_time is None:
        return []

    result = []
    lookback = timedelta(hours=max(1, int(lookback_hours)))
    for symbol, item in items_by_symbol.items():
        if str(item.get("tone", "") or "").strip().lower() != "success":
            continue
        if not bool(item.get("has_live_quote", False)):
            continue

        latest_event = _find_latest_symbol_event(symbol, history_file=history_file)
        if not latest_event:
            continue
        if str(latest_event.get("category", "") or "").strip().lower() != "spread":
            continue

        latest_occurred_at = _parse_occurred_at(latest_event.get("occurred_at", ""))
        if latest_occurred_at is None or current_time - latest_occurred_at > lookback:
            continue

        current_spread_points = float(item.get("spread_points", 0.0) or 0.0)
        detail = (
            f"{symbol} 当前点差已回落到 {current_spread_points:.0f} 点，"
            f"相较上一轮异常提醒（{str(latest_event.get('title', '点差异常') or '点差异常').strip()}）已明显收敛。"
        )
        event_note = _normalize_text(item.get("event_note", ""))
        if event_note:
            detail += f" {event_note}"

        result.append(
            _build_entry(
                "recovery",
                f"{symbol} 点差已恢复",
                detail,
                "success",
                occurred_at,
                extra={
                    "symbol": symbol,
                    **_item_action_meta(item, fallback_trade_meta=trade_meta),
                    "baseline_spread_points": current_spread_points,
                    "recovered_from_title": str(latest_event.get("title", "") or "").strip(),
                    "recovered_from_time": str(latest_event.get("occurred_at", "") or "").strip(),
                    **_item_event_meta(item, fallback=snapshot_event_meta),
                },
            )
        )
    return result


def _build_structure_entries(
    snapshot: dict,
    items_by_symbol: dict[str, dict],
    trade_meta: dict,
    snapshot_event_meta: dict,
) -> list[dict]:
    occurred_at = str(snapshot.get("last_refresh_text", "") or "").strip()
    result = []
    for symbol, item in items_by_symbol.items():
        if not bool(item.get("has_live_quote", False)):
            continue
        if _normalize_text(item.get("trade_grade", "")) != "可轻仓试仓":
            continue
        if _normalize_text(item.get("trade_grade_source", "")) not in {"structure", "setup"}:
            continue
        if str(item.get("tone", "") or "").strip().lower() != "success":
            continue
        if not bool(item.get("risk_reward_ready", False)):
            continue
        risk_reward_state = str(item.get("risk_reward_state", "") or "").strip().lower()
        if risk_reward_state not in {"favorable", "acceptable"}:
            continue

        signal_side = str(item.get("signal_side_text", "") or "").strip() or "等待方向进一步确认"
        detail_parts = [
            f"{symbol} 当前结构和报价相对干净，可继续作为候选机会观察。",
            signal_side,
            _normalize_text(item.get("trade_grade_detail", "")),
            _normalize_text(item.get("risk_reward_context_text", "")),
            _normalize_text(item.get("risk_reward_entry_zone_text", "")),
            _normalize_text(item.get("risk_reward_position_text", "")),
            _normalize_text(item.get("risk_reward_invalidation_text", "")),
            _normalize_text(item.get("external_bias_note", "")),
        ]
        event_note = _normalize_text(item.get("event_note", ""))
        if event_note:
            detail_parts.append(event_note)
        detail = " ".join(part for part in detail_parts if part)
        result.append(
            _build_entry(
                "structure",
                f"{symbol} 结构候选",
                detail,
                "success",
                occurred_at,
                extra={
                    "symbol": symbol,
                    **_item_action_meta(item, fallback_trade_meta=trade_meta),
                    **_item_event_meta(item, fallback=snapshot_event_meta),
                },
                sig_extra=[
                    symbol,
                    _normalize_text(item.get("signal_side", "")),
                    _normalize_text(item.get("trade_grade_detail", "")),
                    _normalize_text(item.get("risk_reward_state", "")),
                    _normalize_text(item.get("risk_reward_entry_zone_text", "")),
                    _normalize_text(item.get("external_bias_note", "")),
                    _normalize_text(item.get("event_active_name", "")),
                ],
            )
        )
    return result


def _build_external_source_entries(snapshot: dict, trade_meta: dict, snapshot_event_meta: dict) -> list[dict]:
    occurred_at = str(snapshot.get("last_refresh_text", "") or "").strip()
    entries = []
    source_specs = [
        ("event_feed_status_text", "source", "事件源状态提醒", "event_feed"),
        ("macro_news_status_text", "source", "资讯流状态提醒", "macro_news"),
        ("macro_data_status_text", "source", "宏观数据状态提醒", "macro_data"),
    ]
    for key, category, title, source_key in source_specs:
        detail = _normalize_text(snapshot.get(key, ""))
        if not detail:
            continue
        tone = ""
        if "拉取失败" in detail:
            tone = "warning"
        elif any(keyword in detail for keyword in ("继续使用", "尚未配置", "未解析", "规格为空")):
            tone = "accent"
        if not tone:
            continue
        entries.append(
            _build_entry(
                category,
                title,
                detail,
                tone,
                occurred_at,
                extra={
                    "source_name": source_key,
                    **trade_meta,
                    **snapshot_event_meta,
                },
                sig_extra=[source_key, detail, tone],
            )
        )
    return entries


def _build_macro_entry(
    snapshot: dict,
    items_by_symbol: dict[str, dict],
    trade_meta: dict,
    snapshot_event_meta: dict,
) -> dict | None:
    alert_text = _normalize_text(snapshot.get("alert_text", ""))
    if not alert_text:
        return None

    representative_item = _pick_representative_item(items_by_symbol)
    event_name = _normalize_text(snapshot_event_meta.get("event_name", ""))
    event_importance_text = _normalize_text(snapshot_event_meta.get("event_importance_text", ""))
    event_result_summary_text = _normalize_text(snapshot.get("event_result_summary_text", ""))
    external_bias_notes = [
        _normalize_text(item.get("external_bias_note", ""))
        for item in items_by_symbol.values()
        if _normalize_text(item.get("external_bias_note", ""))
    ]
    has_actionable_event = bool(event_name or event_result_summary_text or external_bias_notes)
    if not has_actionable_event:
        return None

    representative_trade_meta = trade_meta
    representative_event_meta = snapshot_event_meta
    representative_extra = {}
    if representative_item:
        representative_trade_meta = {
            "trade_grade": str(representative_item.get("trade_grade", "") or "").strip() or trade_meta.get("trade_grade", ""),
            "trade_grade_detail": _normalize_text(representative_item.get("trade_grade_detail", "")) or trade_meta.get("trade_grade_detail", ""),
            "trade_next_review": _normalize_text(representative_item.get("trade_next_review", "")) or trade_meta.get("trade_next_review", ""),
        }
        representative_event_meta = _item_event_meta(representative_item, fallback=snapshot_event_meta)
        representative_extra = {
            "symbol": str(representative_item.get("symbol", "") or "").strip().upper(),
            **_item_action_meta(representative_item, fallback_trade_meta=representative_trade_meta),
            **representative_event_meta,
        }

    detail_parts = [alert_text]
    if event_result_summary_text:
        detail_parts.append(event_result_summary_text)
    if external_bias_notes:
        detail_parts.append(external_bias_notes[0])
    detail = " ".join(part for part in detail_parts if part)
    title = "宏观提醒"
    if event_name:
        title = f"{event_name} 宏观提醒"
    elif event_importance_text:
        title = f"{event_importance_text}宏观提醒"

    return _build_entry(
        "macro",
        title,
        detail,
        "warning" if "高影响" in event_importance_text or event_result_summary_text else "accent",
        str(snapshot.get("last_refresh_text", "") or "").strip(),
        extra={
            **representative_trade_meta,
            **representative_event_meta,
            **representative_extra,
            "macro_actionable": True,
            "event_result_summary_text": event_result_summary_text,
        },
        sig_extra=[
            event_name or "macro",
            event_result_summary_text,
            representative_trade_meta.get("trade_grade", ""),
            representative_trade_meta.get("trade_grade_detail", ""),
            representative_extra.get("symbol", ""),
        ],
    )


def build_snapshot_history_entries(snapshot: dict, history_file: Path | None = None) -> list[dict]:
    if not isinstance(snapshot, dict):
        return []

    occurred_at = str(snapshot.get("last_refresh_text", "") or "").strip()
    entries = []
    items_by_symbol = {
        str(item.get("symbol", "") or "").strip().upper(): item
        for item in list(snapshot.get("items", []) or [])
        if str(item.get("symbol", "") or "").strip()
    }

    runtime_cards = list(snapshot.get("runtime_status_cards", []) or [])
    trade_meta = _snapshot_trade_meta(snapshot)
    snapshot_event_meta = _snapshot_event_meta(snapshot)
    if runtime_cards:
        primary = runtime_cards[0]
        tone = str(primary.get("tone", "neutral") or "neutral")
        if tone in {"negative", "warning"}:
            entries.append(
                _build_entry(
                    "mt5",
                    primary.get("title", "MT5 状态提醒"),
                    primary.get("detail", ""),
                    tone,
                    occurred_at,
                    extra={**trade_meta, **snapshot_event_meta},
                )
            )

    if len(runtime_cards) > 1:
        secondary = runtime_cards[1]
        title = str(secondary.get("title", "") or "").strip()
        if title and title not in {"市场活跃度正常"}:
            entries.append(
                _build_entry(
                    "session",
                    title,
                    secondary.get("detail", ""),
                    secondary.get("tone", "neutral"),
                    occurred_at,
                    extra={**trade_meta, **snapshot_event_meta},
                )
            )

    for card in list(snapshot.get("spread_focus_cards", []) or []):
        title = str(card.get("title", "") or "").strip()
        if not title or title == "点差状态稳定":
            continue
        symbol = title.split(" ", 1)[0].strip().upper()
        item = items_by_symbol.get(symbol, {})
        entries.append(
            _build_entry(
                "spread",
                title,
                card.get("detail", ""),
                card.get("tone", "neutral"),
                occurred_at,
                extra={
                    "symbol": symbol,
                    **_item_action_meta(item, fallback_trade_meta=trade_meta),
                    **_item_event_meta(item, fallback=snapshot_event_meta),
                },
            )
        )

    entries.extend(_build_structure_entries(snapshot, items_by_symbol, trade_meta, snapshot_event_meta))

    macro_entry = _build_macro_entry(snapshot, items_by_symbol, trade_meta, snapshot_event_meta)
    if macro_entry:
        entries.append(macro_entry)
    entries.extend(_build_external_source_entries(snapshot, trade_meta, snapshot_event_meta))
    entries.extend(
        _build_spread_recovery_entries(
            snapshot,
            items_by_symbol,
            trade_meta,
            snapshot_event_meta,
            history_file=history_file,
        )
    )

    unique_entries = []
    seen = set()
    for entry in entries:
        signature = str(entry.get("signature", "") or "").strip()
        if not signature or signature in seen:
            continue
        seen.add(signature)
        unique_entries.append(entry)
    return unique_entries


def append_history_entries(entries: list[dict], history_file: Path | None = None) -> int:
    target = Path(history_file) if history_file else HISTORY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    recent_signatures = set()
    if target.exists():
        try:
            recent_lines = [line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()][-20:]
            for line in recent_lines:
                try:
                    recent_signatures.add(str(json.loads(line).get("signature", "") or "").strip())
                except json.JSONDecodeError:
                    continue
        except OSError:
            recent_signatures = set()

    append_lines = []
    for entry in entries or []:
        signature = str(entry.get("signature", "") or "").strip()
        if not signature or signature in recent_signatures:
            continue
        recent_signatures.add(signature)
        append_lines.append(json.dumps(entry, ensure_ascii=False))

    if not append_lines:
        return 0

    with target.open("a", encoding="utf-8") as handle:
        for line in append_lines:
            handle.write(line + "\n")

    try:
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) > MAX_HISTORY_LINES:
            trimmed = lines[-MAX_HISTORY_LINES:]
            target.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    except OSError:
        pass

    return len(append_lines)


def read_recent_history(limit: int = 8, history_file: Path | None = None) -> list[dict]:
    target = Path(history_file) if history_file else HISTORY_FILE
    if not target.exists():
        return []

    try:
        lines = [line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []

    result = []
    for line in lines[-max(1, int(limit)):]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            result.append(payload)
    return list(reversed(result))


def read_full_history(history_file: Path | None = None) -> list[dict]:
    target = Path(history_file) if history_file else HISTORY_FILE
    if not target.exists():
        return []

    try:
        lines = [line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []

    result = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            result.append(payload)
    return result


def build_latest_symbol_event_map(
    history_file: Path | None = None,
    categories: set[str] | None = None,
) -> dict[str, dict]:
    category_filter = {str(item or "").strip().lower() for item in set(categories or {"spread", "recovery"}) if str(item or "").strip()}
    result = {}
    for entry in reversed(read_full_history(history_file=history_file)):
        symbol = str(entry.get("symbol", "") or "").strip().upper()
        category = str(entry.get("category", "") or "").strip().lower()
        if not symbol or category not in category_filter or symbol in result:
            continue
        result[symbol] = entry
    return result


def _parse_occurred_at(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
