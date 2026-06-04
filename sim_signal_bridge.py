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

_STRATEGY_DRAWDOWN_CACHE = {}
_STRATEGY_LEARNING_GOV_CACHE = {}

def _get_strategy_drawdown_lock(family: str) -> dict:
    query_family = "au_ag_zscore" if family == "au_ag_pair" else family
    if query_family in _STRATEGY_DRAWDOWN_CACHE:
        return _STRATEGY_DRAWDOWN_CACHE[query_family]
    state = {"locked": False, "win_rate": 1.0, "win_count": 0, "loss_count": 0, "net_profit": 0.0}
    _STRATEGY_DRAWDOWN_CACHE[query_family] = state
    return state

def _get_strategy_validation_state(family: str, action: str, config=None) -> dict:
    if config is None:
        config = get_runtime_config()
    enabled = bool(getattr(config, "sim_strategy_validation_enabled", False))
    min_samples = int(getattr(config, "sim_strategy_validation_min_samples", 8) or 8)
    min_win_rate = float(getattr(config, "sim_strategy_validation_min_win_rate", 45.0) or 45.0)
    min_profit_factor = float(getattr(config, "sim_strategy_validation_min_profit_factor", 1.10) or 1.10)
    query_family = "au_ag_zscore" if family == "au_ag_pair" else family
    state = {
        "enabled": enabled,
        "strategy_family": query_family,
        "total_count": 0,
        "passed": True,
        "win_rate": 0.0,
        "net_profit": 0.0,
        "profit_factor": 0.0,
        "min_samples": min_samples,
    }
    if not enabled:
        return state
    try:
        from knowledge_base import open_knowledge_connection
        with open_knowledge_connection() as conn:
            rows = conn.execute(
                """
                SELECT outcome_label, profit
                FROM trade_learning_journal
                WHERE (setup_kind = ? OR (setup_kind IS NULL AND ? = 'structure'))
                  AND action = ?
                """,
                (query_family, query_family, action)
            ).fetchall()
            total_count = len(rows)
            state["total_count"] = total_count
            if total_count == 0:
                state["passed"] = False
                return state
            win_count = 0
            loss_count = 0
            total_profit = 0.0
            total_loss = 0.0
            net_profit = 0.0
            for row in rows:
                profit = float(row["profit"] or 0.0)
                net_profit += profit
                if profit > 0:
                    win_count += 1
                    total_profit += profit
                elif profit < 0:
                    loss_count += 1
                    total_loss += abs(profit)
            win_rate = (win_count / total_count) * 100.0 if total_count > 0 else 0.0
            if total_loss > 0:
                profit_factor = total_profit / total_loss
            else:
                profit_factor = 999.0 if total_profit > 0 else 1.0
            state["win_rate"] = win_rate
            state["net_profit"] = net_profit
            state["profit_factor"] = profit_factor
            passed = True
            if total_count < min_samples:
                passed = False
            elif win_rate < min_win_rate:
                passed = False
            elif profit_factor < min_profit_factor:
                passed = False
            state["passed"] = passed
    except Exception:
        pass
    return state

def _get_strategy_learning_governance_state(family: str) -> dict:
    query_family = "au_ag_zscore" if family == "au_ag_pair" else family
    if query_family in _STRATEGY_LEARNING_GOV_CACHE:
        return _STRATEGY_LEARNING_GOV_CACHE[query_family]
    state = {"blocked": False}
    try:
        from knowledge_base import open_knowledge_connection
        with open_knowledge_connection() as conn:
            rows = conn.execute(
                """
                SELECT kr.logic_json, rg.governance_status
                FROM rule_governance rg
                JOIN knowledge_rules kr ON kr.id = rg.rule_id
                WHERE rg.governance_status = 'archived'
                """
            ).fetchall()
            for row in rows:
                logic_json_str = row["logic_json"]
                if not logic_json_str:
                    continue
                try:
                    logic = json.loads(logic_json_str)
                    if (
                        logic
                        and isinstance(logic, dict)
                        and logic.get("source") == "strategy_learning"
                        and logic.get("strategy_family") == query_family
                    ):
                        state["blocked"] = True
                        break
                except Exception:
                    pass
    except Exception:
        pass
    _STRATEGY_LEARNING_GOV_CACHE[query_family] = state
    return state

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

_GRADE_GATE_SECONDARY_LABELS = {
    "event_gate": "事件窗口",
    "source_gate": "非结构型信号",
    "grade_not_observe": "结构等级偏低",
    "rr_not_ready": "盈亏比未准备好",
    "rr_too_low": "RR不足",
    "risk_reward_state_bad": "盈亏比状态不佳",
    "multi_timeframe_misaligned": "多周期未同向",
    "direction_unclear": "方向不清晰",
    "target_incomplete": "止损目标不完整",
    "entry_zone_miss": "未回到执行区",
    "chasing_upper": "上沿追价",
    "chasing_lower": "下沿追空",
    "unknown": "待继续细分",
}

_RR_NOT_READY_TERTIARY_LABELS = {
    "no_price": "现价缺失",
    "no_direction": "方向基础不足",
    "atr_missing_no_key_levels": "ATR缺失且关键位不足",
    "key_range_invalid": "关键位区间无效",
    "price_span_too_small": "止损目标跨度过小",
    "entry_zone_missing": "入场区间未生成",
    "unknown": "待继续细分",
}

_NO_DIRECTION_COMPONENT_LABELS = {
    "signal_side_missing": "信号方向缺失",
    "intraday_sideways": "日内方向震荡",
    "multi_not_aligned": "多周期未同向",
    "breakout_direction_neutral": "突破方向中性",
    "breakout_state_none": "突破未确认",
    "retest_state_none": "回踩未确认",
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
    scalp_dir = _normalize_text(item.get("scalp_direction", "")).lower()
    if scalp_dir in {"long", "short"}:
        return scalp_dir

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


def _resolve_rr_direction_hint(item: dict) -> str:
    for key in ("signal_side", "risk_reward_direction", "multi_timeframe_bias", "breakout_direction", "intraday_bias"):
        value = _normalize_text(item.get(key, "")).lower()
        if value in {"long", "bullish"}:
            return "bullish"
        if value in {"short", "bearish"}:
            return "bearish"
    return "unknown"


def _diagnose_rr_not_ready_tertiary(item: dict) -> tuple[str, str]:
    current_price = float(item.get("latest_price", 0.0) or 0.0)
    key_high = float(item.get("key_level_high", 0.0) or 0.0)
    key_low = float(item.get("key_level_low", 0.0) or 0.0)
    atr14 = max(float(item.get("atr14", 0.0) or 0.0), 0.0)
    direction = _resolve_rr_direction_hint(item)

    if current_price <= 0:
        return "no_price", _RR_NOT_READY_TERTIARY_LABELS["no_price"]
    if direction not in {"bullish", "bearish"}:
        return "no_direction", _RR_NOT_READY_TERTIARY_LABELS["no_direction"]
    if min(key_high, key_low) <= 0 or key_high <= key_low:
        if atr14 <= 0:
            return "atr_missing_no_key_levels", _RR_NOT_READY_TERTIARY_LABELS["atr_missing_no_key_levels"]
        return "unknown", _RR_NOT_READY_TERTIARY_LABELS["unknown"]

    stop_price = float(item.get("risk_reward_stop_price", 0.0) or 0.0)
    target_price = float(item.get("risk_reward_target_price", 0.0) or 0.0)
    if min(stop_price, target_price) > 0:
        risk = abs(current_price - stop_price)
        reward = abs(target_price - current_price)
        if risk < 1e-5 or reward < 1e-5:
            return "price_span_too_small", _RR_NOT_READY_TERTIARY_LABELS["price_span_too_small"]

    zone_low = float(item.get("risk_reward_entry_zone_low", 0.0) or 0.0)
    zone_high = float(item.get("risk_reward_entry_zone_high", 0.0) or 0.0)
    if zone_low <= 0 or zone_high <= 0:
        return "entry_zone_missing", _RR_NOT_READY_TERTIARY_LABELS["entry_zone_missing"]
    return "unknown", _RR_NOT_READY_TERTIARY_LABELS["unknown"]


def _diagnose_no_direction_components(item: dict) -> list[dict]:
    components: list[dict] = []
    signal_side = _normalize_text(item.get("signal_side", "")).lower()
    intraday_bias = _normalize_text(item.get("intraday_bias", "")).lower()
    multi_alignment = _normalize_text(item.get("multi_timeframe_alignment", "")).lower()
    multi_bias = _normalize_text(item.get("multi_timeframe_bias", "")).lower()
    breakout_direction = _normalize_text(item.get("breakout_direction", "")).lower()
    breakout_state = _normalize_text(item.get("breakout_state", "")).lower()
    retest_state = _normalize_text(item.get("retest_state", "")).lower()

    if signal_side not in {"long", "short"}:
        components.append({"reason_key": "signal_side_missing", "reason_label": _NO_DIRECTION_COMPONENT_LABELS["signal_side_missing"]})
    if intraday_bias not in {"bullish", "bearish"}:
        components.append({"reason_key": "intraday_sideways", "reason_label": _NO_DIRECTION_COMPONENT_LABELS["intraday_sideways"]})
    if multi_alignment not in {"aligned", "partial"} or multi_bias not in {"bullish", "bearish"}:
        components.append({"reason_key": "multi_not_aligned", "reason_label": _NO_DIRECTION_COMPONENT_LABELS["multi_not_aligned"]})
    if breakout_direction not in {"bullish", "bearish"}:
        components.append({"reason_key": "breakout_direction_neutral", "reason_label": _NO_DIRECTION_COMPONENT_LABELS["breakout_direction_neutral"]})
    if breakout_state in {"", "none", "unknown"}:
        components.append({"reason_key": "breakout_state_none", "reason_label": _NO_DIRECTION_COMPONENT_LABELS["breakout_state_none"]})
    if retest_state in {"", "none", "unknown"}:
        components.append({"reason_key": "retest_state_none", "reason_label": _NO_DIRECTION_COMPONENT_LABELS["retest_state_none"]})
    return components


def _diagnose_grade_gate_secondary(item: dict, thresholds: dict[str, float] | None = None) -> tuple[str, str]:
    thresholds = dict(thresholds or _get_sim_thresholds())
    trade_grade = _normalize_text(item.get("trade_grade", ""))
    trade_grade_source = _normalize_text(item.get("trade_grade_source", "")).lower()
    event_note = _normalize_text(item.get("event_note", ""))
    event_mode = _normalize_text(item.get("event_mode_text", "") or item.get("event_risk_mode_text", ""))
    if "事件" in trade_grade or bool(event_note) or event_mode in {"事件前高敏", "事件前", "事件落地观察", "事件后"}:
        return "event_gate", _GRADE_GATE_SECONDARY_LABELS["event_gate"]
    if trade_grade_source not in {"structure", "setup"}:
        return "source_gate", _GRADE_GATE_SECONDARY_LABELS["source_gate"]
    if trade_grade != TradeGrade.OBSERVE_ONLY:
        return "grade_not_observe", _GRADE_GATE_SECONDARY_LABELS["grade_not_observe"]
    if not bool(item.get("risk_reward_ready", False)):
        return "rr_not_ready", _GRADE_GATE_SECONDARY_LABELS["rr_not_ready"]

    rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
    exploratory_min_rr = float(thresholds.get("exploratory_min_rr", 1.8) or 1.8)
    if rr < exploratory_min_rr:
        return "rr_too_low", _GRADE_GATE_SECONDARY_LABELS["rr_too_low"]

    risk_reward_state = _normalize_text(item.get("risk_reward_state", "")).lower()
    if risk_reward_state and risk_reward_state not in {"acceptable", "favorable", "good"}:
        return "risk_reward_state_bad", _GRADE_GATE_SECONDARY_LABELS["risk_reward_state_bad"]

    action = _resolve_signal_side(item)
    if action not in {"long", "short"}:
        return "direction_unclear", _GRADE_GATE_SECONDARY_LABELS["direction_unclear"]

    multi_alignment = _normalize_text(item.get("multi_timeframe_alignment", "")).lower()
    multi_bias = _normalize_text(item.get("multi_timeframe_bias", "")).lower()
    if multi_alignment and multi_alignment not in {"aligned", "partial"}:
        return "multi_timeframe_misaligned", _GRADE_GATE_SECONDARY_LABELS["multi_timeframe_misaligned"]
    if multi_bias in {"bullish", "long"} and action != "long":
        return "multi_timeframe_misaligned", _GRADE_GATE_SECONDARY_LABELS["multi_timeframe_misaligned"]
    if multi_bias in {"bearish", "short"} and action != "short":
        return "multi_timeframe_misaligned", _GRADE_GATE_SECONDARY_LABELS["multi_timeframe_misaligned"]

    if min(
        float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        float(item.get("risk_reward_target_price", 0.0) or 0.0),
    ) <= 0:
        return "target_incomplete", _GRADE_GATE_SECONDARY_LABELS["target_incomplete"]

    if not _is_price_near_entry_zone(item, action):
        return "entry_zone_miss", _GRADE_GATE_SECONDARY_LABELS["entry_zone_miss"]

    zone_side, _zone_side_text = _resolve_entry_zone_position(item, action)
    if action == "long" and zone_side == "upper":
        return "chasing_upper", _GRADE_GATE_SECONDARY_LABELS["chasing_upper"]
    if action == "short" and zone_side == "lower":
        return "chasing_lower", _GRADE_GATE_SECONDARY_LABELS["chasing_lower"]
    return "unknown", _GRADE_GATE_SECONDARY_LABELS["unknown"]


def _build_sim_block_diagnostics(item: dict, reason_key: str, thresholds: dict[str, float] | None = None) -> dict:
    diagnostics: dict = {}
    clean_reason_key = _normalize_text(reason_key).lower()
    if clean_reason_key == "grade_gate":
        secondary_key, secondary_label = _diagnose_grade_gate_secondary(item, thresholds)
        diagnostics["secondary_reason_key"] = secondary_key
        diagnostics["secondary_reason_label"] = secondary_label
        if secondary_key == "rr_not_ready":
            tertiary_key, tertiary_label = _diagnose_rr_not_ready_tertiary(item)
            diagnostics["tertiary_reason_key"] = tertiary_key
            diagnostics["tertiary_reason_label"] = tertiary_label
            if tertiary_key == "no_direction":
                diagnostics["direction_components"] = _diagnose_no_direction_components(item)
        elif secondary_key == "direction_unclear":
            diagnostics["direction_components"] = _diagnose_no_direction_components(item)
    elif clean_reason_key == "rr_not_ready":
        tertiary_key, tertiary_label = _diagnose_rr_not_ready_tertiary(item)
        diagnostics["tertiary_reason_key"] = tertiary_key
        diagnostics["tertiary_reason_label"] = tertiary_label
    elif clean_reason_key == "direction_unclear":
        diagnostics["direction_components"] = _diagnose_no_direction_components(item)
    return diagnostics


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
    
    is_scalp_strategy = (setup_kind == "scalp")
    sl_val = float(item.get("scalp_stop_price", 0.0) or 0.0) if is_scalp_strategy else float(item.get("risk_reward_stop_price", 0.0) or 0.0)
    tp_val = float(item.get("scalp_target_price", 0.0) or 0.0) if is_scalp_strategy else float(item.get("risk_reward_target_price", 0.0) or 0.0)
    tp2_val = float(item.get("scalp_target_2_price", 0.0) or 0.0) if is_scalp_strategy else float(item.get("risk_reward_target_price_2", 0.0) or 0.0)

    signal = StrategySignal.from_payload(
        {
            "symbol": _normalize_text(item.get("symbol", "")).upper(),
            "action": action,
            "price": _pick_entry_price(item, action),
            "sl": sl_val,
            "tp": tp_val,
            "tp2": tp2_val,
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

    setup_kind = _normalize_text(item.get("setup_kind", "")).lower()
    source_kind = _normalize_text(item.get("trade_grade_source", "")).lower()
    strategy_family = setup_kind or source_kind
    
    # 提前获取并解析方向
    action = _resolve_signal_side(item)
    config = get_runtime_config()

    # 1. 策略禁用名单风控拦截
    disabled_strategies = getattr(config, "sim_disabled_strategies", []) or []
    if strategy_family in disabled_strategies:
        reason = f"策略 {strategy_family} 已在策略回滚禁用名单中。"
        return False, reason, "neutral", "grade_gate"

    # 2. 策略方向禁用名单风控拦截
    if action in {"long", "short"}:
        disabled_actions = getattr(config, "sim_disabled_strategy_actions", []) or []
        action_key = f"{strategy_family}:{action}"
        if action_key in disabled_actions:
            reason = f"策略动作 {action_key} 已在策略方向回滚禁用名单中。"
            return False, reason, "neutral", "grade_gate"

    # 3. 防回撤冬眠风控拦截
    lock_state = _get_strategy_drawdown_lock(strategy_family)
    if lock_state and lock_state.get("locked"):
        reason = f"防回撤冬眠：策略 {strategy_family} 最近表现不佳已锁定。"
        return False, reason, "neutral", "grade_gate"

    # 4. 策略学习治理期冻结拦截
    gov_state = _get_strategy_learning_governance_state(strategy_family)
    if gov_state and gov_state.get("blocked"):
        reason = "策略学习治理：该策略自动开仓已冻结。"
        return False, reason, "neutral", "grade_gate"

    # 5. 样本验证自适应风控拦截
    if action in {"long", "short"}:
        validation_enabled = getattr(config, "sim_strategy_validation_enabled", False)
        if validation_enabled:
            val_state = _get_strategy_validation_state(strategy_family, action, config)
            if val_state and not val_state.get("passed", True):
                reason = "策略样本验证未通过：当前样本不足或表现不佳。"
                return False, reason, "neutral", "grade_gate"

    # 6. 资讯流宏观政策冲突拦截
    if action in {"long", "short"}:
        macro_news = item.get("macro_news_items", []) or []
        symbol = _normalize_text(item.get("symbol", "")).upper()
        for news in macro_news:
            bias_by_symbol = news.get("bias_by_symbol", {}) or {}
            news_bias = _normalize_text(bias_by_symbol.get(symbol, "")).lower()
            bias_dir = "long" if news_bias in {"bullish", "long"} else ("short" if news_bias in {"bearish", "short"} else "")
            if bias_dir and bias_dir != action:
                reason = f"资讯流宏观政策冲突：信号方向为 {action}，但宏观政策偏向为 {news_bias}。"
                return False, reason, "neutral", "grade_gate"

    # 7. 结构化宏观数据冲突拦截
    if action in {"long", "short"}:
        macro_data = item.get("macro_data_items", []) or []
        for data in macro_data:
            data_dir_raw = _normalize_text(data.get("direction", "")).lower()
            data_dir = "long" if data_dir_raw in {"bullish", "long"} else ("short" if data_dir_raw in {"bearish", "short"} else "")
            if data_dir and data_dir != action:
                reason = f"结构化宏观数据冲突：信号方向为 {action}，但宏观数据偏向为 {data_dir_raw}。"
                return False, reason, "neutral", "grade_gate"

    # 8. 事件窗口物理硬拦截风控
    trade_grade_source_raw = str(item.get("trade_grade_source", "") or "").strip().lower()
    event_override_kind = str(item.get("event_override_kind", "") or "").strip().lower()
    evt_mode = str(item.get("event_risk_mode", "") or "").strip().lower()
    evt_text = str(item.get("event_mode_text", "") or item.get("event_risk_mode_text", "") or "").strip()
    evt_note = str(item.get("event_note", "") or "").strip()

    is_event_phase = (
        evt_mode in {"pre_event", "post_event", "illiquid"}
        or "pre_event" in evt_mode
        or "post_event" in evt_mode
        or "illiquid" in evt_mode
        or "事件前" in evt_text
        or "事件后" in evt_text
        or "流动性" in evt_text
        or bool(evt_note)
    )
    is_event_source = (trade_grade_source_raw == "event")
    is_continuation = (event_override_kind == "post_event_continuation")

    if (is_event_phase or is_event_source) and not is_continuation:
        is_scalp = (strategy_family == "scalp")
        evt_importance = str(item.get("event_importance", "") or item.get("event_importance_text", "") or "").strip().lower()
        evt_note_text = str(item.get("event_note", "") or "").strip().lower()
        is_high_impact = (
            evt_importance == "high" 
            or "high" in evt_importance 
            or "重大" in evt_importance 
            or "高影响" in evt_importance
            or "高敏" in evt_importance
            or "重大" in evt_note_text
            or "高影响" in evt_note_text
        )
        if is_scalp:
            if is_high_impact:
                reason = "当前正处于重大高影响事件窗口内，短线策略执行物理拦截禁止开仓。"
                return False, reason, "neutral", "grade_gate"
        else:
            reason = f"当前正处于事件高敏发布或流动性窗口内，执行面物理拦截禁止开仓。（细分：{evt_text or evt_note}）"
            return False, reason, "neutral", "grade_gate"

    # 9. Tick剧震冷却拦截
    if bool(item.get("tick_shock_active", False)):
        reason = "Tick 异动冷却中：当前标的处于急拉/急跌后的剧震冷却期。"
        return False, reason, action, "grade_gate"

    # 10. M15 裸K价格结构反向拦截
    if action in {"long", "short"} and bool(item.get("m15_price_structure_ready", False)):
        struct_dir = _normalize_text(item.get("m15_price_structure_direction", "")).lower()
        if struct_dir == "bullish" and action == "short":
            reason = "裸K结构偏多：M15结构偏多，禁止做空。"
            return False, reason, "neutral", "grade_gate"
        elif struct_dir == "bearish" and action == "long":
            reason = "裸K结构偏空：M15结构偏空，禁止做多。"
            return False, reason, "neutral", "grade_gate"

    # ── 下面接原本的轻仓试仓级别、高级智能规则、盈亏比等后续判定 ──
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
    
    is_scalp_strategy = (strategy_family == "scalp")

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
    
    # ── 盈亏比就绪自适应校验 ──
    rr_ready = bool(item.get("scalp_ready", False)) if is_scalp_strategy else bool(item.get("risk_reward_ready", False))
    if not rr_ready:
        reason = "盈亏比尚未准备好。"
        return False, reason, "neutral", _classify_sim_block_reason(reason, False)

    # ── 盈亏比数值自适应校验 ──
    from runtime_utils import get_adaptive_filter_ratio
    adaptive_ratio = get_adaptive_filter_ratio()

    rr = float(item.get("scalp_rr", 0.0) or 0.0) if is_scalp_strategy else float(item.get("risk_reward_ratio", 0.0) or 0.0)
    model_ready = bool(item.get("model_ready", False))
    model_probability = float(item.get("model_win_probability", 0.0) or 0.0)
    
    if is_scalp_strategy:
        min_rr = float(getattr(config, "scalp_min_rr", 1.2) or 1.2) * adaptive_ratio
    else:
        min_rr = (float(thresholds.get("min_rr", 1.6) or 1.6)) * adaptive_ratio
        if _normalize_text(item.get("trade_grade_source", "")) == "setup":
            min_rr = (_resolve_setup_min_rr(item, thresholds)) * adaptive_ratio
            
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

    # ── 方向清晰度最终阻断 ──
    if action not in {"long", "short"}:
        reason = "方向还不够清晰，暂不自动试仓。"
        return False, reason, "neutral", _classify_sim_block_reason(reason, False)

    # ── 止损目标绝对价格校验 ──
    sl_check = float(item.get("scalp_stop_price", 0.0) or 0.0) if is_scalp_strategy else float(item.get("risk_reward_stop_price", 0.0) or 0.0)
    tp_check = float(item.get("scalp_target_price", 0.0) or 0.0) if is_scalp_strategy else float(item.get("risk_reward_target_price", 0.0) or 0.0)
    if min(sl_check, tp_check) <= 0:
        reason = "止损或目标价仍不完整。"
        return False, reason, action, _classify_sim_block_reason(reason, False)
        
    # ── 高频短线不执行慢速大周期 entry zone 与追涨杀跌校验 ──
    if not is_scalp_strategy:
        if not _is_price_near_entry_zone(item, action):
            reason = "价格尚未回到可执行观察区间附近，继续等回踩。"
            return False, reason, action, _classify_sim_block_reason(reason, False)

        zone_side, zone_side_text = _resolve_entry_zone_position(item, action)
        if action == "long" and zone_side == "upper":
            reason = f"当前更贴近观察区间{zone_side_text}，自动试仓先别在上沿追价。"
            return False, reason, action, _classify_sim_block_reason(reason, False)
        if action == "short" and zone_side == "lower":
            reason = f"当前更贴近观察区间{zone_side_text}，自动试仓先别在下沿追空。"
            return False, reason, action, _classify_sim_block_reason(reason, False)

    meta = _build_contract_signal_payload(
        item,
        action,
        execution_profile="exploratory" if exploratory_override else "standard",
        reason_key="exploratory_ready" if exploratory_override else "ready",
    )
    valid, reason_str = validate_signal_meta(meta)
    if not valid:
        normalized_reason = _normalize_text(reason_str) or "信号元数据校验失败。"
        return False, normalized_reason, action, _classify_sim_block_reason(normalized_reason, False)
    if exploratory_override:
        return True, "", action, "exploratory_ready"
    return True, "", action, "ready"

def audit_rule_sim_signal_decision(snapshot: dict, allow_exploratory: bool = False) -> dict:
    thresholds = _get_sim_thresholds()
    blocked_counts: dict[str, int] = {}
    blocked_labels: dict[str, str] = {}
    secondary_blocked_counts: dict[str, int] = {}
    secondary_blocked_labels: dict[str, str] = {}
    tertiary_blocked_counts: dict[str, int] = {}
    tertiary_blocked_labels: dict[str, str] = {}
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
        diagnostics = {}
        if eligible:
            ready_count += 1
        else:
            diagnostics = _build_sim_block_diagnostics(item, reason_key, thresholds)
            blocked_counts[reason_key] = int(blocked_counts.get(reason_key, 0) or 0) + 1
            blocked_labels[reason_key] = _SIM_BLOCK_REASON_LABELS.get(reason_key, reason_key)
            secondary_key = _normalize_text(diagnostics.get("secondary_reason_key", ""))
            if secondary_key:
                secondary_blocked_counts[secondary_key] = int(secondary_blocked_counts.get(secondary_key, 0) or 0) + 1
                secondary_blocked_labels[secondary_key] = _normalize_text(
                    diagnostics.get("secondary_reason_label", "")
                ) or secondary_key
            tertiary_key = _normalize_text(diagnostics.get("tertiary_reason_key", ""))
            if tertiary_key:
                tertiary_blocked_counts[tertiary_key] = int(tertiary_blocked_counts.get(tertiary_key, 0) or 0) + 1
                tertiary_blocked_labels[tertiary_key] = _normalize_text(
                    diagnostics.get("tertiary_reason_label", "")
                ) or tertiary_key
        candidate_rows.append(
            {
                "symbol": symbol,
                "eligible": eligible,
                "action": action,
                "reason": _normalize_text(reason),
                "reason_key": reason_key,
                "reason_label": _SIM_BLOCK_REASON_LABELS.get(reason_key, reason_key),
                **diagnostics,
            }
        )

    blocked_summary = sorted(
        (
            {"reason_key": key, "reason_label": _SIM_BLOCK_REASON_LABELS.get(key, key), "count": count}
            for key, count in blocked_counts.items()
        ),
        key=lambda row: (-int(row["count"]), str(row["reason_label"])),
    )
    secondary_blocked_summary = sorted(
        (
            {"reason_key": key, "reason_label": secondary_blocked_labels.get(key, key), "count": count}
            for key, count in secondary_blocked_counts.items()
        ),
        key=lambda row: (-int(row["count"]), str(row["reason_label"])),
    )
    tertiary_blocked_summary = sorted(
        (
            {"reason_key": key, "reason_label": tertiary_blocked_labels.get(key, key), "count": count}
            for key, count in tertiary_blocked_counts.items()
        ),
        key=lambda row: (-int(row["count"]), str(row["reason_label"])),
    )
    return {
        "ready_count": ready_count,
        "blocked_counts": blocked_counts,
        "blocked_summary": blocked_summary,
        "secondary_blocked_counts": secondary_blocked_counts,
        "secondary_blocked_summary": secondary_blocked_summary,
        "tertiary_blocked_counts": tertiary_blocked_counts,
        "tertiary_blocked_summary": tertiary_blocked_summary,
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
                diagnostics = _build_sim_block_diagnostics(item, reason_key, thresholds)
                secondary_label = _normalize_text(diagnostics.get("secondary_reason_label", ""))
                suffix = f"（细分：{secondary_label}）" if reason_key == "grade_gate" and secondary_label else ""
                blocked_reasons.append(f"{symbol}：{reason}{suffix}")
            continue

        setup_kind = _normalize_text(item.get("setup_kind", "")).lower()
        if setup_kind == "scalp":
            score = float(item.get("scalp_rr", 0.0) or 0.0)
        else:
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
