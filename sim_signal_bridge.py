from __future__ import annotations

from app_config import get_runtime_config, get_sim_strategy_min_rr, normalize_sim_strategy_min_rr
from quote_models import SnapshotItem
from signal_enums import TradeGrade
from signal_protocol import validate_signal_meta
from trade_contracts import RiskDecision, StrategySignal
from rule_compiler import evaluate_rule_logic
import time
import json

_ACTIVE_RULES_CACHE = []
_ACTIVE_RULES_CACHE_TIME = 0

def _get_active_structured_rules() -> list[dict]:
    global _ACTIVE_RULES_CACHE, _ACTIVE_RULES_CACHE_TIME
    now = time.time()
    if now - _ACTIVE_RULES_CACHE_TIME < 60:
        return _ACTIVE_RULES_CACHE
    try:
        from knowledge_base import open_knowledge_connection
        with open_knowledge_connection() as conn:
            rows = conn.execute(
                """
                SELECT kr.id, kr.logic_json, kr.category
                FROM rule_governance rg
                JOIN knowledge_rules kr ON kr.id = rg.rule_id
                WHERE rg.horizon_min = 30
                  AND rg.governance_status = 'active'
                  AND kr.category IN ('entry', 'trend', 'directional')
                  AND kr.logic_json IS NOT NULL
                  AND kr.logic_json != '{}'
                ORDER BY kr.id ASC
                """
            ).fetchall()
            valid_rules = []
            for r in rows:
                try:
                    js = json.loads(r["logic_json"])
                    if js and isinstance(js, dict) and "op" in js:
                        valid_rules.append({"rule_id": int(r["id"]), "logic": js, "category": r["category"]})
                except Exception:
                    pass
            _ACTIVE_RULES_CACHE = valid_rules
            _ACTIVE_RULES_CACHE_TIME = now
            return _ACTIVE_RULES_CACHE
    except Exception:
        return _ACTIVE_RULES_CACHE

_SIM_BLOCK_REASON_LABELS = {
    "inactive_quote": "非实时报价",
    "grade_gate": "未到试仓级别",
    "source_gate": "非结构型信号",
    "rr_not_ready": "盈亏比未准备好",
    "rr_too_low": "盈亏比不足",
    "direction_unclear": "方向不清晰",
    "target_incomplete": "止损目标不完整",
    "entry_zone_miss": "未回到执行区",
    "chasing_upper": "上沿追价拦截",
    "chasing_lower": "下沿追空拦截",
    "meta_invalid": "信号元数据无效",
    "ready": "已满足试仓条件",
    "exploratory_ready": "探索试仓就绪",
}


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    """统一模拟试仓桥接链消费的快照项字段契约。"""
    return SnapshotItem.from_payload(item).to_dict()


def _pick_entry_price(item: dict, action: str) -> float:
    bid = float(item.get("bid", 0.0) or 0.0)
    ask = float(item.get("ask", 0.0) or 0.0)
    latest = float(item.get("latest_price", 0.0) or 0.0)
    if action == "long":
        return ask if ask > 0 else latest
    if action == "short":
        return bid if bid > 0 else latest
    return latest


def _resolve_signal_side(item: dict) -> str:
    explicit = _normalize_text(item.get("signal_side", "")).lower()
    if explicit in {"long", "short"}:
        return explicit

    for key in ("risk_reward_direction", "multi_timeframe_bias", "breakout_direction", "intraday_bias"):
        value = _normalize_text(item.get(key, "")).lower()
        if value == "bullish":
            return "long"
        if value == "bearish":
            return "short"

    price = float(item.get("latest_price", 0.0) or 0.0)
    stop = float(item.get("risk_reward_stop_price", 0.0) or 0.0)
    target = float(item.get("risk_reward_target_price", 0.0) or 0.0)
    if min(price, stop, target) > 0:
        if stop < price < target:
            return "long"
        if target < price < stop:
            return "short"
    return "neutral"


def _is_price_near_entry_zone(item: dict, action: str) -> bool:
    entry_zone_low = float(item.get("risk_reward_entry_zone_low", 0.0) or 0.0)
    entry_zone_high = float(item.get("risk_reward_entry_zone_high", 0.0) or 0.0)
    if entry_zone_low <= 0 or entry_zone_high <= 0:
        return True

    price = _pick_entry_price(item, action)
    low, high = sorted((entry_zone_low, entry_zone_high))
    span = max(high - low, 0.0)
    atr = max(
        float(item.get("atr14", 0.0) or 0.0),
        float(item.get("risk_reward_atr", 0.0) or 0.0),
    )
    point = max(float(item.get("point", 0.0) or 0.0), 0.0)
    padding = max(span * 0.35, atr * 0.15, point * 20)
    return (low - padding) <= price <= (high + padding)


def _resolve_entry_zone_position(item: dict, action: str) -> tuple[str, str]:
    entry_zone_low = float(item.get("risk_reward_entry_zone_low", 0.0) or 0.0)
    entry_zone_high = float(item.get("risk_reward_entry_zone_high", 0.0) or 0.0)
    if entry_zone_low <= 0 or entry_zone_high <= 0:
        return "", ""

    low, high = sorted((entry_zone_low, entry_zone_high))
    span = max(high - low, 0.0)
    price = _pick_entry_price(item, action)
    if span <= 0:
        return "middle", "中段"

    if low <= price <= high:
        progress = (price - low) / span
        if progress <= 0.33:
            return "lower", "下沿"
        if progress >= 0.67:
            return "upper", "上沿"
        return "middle", "中段"

    distance_to_low = abs(price - low)
    distance_to_high = abs(price - high)
    if distance_to_low <= distance_to_high:
        return "lower", "下沿"
    return "upper", "上沿"


def _classify_sim_block_reason(reason: str, eligible: bool) -> str:
    if eligible:
        return "ready"
    text = _normalize_text(reason)
    if not text:
        return "meta_invalid"
    if "当前不是实时报价" in text:
        return "inactive_quote"
    if "未触发任何高级智能规则" in text or "可轻仓试仓级别" in text:
        return "grade_gate"
    if "并非结构型入场信号" in text:
        return "source_gate"
    if "盈亏比尚未准备好" in text:
        return "rr_not_ready"
    if "盈亏比还不够健康" in text:
        return "rr_too_low"
    if "方向还不够清晰" in text:
        return "direction_unclear"
    if "止损或目标价仍不完整" in text:
        return "target_incomplete"
    if "继续等回踩" in text or "观察区间附近" in text:
        return "entry_zone_miss"
    if "上沿追价" in text or "上沿" in text:
        return "chasing_upper"
    if "下沿追空" in text or "下沿" in text:
        return "chasing_lower"
    return "meta_invalid"


def _build_contract_signal_payload(
    item: dict,
    action: str,
    *,
    execution_profile: str = "standard",
    risk_reason: str = "",
    reason_key: str = "ready",
) -> dict:
    source_kind = _normalize_text(item.get("trade_grade_source", "")).lower()
    setup_kind = _normalize_text(item.get("setup_kind", "")).lower()
    strategy_family = setup_kind or source_kind
    signal = StrategySignal.from_payload(
        {
            "symbol": _normalize_text(item.get("symbol", "")).upper(),
            "action": action,
            "price": _pick_entry_price(item, action),
            "sl": float(item.get("risk_reward_stop_price", 0.0) or 0.0),
            "tp": float(item.get("risk_reward_target_price", 0.0) or 0.0),
            "tp2": float(item.get("risk_reward_target_price_2", 0.0) or 0.0),
            "source_kind": source_kind,
            "trade_grade_source": source_kind,
            "setup_kind": setup_kind,
            "strategy_family": strategy_family,
            "execution_profile": execution_profile,
            "atr14": float(item.get("atr14", 0.0) or 0.0),
            "atr14_h4": float(item.get("atr14_h4", 0.0) or 0.0),
            "risk_reward_atr": float(item.get("risk_reward_atr", 0.0) or 0.0),
            "volume_step": float(item.get("volume_step", 0.0) or 0.0),
            "volume_min": float(item.get("volume_min", 0.0) or 0.0),
        }
    )
    payload = signal.to_signal_meta()
    zone_side, zone_side_text = _resolve_entry_zone_position(item, action)
    if zone_side:
        payload["entry_zone_side"] = zone_side
        payload["entry_zone_side_text"] = zone_side_text
    payload["risk_decision"] = RiskDecision(
        allowed=True,
        reason=_normalize_text(risk_reason) or "规则桥接已满足试仓条件",
        block_code=reason_key,
    ).to_dict()
    return payload


def _get_sim_thresholds() -> dict[str, float]:
    config = get_runtime_config()
    return {
        "min_rr": float(getattr(config, "sim_min_rr", 1.6) or 1.6),
        "relaxed_rr": float(getattr(config, "sim_relaxed_rr", 1.3) or 1.3),
        "model_min_probability": float(getattr(config, "sim_model_min_probability", 0.68) or 0.68),
        "exploratory_min_rr": 1.8,
        "setup_min_rr": normalize_sim_strategy_min_rr(getattr(config, "sim_strategy_min_rr", {})),
    }


def _resolve_setup_min_rr(item: dict, thresholds: dict[str, float]) -> float:
    setup_kind = _normalize_text(item.get("setup_kind", "")).lower()
    setup_rr_map = thresholds.get("setup_min_rr", {})
    if isinstance(setup_rr_map, dict) and setup_kind in setup_rr_map:
        return float(setup_rr_map.get(setup_kind, 0.0) or 0.0)
    if setup_kind:
        return get_sim_strategy_min_rr(setup_kind, default=float(thresholds.get("min_rr", 1.6) or 1.6))
    return float(thresholds.get("min_rr", 1.6) or 1.6)


def _is_exploratory_observation_candidate(item: dict, thresholds: dict[str, float] | None = None) -> bool:
    thresholds = dict(thresholds or _get_sim_thresholds())
    if _normalize_text(item.get("trade_grade", "")) != TradeGrade.OBSERVE_ONLY:
        return False
    if _normalize_text(item.get("trade_grade_source", "")) != "structure":
        return False
    if not bool(item.get("risk_reward_ready", False)):
        return False

    rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
    exploratory_min_rr = float(thresholds.get("exploratory_min_rr", 1.8) or 1.8)
    if rr < exploratory_min_rr:
        return False

    risk_reward_state = _normalize_text(item.get("risk_reward_state", "")).lower()
    if risk_reward_state and risk_reward_state not in {"acceptable", "favorable", "good"}:
        return False

    action = _resolve_signal_side(item)
    if action not in {"long", "short"}:
        return False

    risk_reward_direction = _normalize_text(item.get("risk_reward_direction", "")).lower()
    if risk_reward_direction in {"bullish", "long"} and action != "long":
        return False
    if risk_reward_direction in {"bearish", "short"} and action != "short":
        return False

    multi_alignment = _normalize_text(item.get("multi_timeframe_alignment", "")).lower()
    multi_bias = _normalize_text(item.get("multi_timeframe_bias", "")).lower()
    if multi_alignment and multi_alignment not in {"aligned", "partial"}:
        return False
    if multi_bias in {"bullish", "long"} and action != "long":
        return False
    if multi_bias in {"bearish", "short"} and action != "short":
        return False

    if min(
        float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        float(item.get("risk_reward_target_price", 0.0) or 0.0),
    ) <= 0:
        return False
    return True


def _is_exploratory_setup_candidate(item: dict, thresholds: dict[str, float] | None = None) -> bool:
    thresholds = dict(thresholds or _get_sim_thresholds())
    if _normalize_text(item.get("trade_grade", "")) != TradeGrade.LIGHT_POSITION:
        return False
    if _normalize_text(item.get("trade_grade_source", "")) != "setup":
        return False
    if not bool(item.get("risk_reward_ready", False)):
        return False

    action = _resolve_signal_side(item)
    if action not in {"long", "short"}:
        return False

    rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
    if rr < _resolve_setup_min_rr(item, thresholds):
        return False

    risk_reward_state = _normalize_text(item.get("risk_reward_state", "")).lower()
    if risk_reward_state and risk_reward_state not in {"acceptable", "favorable", "good"}:
        return False

    risk_reward_direction = _normalize_text(item.get("risk_reward_direction", "")).lower()
    if risk_reward_direction in {"bullish", "long"} and action != "long":
        return False
    if risk_reward_direction in {"bearish", "short"} and action != "short":
        return False

    if min(
        float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        float(item.get("risk_reward_target_price", 0.0) or 0.0),
    ) <= 0:
        return False
    return True


def _evaluate_item_for_sim(
    item: dict,
    thresholds: dict[str, float] | None = None,
    allow_exploratory: bool = False,
) -> tuple[bool, str, str, str]:
    thresholds = dict(thresholds or _get_sim_thresholds())
    if not bool(item.get("has_live_quote", False)):
        reason = "当前不是实时报价。"
        return False, reason, "neutral", _classify_sim_block_reason(reason, False)

    rule_overridden = False
    active_rules = _get_active_structured_rules()
    for rule in active_rules:
        if evaluate_rule_logic(rule["logic"], item):
            rule_overridden = True
            break

    observation_exploratory_override = bool(
        allow_exploratory and _is_exploratory_observation_candidate(item, thresholds)
    )
    setup_exploratory_override = bool(
        allow_exploratory and _is_exploratory_setup_candidate(item, thresholds)
    )
    exploratory_override = bool(observation_exploratory_override or setup_exploratory_override)
    if (
        not rule_overridden
        and not exploratory_override
        and _normalize_text(item.get("trade_grade", "")) != TradeGrade.LIGHT_POSITION
    ):
        reason = "当前还没到可轻仓试仓级别，且未触发任何高级智能规则。"
        return False, reason, "neutral", _classify_sim_block_reason(reason, False)
    if _normalize_text(item.get("trade_grade_source", "")) not in {"structure", "setup"}:
        reason = "当前候选并非结构型入场信号。"
        return False, reason, "neutral", _classify_sim_block_reason(reason, False)
    if not bool(item.get("risk_reward_ready", False)):
        reason = "盈亏比尚未准备好。"
        return False, reason, "neutral", _classify_sim_block_reason(reason, False)

    rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
    model_ready = bool(item.get("model_ready", False))
    model_probability = float(item.get("model_win_probability", 0.0) or 0.0)
    min_rr = float(thresholds.get("min_rr", 1.6) or 1.6)
    if _normalize_text(item.get("trade_grade_source", "")) == "setup":
        min_rr = _resolve_setup_min_rr(item, thresholds)
    relaxed_rr = float(thresholds.get("relaxed_rr", 1.3) or 1.3)
    model_min_probability = float(thresholds.get("model_min_probability", 0.68) or 0.68)
    if rr < min_rr:
        setup_ready = (
            exploratory_override
            and _normalize_text(item.get("trade_grade_source", "")) == "setup"
            and rr >= _resolve_setup_min_rr(item, thresholds)
        )
        if not setup_ready and not (rr >= relaxed_rr and model_ready and model_probability >= model_min_probability):
            reason = "盈亏比还不够健康，先继续观察。"
            return False, reason, "neutral", _classify_sim_block_reason(reason, False)

    action = _resolve_signal_side(item)
    if action not in {"long", "short"}:
        reason = "方向还不够清晰，暂不自动试仓。"
        return False, reason, "neutral", _classify_sim_block_reason(reason, False)

    if min(
        float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        float(item.get("risk_reward_target_price", 0.0) or 0.0),
    ) <= 0:
        reason = "止损或目标价仍不完整。"
        return False, reason, action, _classify_sim_block_reason(reason, False)
    if not _is_price_near_entry_zone(item, action):
        reason = "价格尚未回到可执行观察区间附近，继续等回踩。"
        return False, reason, action, _classify_sim_block_reason(reason, False)

    zone_side, zone_side_text = _resolve_entry_zone_position(item, action)
    if not exploratory_override and action == "long" and zone_side == "upper":
        reason = f"当前更贴近观察区间{zone_side_text}，自动试仓先别在上沿追价。"
        return False, reason, action, _classify_sim_block_reason(reason, False)
    if not exploratory_override and action == "short" and zone_side == "lower":
        reason = f"当前更贴近观察区间{zone_side_text}，自动试仓先别在下沿追空。"
        return False, reason, action, _classify_sim_block_reason(reason, False)

    meta = _build_contract_signal_payload(
        item,
        action,
        execution_profile="exploratory" if exploratory_override else "standard",
        reason_key="exploratory_ready" if exploratory_override else "ready",
    )
    valid, reason = validate_signal_meta(meta)
    if not valid:
        normalized_reason = _normalize_text(reason) or "信号元数据校验失败。"
        return False, normalized_reason, action, _classify_sim_block_reason(normalized_reason, False)
    if exploratory_override:
        return True, "", action, "exploratory_ready"
    return True, "", action, "ready"


def audit_rule_sim_signal_decision(snapshot: dict, allow_exploratory: bool = False) -> dict:
    thresholds = _get_sim_thresholds()
    blocked_counts: dict[str, int] = {}
    blocked_labels: dict[str, str] = {}
    candidate_rows = []
    ready_count = 0

    for item in [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]:
        symbol = _normalize_text(item.get("symbol", "")).upper()
        if not symbol:
            continue
        eligible, reason, action, reason_key = _evaluate_item_for_sim(
            item,
            thresholds=thresholds,
            allow_exploratory=allow_exploratory,
        )
        if eligible:
            ready_count += 1
        else:
            blocked_counts[reason_key] = int(blocked_counts.get(reason_key, 0) or 0) + 1
            blocked_labels[reason_key] = _SIM_BLOCK_REASON_LABELS.get(reason_key, reason_key)
        candidate_rows.append(
            {
                "symbol": symbol,
                "eligible": eligible,
                "action": action,
                "reason": _normalize_text(reason),
                "reason_key": reason_key,
                "reason_label": _SIM_BLOCK_REASON_LABELS.get(reason_key, reason_key),
            }
        )

    blocked_summary = sorted(
        (
            {"reason_key": key, "reason_label": _SIM_BLOCK_REASON_LABELS.get(key, key), "count": count}
            for key, count in blocked_counts.items()
        ),
        key=lambda row: (-int(row["count"]), str(row["reason_label"])),
    )
    return {
        "ready_count": ready_count,
        "blocked_counts": blocked_counts,
        "blocked_summary": blocked_summary,
        "rows": candidate_rows,
        "total_candidates": len(candidate_rows),
    }


def build_rule_sim_signal_decision(snapshot: dict, allow_exploratory: bool = False) -> tuple[dict | None, str]:
    thresholds = _get_sim_thresholds()
    actionable_candidates: list[tuple[float, dict]] = []
    blocked_reasons: list[str] = []

    for item in [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]:
        eligible, reason, action, reason_key = _evaluate_item_for_sim(
            item,
            thresholds=thresholds,
            allow_exploratory=allow_exploratory,
        )
        symbol = _normalize_text(item.get("symbol", "")).upper()
        if not symbol:
            continue
        if not eligible:
            if bool(item.get("has_live_quote", False)) and _normalize_text(item.get("trade_grade", "")):
                blocked_reasons.append(f"{symbol}：{reason}")
            continue

        score = float(item.get("risk_reward_ratio", 0.0) or 0.0)
        if bool(item.get("model_ready", False)):
            score += float(item.get("model_win_probability", 0.0) or 0.0)
        execution_profile = "exploratory" if reason_key == "exploratory_ready" else "standard"
        payload = _build_contract_signal_payload(
            item,
            action,
            execution_profile=execution_profile,
            reason_key=reason_key,
        )
        actionable_candidates.append((score, payload))

    if actionable_candidates:
        actionable_candidates.sort(key=lambda item_: item_[0], reverse=True)
        return actionable_candidates[0][1], ""
    return None, (blocked_reasons[0] if blocked_reasons else "")


def build_rule_sim_signal(snapshot: dict) -> dict | None:
    signal, _reason = build_rule_sim_signal_decision(snapshot)
    return signal
