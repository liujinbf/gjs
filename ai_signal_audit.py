from __future__ import annotations

from quote_models import SnapshotItem
from sim_signal_bridge import audit_rule_sim_signal_decision


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    return SnapshotItem.from_payload(item).to_dict()


def resolve_ai_signal_execution_audit(snapshot: dict, symbol: str = "") -> dict:
    """对齐 AI 方向信号与模拟试仓桥的执行审计结果。"""
    items = [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]
    target_symbol = _normalize_text(symbol).upper()
    target_item = {}
    if target_symbol:
        for item in items:
            if _normalize_text(item.get("symbol", "")).upper() == target_symbol:
                target_item = item
                break
    if not target_item and items:
        target_item = items[0]
        target_symbol = _normalize_text(target_item.get("symbol", "")).upper()

    result = {
        "audit_available": False,
        "symbol": target_symbol,
        "trade_grade": _normalize_text(target_item.get("trade_grade", "")),
        "trade_grade_source": _normalize_text(target_item.get("trade_grade_source", "")),
        "snapshot_signal_side": _normalize_text(target_item.get("signal_side", "")).lower(),
        "has_live_quote": bool(target_item.get("has_live_quote", False)),
        "sim_eligible": False,
        "sim_block_reason": "",
        "sim_block_reason_key": "",
        "sim_block_reason_label": "",
    }
    if not target_item:
        return result

    signal_side = _normalize_text(target_item.get("signal_side", "")).lower()
    has_rule_context = any(
        [
            _normalize_text(target_item.get("trade_grade", "")),
            _normalize_text(target_item.get("trade_grade_source", "")),
            signal_side in {"long", "short"},
            _normalize_text(target_item.get("alert_state_text", "")),
            _normalize_text(target_item.get("event_risk_mode_text", "")),
            bool(target_item.get("risk_reward_ready", False)),
            float(target_item.get("risk_reward_ratio", 0.0) or 0.0) > 0.0,
            float(target_item.get("risk_reward_stop_price", 0.0) or 0.0) > 0.0,
            float(target_item.get("risk_reward_target_price", 0.0) or 0.0) > 0.0,
        ]
    )
    if not has_rule_context:
        return result

    result["audit_available"] = True
    audit = audit_rule_sim_signal_decision({"items": items})
    for row in list(audit.get("rows", []) or []):
        row_symbol = _normalize_text(row.get("symbol", "")).upper()
        if row_symbol != target_symbol:
            continue
        result.update(
            {
                "sim_eligible": bool(row.get("eligible", False)),
                "sim_block_reason": _normalize_text(row.get("reason", "")),
                "sim_block_reason_key": _normalize_text(row.get("reason_key", "")),
                "sim_block_reason_label": _normalize_text(row.get("reason_label", "")),
            }
        )
        break
    return result
