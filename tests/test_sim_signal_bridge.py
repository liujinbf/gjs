import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quote_models import SnapshotItem
import sim_signal_bridge
from sim_signal_bridge import audit_rule_sim_signal_decision, build_rule_sim_signal, build_rule_sim_signal_decision


class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeRuleConn:
    def __init__(self, rows, captured_sql=None):
        self._rows = rows
        self._captured_sql = captured_sql if captured_sql is not None else []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        self._captured_sql.append((" ".join(str(sql).split()), params))
        return _FakeRows(self._rows)


def _reset_active_rule_cache():
    sim_signal_bridge._ACTIVE_RULES_CACHE = []
    sim_signal_bridge._ACTIVE_RULES_CACHE_TIME = 0


def test_build_rule_sim_signal_picks_actionable_structure_candidate():
    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4759.82,
                    "bid": 4759.74,
                    "ask": 4759.91,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "risk_reward_target_price_2": 4810.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                    "risk_reward_atr": 18.0,
                }
            ]
        }
    )

    assert signal is not None
    assert signal["symbol"] == "XAUUSD"
    assert signal["action"] == "long"
    assert signal["price"] == 4759.91
    assert signal["sl"] == 4748.0
    assert signal["tp"] == 4788.0
    assert signal["tp2"] == 4810.0
    assert signal["atr14"] == 18.0
    assert signal["risk_reward_atr"] == 18.0
    assert signal["entry_zone_side"] == "middle"
    assert signal["source_kind"] == "structure"
    assert signal["trade_grade_source"] == "structure"
    assert signal["strategy_family"] == "structure"
    assert signal["risk_decision"]["allowed"] is True


def test_build_rule_sim_signal_skips_candidate_outside_entry_zone():
    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4795.0,
                    "bid": 4794.9,
                    "ask": 4795.1,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4810.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                }
            ]
        }
    )

    assert signal is None


def test_build_rule_sim_signal_allows_sim_only_exploratory_observation_candidate():
    snapshot = {
        "items": [
            {
                "symbol": "XAUUSD",
                "has_live_quote": True,
                "trade_grade": "只适合观察",
                "trade_grade_source": "structure",
                "signal_side": "neutral",
                "risk_reward_ready": True,
                "risk_reward_state": "acceptable",
                "risk_reward_ratio": 2.0,
                "risk_reward_direction": "bullish",
                "multi_timeframe_alignment": "aligned",
                "multi_timeframe_bias": "bullish",
                "latest_price": 4801.85,
                "bid": 4801.74,
                "ask": 4801.96,
                "risk_reward_stop_price": 4776.48,
                "risk_reward_target_price": 4852.58,
                "risk_reward_target_price_2": 4877.94,
                "risk_reward_entry_zone_low": 4792.33,
                "risk_reward_entry_zone_high": 4805.02,
                "atr14": 21.14,
                "risk_reward_atr": 21.14,
            }
        ]
    }

    strict_signal, strict_reason = build_rule_sim_signal_decision(snapshot)
    exploratory_signal, exploratory_reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=True)

    assert strict_signal is None
    assert "可轻仓试仓级别" in strict_reason
    assert exploratory_reason == ""
    assert exploratory_signal is not None
    assert exploratory_signal["symbol"] == "XAUUSD"
    assert exploratory_signal["action"] == "long"
    assert exploratory_signal["price"] == 4801.96
    assert exploratory_signal["execution_profile"] == "exploratory"
    assert exploratory_signal["entry_zone_side"] == "upper"
    assert exploratory_signal["risk_decision"]["block_code"] == "exploratory_ready"


def test_build_rule_sim_signal_allows_setup_early_momentum_in_exploratory_mode():
    snapshot = {
        "items": [
            {
                "symbol": "XAUUSD",
                "has_live_quote": True,
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "setup",
                "setup_kind": "early_momentum",
                "signal_side": "long",
                "risk_reward_ready": True,
                "risk_reward_state": "acceptable",
                "risk_reward_ratio": 1.46,
                "risk_reward_direction": "bullish",
                "latest_price": 4761.20,
                "bid": 4761.12,
                "ask": 4761.28,
                "risk_reward_stop_price": 4748.0,
                "risk_reward_target_price": 4788.0,
                "risk_reward_target_price_2": 4810.0,
                "risk_reward_entry_zone_low": 4750.0,
                "risk_reward_entry_zone_high": 4765.0,
                "atr14": 18.0,
                "risk_reward_atr": 18.0,
            }
        ]
    }

    strict_signal, strict_reason = build_rule_sim_signal_decision(snapshot)
    exploratory_signal, exploratory_reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=True)

    assert strict_signal is None
    assert "上沿追价" in strict_reason
    assert exploratory_reason == ""
    assert exploratory_signal is not None
    assert exploratory_signal["symbol"] == "XAUUSD"
    assert exploratory_signal["execution_profile"] == "exploratory"
    assert exploratory_signal["action"] == "long"


def test_build_rule_sim_signal_allows_setup_direct_momentum_to_bypass_upper_chase_in_exploratory_mode():
    snapshot = {
        "items": [
            {
                "symbol": "XAUUSD",
                "has_live_quote": True,
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "setup",
                "setup_kind": "direct_momentum",
                "signal_side": "long",
                "risk_reward_ready": True,
                "risk_reward_state": "favorable",
                "risk_reward_ratio": 1.82,
                "risk_reward_direction": "bullish",
                "latest_price": 4764.8,
                "bid": 4764.7,
                "ask": 4764.9,
                "risk_reward_stop_price": 4748.0,
                "risk_reward_target_price": 4788.0,
                "risk_reward_target_price_2": 4810.0,
                "risk_reward_entry_zone_low": 4750.0,
                "risk_reward_entry_zone_high": 4765.0,
                "atr14": 18.0,
                "risk_reward_atr": 18.0,
            }
        ]
    }

    strict_signal, strict_reason = build_rule_sim_signal_decision(snapshot)
    exploratory_signal, exploratory_reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=True)

    assert strict_signal is None
    assert "上沿追价" in strict_reason
    assert exploratory_reason == ""
    assert exploratory_signal is not None
    assert exploratory_signal["symbol"] == "XAUUSD"
    assert exploratory_signal["execution_profile"] == "exploratory"
    assert exploratory_signal["entry_zone_side"] == "upper"


def test_build_rule_sim_signal_allows_setup_pullback_sniper_in_exploratory_mode():
    snapshot = {
        "items": [
            {
                "symbol": "XAUUSD",
                "has_live_quote": True,
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "setup",
                "setup_kind": "pullback_sniper_probe",
                "signal_side": "long",
                "risk_reward_ready": True,
                "risk_reward_state": "acceptable",
                "risk_reward_ratio": 1.52,
                "risk_reward_direction": "bullish",
                "latest_price": 4778.40,
                "bid": 4778.32,
                "ask": 4778.48,
                "risk_reward_stop_price": 4766.40,
                "risk_reward_target_price": 4796.40,
                "risk_reward_target_price_2": 4802.40,
                "risk_reward_entry_zone_low": 4774.00,
                "risk_reward_entry_zone_high": 4780.00,
                "atr14": 8.0,
                "risk_reward_atr": 8.0,
            }
        ]
    }

    strict_signal, strict_reason = build_rule_sim_signal_decision(snapshot)
    exploratory_signal, exploratory_reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=True)

    assert strict_signal is None
    assert "上沿追价" in strict_reason
    assert exploratory_reason == ""
    assert exploratory_signal is not None
    assert exploratory_signal["symbol"] == "XAUUSD"
    assert exploratory_signal["action"] == "long"
    assert exploratory_signal["execution_profile"] == "exploratory"


def test_build_rule_sim_signal_uses_strategy_specific_rr_threshold(monkeypatch):
    monkeypatch.setattr(
        sim_signal_bridge,
        "get_runtime_config",
        lambda: SimpleNamespace(
            sim_min_rr=1.60,
            sim_relaxed_rr=1.30,
            sim_model_min_probability=0.68,
            sim_strategy_min_rr={"pullback_sniper_probe": 1.70},
        ),
    )
    snapshot = {
        "items": [
            {
                "symbol": "XAUUSD",
                "has_live_quote": True,
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "setup",
                "setup_kind": "pullback_sniper_probe",
                "signal_side": "long",
                "risk_reward_ready": True,
                "risk_reward_state": "acceptable",
                "risk_reward_ratio": 1.62,
                "risk_reward_direction": "bullish",
                "latest_price": 4778.40,
                "bid": 4778.32,
                "ask": 4778.48,
                "risk_reward_stop_price": 4766.40,
                "risk_reward_target_price": 4796.40,
                "risk_reward_entry_zone_low": 4770.00,
                "risk_reward_entry_zone_high": 4788.00,
            }
        ]
    }

    blocked_signal, blocked_reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=True)
    snapshot["items"][0]["risk_reward_ratio"] = 1.72
    allowed_signal, allowed_reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=True)

    assert blocked_signal is None
    assert "盈亏比还不够健康" in blocked_reason
    assert allowed_reason == ""
    assert allowed_signal is not None
    assert allowed_signal["execution_profile"] == "exploratory"


def test_build_rule_sim_signal_accepts_mid_quality_setup_when_model_is_strong():
    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "EURUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "",
                    "risk_reward_direction": "bullish",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 1.4,
                    "latest_price": 1.1720,
                    "bid": 1.1719,
                    "ask": 1.1721,
                    "risk_reward_stop_price": 1.1708,
                    "risk_reward_target_price": 1.1766,
                    "risk_reward_entry_zone_low": 1.1719,
                    "risk_reward_entry_zone_high": 1.1726,
                    "point": 0.0001,
                    "model_ready": True,
                    "model_win_probability": 0.72,
                }
            ]
        }
    )

    assert signal is not None
    assert signal["symbol"] == "EURUSD"
    assert signal["action"] == "long"
    assert signal["price"] == 1.1721
    assert signal["entry_zone_side"] == "lower"


def test_build_rule_sim_signal_uses_runtime_rr_thresholds(monkeypatch):
    monkeypatch.setattr(
        sim_signal_bridge,
        "get_runtime_config",
        lambda: SimpleNamespace(sim_min_rr=1.40, sim_relaxed_rr=1.2, sim_model_min_probability=0.61),
    )

    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "EURUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 1.42,
                    "latest_price": 1.1720,
                    "bid": 1.1719,
                    "ask": 1.1721,
                    "risk_reward_stop_price": 1.1708,
                    "risk_reward_target_price": 1.1766,
                    "risk_reward_entry_zone_low": 1.1719,
                    "risk_reward_entry_zone_high": 1.1726,
                    "point": 0.0001,
                    "model_ready": False,
                    "model_win_probability": 0.0,
                }
            ]
        }
    )

    assert signal is not None
    assert signal["action"] == "long"


def test_build_rule_sim_signal_decision_returns_block_reason():
    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4795.0,
                    "bid": 4794.9,
                    "ask": 4795.1,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4810.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                }
            ]
        }
    )

    assert signal is None
    assert "继续等回踩" in reason


def test_build_rule_sim_signal_blocks_long_when_price_is_near_upper_side():
    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.2,
                    "latest_price": 4764.8,
                    "bid": 4764.7,
                    "ask": 4764.9,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                }
            ]
        }
    )

    assert signal is None
    assert "上沿追价" in reason


def test_build_rule_sim_signal_blocks_short_when_price_is_near_lower_side():
    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "EURUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "short",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.0,
                    "latest_price": 1.1711,
                    "bid": 1.1710,
                    "ask": 1.1712,
                    "risk_reward_stop_price": 1.1730,
                    "risk_reward_target_price": 1.1675,
                    "risk_reward_entry_zone_low": 1.1710,
                    "risk_reward_entry_zone_high": 1.1725,
                    "point": 0.0001,
                    "atr14": 0.0012,
                }
            ]
        }
    )

    assert signal is None
    assert "下沿追空" in reason


def test_build_rule_sim_signal_accepts_actionable_short_candidate():
    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "short",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 1.8,
                    "latest_price": 4798.1,
                    "bid": 4798.0,
                    "ask": 4798.2,
                    "risk_reward_stop_price": 4815.0,
                    "risk_reward_target_price": 4765.0,
                    "risk_reward_target_price_2": 4750.0,
                    "risk_reward_entry_zone_low": 4792.0,
                    "risk_reward_entry_zone_high": 4800.0,
                    "atr14": 10.0,
                    "risk_reward_atr": 10.0,
                }
            ]
        }
    )

    assert signal is not None
    assert signal["symbol"] == "XAUUSD"
    assert signal["action"] == "short"
    assert signal["price"] == 4798.0
    assert signal["tp"] == 4765.0
    assert signal["sl"] == 4815.0
    assert signal["entry_zone_side"] == "upper"


def test_build_rule_sim_signal_accepts_snapshot_item_objects():
    signal = build_rule_sim_signal(
        {
            "items": [
                SnapshotItem(
                    symbol="XAUUSD",
                    latest_price=4759.82,
                    bid=4759.74,
                    ask=4759.91,
                    spread_points=17.0,
                    has_live_quote=True,
                    trade_grade="可轻仓试仓",
                    trade_grade_source="structure",
                    quote_status_code="live",
                    signal_side="long",
                    extra={
                        "risk_reward_ready": True,
                        "risk_reward_ratio": 2.4,
                        "risk_reward_stop_price": 4748.0,
                        "risk_reward_target_price": 4788.0,
                        "risk_reward_target_price_2": 4810.0,
                        "risk_reward_entry_zone_low": 4750.0,
                        "risk_reward_entry_zone_high": 4765.0,
                        "atr14": 18.0,
                        "risk_reward_atr": 18.0,
                    },
                )
            ]
        }
    )

    assert signal is not None
    assert signal["symbol"] == "XAUUSD"
    assert signal["action"] == "long"


def test_active_structured_rules_use_governance_chain(monkeypatch):
    _reset_active_rule_cache()
    captured_sql = []
    rows = [
        {
            "id": 101,
            "logic_json": '{"op":"AND","conditions":[{"field":"signal_side","op":"==","value":"long"}]}',
            "category": "entry",
        }
    ]

    monkeypatch.setattr(
        "knowledge_base.open_knowledge_connection",
        lambda *_args, **_kwargs: _FakeRuleConn(rows, captured_sql),
    )

    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.2,
                    "latest_price": 4759.82,
                    "bid": 4759.74,
                    "ask": 4759.91,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4770.0,
                    "atr14": 18.0,
                }
            ]
        }
    )

    assert signal is not None
    assert signal["symbol"] == "XAUUSD"
    sql_text = captured_sql[0][0]
    assert "FROM rule_governance rg" in sql_text
    assert "rg.governance_status = 'active'" in sql_text
    assert "confidence IN" not in sql_text


def test_archived_structured_rules_do_not_override_trade_grade(monkeypatch):
    _reset_active_rule_cache()

    monkeypatch.setattr(
        "knowledge_base.open_knowledge_connection",
        lambda *_args, **_kwargs: _FakeRuleConn([]),
    )

    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.2,
                    "latest_price": 4759.82,
                    "bid": 4759.74,
                    "ask": 4759.91,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4762.0,
                    "atr14": 18.0,
                }
            ]
        }
    )

    assert signal is None
    assert "未触发任何高级智能规则" in reason


def test_audit_rule_sim_signal_decision_counts_block_reasons():
    audit = audit_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4795.0,
                    "bid": 4794.9,
                    "ask": 4795.1,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4810.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                },
                {
                    "symbol": "EURUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 1.9,
                    "latest_price": 1.1715,
                    "bid": 1.1714,
                    "ask": 1.1716,
                    "risk_reward_stop_price": 1.1740,
                    "risk_reward_target_price": 1.1750,
                    "risk_reward_entry_zone_low": 1.1710,
                    "risk_reward_entry_zone_high": 1.1722,
                    "point": 0.0001,
                },
            ]
        }
    )

    assert audit["ready_count"] == 0
    assert audit["blocked_counts"]["entry_zone_miss"] == 1
    assert audit["blocked_counts"]["direction_unclear"] == 1
    labels = {row["reason_key"]: row["reason_label"] for row in audit["blocked_summary"]}
    assert labels["entry_zone_miss"] == "未回到执行区"
    assert labels["direction_unclear"] == "方向不清晰"
