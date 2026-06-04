import sys
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app_config
import knowledge_base
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
    sim_signal_bridge._STRATEGY_DRAWDOWN_CACHE = {}


@pytest.fixture(autouse=True)
def _isolate_runtime_config(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    monkeypatch.setenv("SIM_DISABLED_STRATEGIES_JSON", "[]")
    monkeypatch.setenv("SIM_DISABLED_STRATEGY_ACTIONS_JSON", "[]")
    _reset_active_rule_cache()
    yield
    _reset_active_rule_cache()


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


def test_build_rule_sim_signal_blocks_exploratory_observation_when_chasing_upper():
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
    assert exploratory_signal is None
    assert "上沿追价" in exploratory_reason


def test_build_rule_sim_signal_blocks_setup_early_momentum_chase_in_exploratory_mode():
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
    assert exploratory_signal is None
    assert "上沿追价" in exploratory_reason


def test_build_rule_sim_signal_blocks_setup_direct_momentum_upper_chase_in_exploratory_mode():
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
                "risk_reward_ratio": 2.60,
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
    assert exploratory_signal is None
    assert "上沿追价" in exploratory_reason


def test_build_rule_sim_signal_blocks_setup_pullback_sniper_upper_chase_in_exploratory_mode():
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
    assert exploratory_signal is None
    assert "上沿追价" in exploratory_reason


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


def test_build_rule_sim_signal_blocks_recent_drawdown_strategy(monkeypatch):
    monkeypatch.setattr(
        sim_signal_bridge,
        "_get_strategy_drawdown_lock",
        lambda family: {
            "locked": family == "direct_momentum",
            "win_rate": 0.125,
            "win_count": 1,
            "loss_count": 7,
            "net_profit": -64.8,
        },
    )

    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "setup",
                    "setup_kind": "direct_momentum",
                    "signal_side": "short",
                    "risk_reward_ready": True,
                    "risk_reward_state": "acceptable",
                    "risk_reward_ratio": 2.4,
                    "risk_reward_direction": "bearish",
                    "latest_price": 4476.90,
                    "bid": 4476.82,
                    "ask": 4476.98,
                    "risk_reward_stop_price": 4505.71,
                    "risk_reward_target_price": 4419.28,
                    "risk_reward_entry_zone_low": 4468.0,
                    "risk_reward_entry_zone_high": 4484.0,
                }
            ]
        },
        allow_exploratory=True,
    )

    assert signal is None
    assert "防回撤冬眠" in reason


def test_build_rule_sim_signal_blocks_disabled_strategy(monkeypatch):
    monkeypatch.setattr(
        sim_signal_bridge,
        "get_runtime_config",
        lambda: SimpleNamespace(
            sim_min_rr=1.60,
            sim_relaxed_rr=1.30,
            sim_model_min_probability=0.68,
            sim_strategy_min_rr={"direct_momentum": 2.20},
            sim_disabled_strategies=["direct_momentum"],
        ),
    )
    monkeypatch.setattr(sim_signal_bridge, "_get_strategy_drawdown_lock", lambda family: {"locked": False})

    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "setup",
                    "setup_kind": "direct_momentum",
                    "signal_side": "short",
                    "risk_reward_ready": True,
                    "risk_reward_state": "acceptable",
                    "risk_reward_ratio": 2.6,
                    "risk_reward_direction": "bearish",
                    "latest_price": 4476.90,
                    "bid": 4476.82,
                    "ask": 4476.98,
                    "risk_reward_stop_price": 4505.71,
                    "risk_reward_target_price": 4419.28,
                    "risk_reward_entry_zone_low": 4468.0,
                    "risk_reward_entry_zone_high": 4484.0,
                }
            ]
        },
        allow_exploratory=True,
    )

    assert signal is None
    assert "策略回滚禁用名单" in reason


def test_build_rule_sim_signal_blocks_disabled_strategy_action(monkeypatch):
    monkeypatch.setattr(
        sim_signal_bridge,
        "get_runtime_config",
        lambda: SimpleNamespace(
            sim_min_rr=1.60,
            sim_relaxed_rr=1.30,
            sim_model_min_probability=0.68,
            sim_strategy_min_rr={"structure": 1.55},
            sim_disabled_strategies=[],
            sim_disabled_strategy_actions=["structure:long"],
        ),
    )
    monkeypatch.setattr(sim_signal_bridge, "_get_strategy_drawdown_lock", lambda family: {"locked": False})

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
                    "risk_reward_state": "favorable",
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4759.82,
                    "bid": 4759.74,
                    "ask": 4759.91,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                    "risk_reward_atr": 18.0,
                }
            ]
        }
    )

    assert signal is None
    assert "策略方向回滚禁用名单" in reason


def test_build_rule_sim_signal_blocks_unvalidated_strategy(monkeypatch):
    monkeypatch.setattr(
        sim_signal_bridge,
        "get_runtime_config",
        lambda: SimpleNamespace(
            sim_min_rr=1.60,
            sim_relaxed_rr=1.30,
            sim_model_min_probability=0.68,
            sim_strategy_min_rr={"structure": 1.55},
            sim_disabled_strategies=[],
            sim_disabled_strategy_actions=[],
            sim_strategy_validation_enabled=True,
            sim_strategy_validation_min_samples=8,
            sim_strategy_validation_min_win_rate=45.0,
            sim_strategy_validation_min_profit_factor=1.10,
        ),
    )
    monkeypatch.setattr(
        sim_signal_bridge,
        "_get_strategy_validation_state",
        lambda family, action, config=None: {
            "passed": False,
            "total_count": 5,
            "min_samples": 8,
            "win_rate": 40.0,
            "net_profit": -12.0,
            "profit_factor": 0.8,
        },
    )
    monkeypatch.setattr(sim_signal_bridge, "_get_strategy_drawdown_lock", lambda family: {"locked": False})
    monkeypatch.setattr(sim_signal_bridge, "_get_strategy_learning_governance_state", lambda family: {"blocked": False})

    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "short",
                    "risk_reward_ready": True,
                    "risk_reward_state": "favorable",
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4759.82,
                    "bid": 4759.74,
                    "ask": 4759.91,
                    "risk_reward_stop_price": 4772.0,
                    "risk_reward_target_price": 4728.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                    "risk_reward_atr": 18.0,
                }
            ]
        }
    )

    assert signal is None
    assert "样本验证未通过" in reason


def test_strategy_validation_uses_au_ag_pair_alias_history(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge.db"
    with knowledge_base.open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        for idx, profit in enumerate([-1.0, -2.0, 3.0], start=1):
            conn.execute(
                """
                INSERT INTO trade_learning_journal (
                    sim_position_id, symbol, action, setup_kind, opened_at, closed_at,
                    updated_at, outcome_label, profit, entry_payload_json
                ) VALUES (?, 'XAGUSD', 'long', 'au_ag_zscore', '2026-06-03 00:00:00',
                    '2026-06-03 00:10:00', '2026-06-03 00:10:00', ?, ?, ?)
                """,
                (
                    idx,
                    "success" if profit > 0 else "fail",
                    profit,
                    json.dumps({"strategy_family": "au_ag_zscore"}, ensure_ascii=False),
                ),
            )
    monkeypatch.setattr(knowledge_base, "KNOWLEDGE_DB_FILE", db_path)

    state = sim_signal_bridge._get_strategy_validation_state(
        "au_ag_pair",
        "long",
        SimpleNamespace(
            sim_strategy_validation_enabled=True,
            sim_strategy_validation_min_samples=2,
            sim_strategy_validation_min_win_rate=45.0,
            sim_strategy_validation_min_profit_factor=1.10,
        ),
    )

    assert state["enabled"] is True
    assert state["strategy_family"] == "au_ag_zscore"
    assert state["total_count"] == 3
    assert state["passed"] is False
    assert state["net_profit"] == 0.0


def test_build_rule_sim_signal_blocks_archived_strategy_learning_governance(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge.db"
    with knowledge_base.open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        source_id = conn.execute(
            """
            INSERT INTO knowledge_sources (title, source_type, location, trust_level, tags_json, notes, created_at, updated_at)
            VALUES ('策略学习', 'strategy_learning', 'strategy_learning::au_ag_zscore::tighten', 'working', '[]', '', '2026-06-03 00:00:00', '2026-06-03 00:00:00')
            """
        ).lastrowid
        rule_id = conn.execute(
            """
            INSERT INTO knowledge_rules (
                source_id, section_title, category, asset_scope, rule_text,
                confidence, evidence_type, tags_json, logic_json, created_at
            ) VALUES (?, '模拟盘策略学习', 'risk', 'XAUUSD', '策略学习建议：冻结金银配对',
                'pending', '模拟盘策略学习', '[]', ?, '2026-06-03 00:00:00')
            """,
            (source_id, json.dumps({"source": "strategy_learning", "strategy_family": "au_ag_zscore"}, ensure_ascii=False)),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO rule_scores (
                rule_id, horizon_min, sample_count, success_count, mixed_count, fail_count,
                observe_count, success_rate, score, validation_status, updated_at
            ) VALUES (?, 30, 20, 4, 0, 16, 0, 0.20, 100.0, 'archived', '2026-06-03 00:00:00')
            """,
            (rule_id,),
        )
        conn.execute(
            """
            INSERT INTO rule_governance (rule_id, horizon_min, governance_status, rationale, updated_at)
            VALUES (?, 30, 'archived', '策略学习已归档，禁止自动开仓。', '2026-06-03 00:00:00')
            """,
            (rule_id,),
        )
    monkeypatch.setattr(knowledge_base, "KNOWLEDGE_DB_FILE", db_path)
    sim_signal_bridge._STRATEGY_LEARNING_GOV_CACHE.clear()
    monkeypatch.setattr(
        sim_signal_bridge,
        "get_runtime_config",
        lambda: SimpleNamespace(
            sim_min_rr=1.60,
            sim_relaxed_rr=1.30,
            sim_model_min_probability=0.68,
            sim_strategy_min_rr={"au_ag_zscore": 1.2},
            sim_disabled_strategies=[],
            sim_disabled_strategy_actions=[],
            sim_strategy_validation_enabled=False,
        ),
    )

    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAGUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "setup",
                    "setup_kind": "au_ag_pair",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_state": "favorable",
                    "risk_reward_ratio": 2.4,
                    "latest_price": 75.5,
                    "bid": 75.49,
                    "ask": 75.51,
                    "risk_reward_stop_price": 74.8,
                    "risk_reward_target_price": 77.0,
                    "risk_reward_entry_zone_low": 75.0,
                    "risk_reward_entry_zone_high": 75.8,
                    "atr14": 0.6,
                    "risk_reward_atr": 0.6,
                }
            ]
        }
    )

    assert signal is None
    assert "策略学习治理" in reason
    assert "自动开仓已冻结" in reason


def test_build_rule_sim_signal_blocks_macro_news_conflict():
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
                    "risk_reward_state": "favorable",
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4759.82,
                    "bid": 4759.74,
                    "ask": 4759.91,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "macro_news_items": [
                        {
                            "title": "Fed signals higher for longer as yields rise",
                            "importance": "high",
                            "symbols": ["XAUUSD"],
                            "bias_by_symbol": {"XAUUSD": "bearish"},
                        }
                    ],
                }
            ]
        }
    )

    assert signal is None
    assert "资讯流宏观政策冲突" in reason


def test_build_rule_sim_signal_blocks_macro_data_conflict():
    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "short",
                    "risk_reward_ready": True,
                    "risk_reward_state": "favorable",
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4759.82,
                    "bid": 4759.74,
                    "ask": 4759.91,
                    "risk_reward_stop_price": 4772.0,
                    "risk_reward_target_price": 4728.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "macro_data_items": [
                        {
                            "name": "VIX",
                            "importance": "medium",
                            "symbols": ["XAUUSD"],
                            "direction": "bullish",
                        }
                    ],
                }
            ]
        }
    )

    assert signal is None
    assert "结构化宏观数据冲突" in reason


def test_build_rule_sim_signal_blocks_event_risk_mode_text():
    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "short",
                    "risk_reward_ready": True,
                    "risk_reward_state": "favorable",
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4759.82,
                    "bid": 4759.74,
                    "ask": 4759.91,
                    "risk_reward_stop_price": 4772.0,
                    "risk_reward_target_price": 4728.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "event_risk_mode_text": "事件前高敏",
                }
            ]
        }
    )

    assert signal is None
    assert "事件高敏" in reason


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


def test_build_rule_sim_signal_blocks_short_against_bullish_m15_price_structure():
    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAGUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "short",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.1,
                    "latest_price": 76.35,
                    "bid": 76.34,
                    "ask": 76.36,
                    "risk_reward_stop_price": 76.95,
                    "risk_reward_target_price": 75.10,
                    "risk_reward_entry_zone_low": 76.20,
                    "risk_reward_entry_zone_high": 76.50,
                    "atr14": 0.35,
                    "m15_price_structure_ready": True,
                    "m15_price_structure_direction": "bullish",
                    "m15_price_structure_strength": 5,
                    "m15_price_structure_state": "higher_high_higher_low",
                    "m15_price_structure_text": "结构偏多：高低点逐级抬升，价格处在区间上半部。",
                }
            ]
        }
    )

    assert signal is None
    assert "裸K结构偏多" in reason


def test_build_rule_sim_signal_blocks_when_tick_shock_cooldown_active():
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
                    "latest_price": 4476.75,
                    "bid": 4476.70,
                    "ask": 4476.80,
                    "risk_reward_stop_price": 4468.0,
                    "risk_reward_target_price": 4494.0,
                    "risk_reward_entry_zone_low": 4474.0,
                    "risk_reward_entry_zone_high": 4478.0,
                    "atr14": 7.0,
                    "tick_shock_active": True,
                    "tick_shock_direction": "bullish",
                    "tick_shock_move": 0.75,
                    "tick_shock_threshold": 0.50,
                    "tick_shock_window_sec": 5.0,
                    "tick_shock_text": "XAUUSD Tick 异动冷却中：5.0秒窗口内急拉 +0.7500，阈值 0.5000。",
                }
            ]
        }
    )

    assert signal is None
    assert "Tick 异动冷却" in reason


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


def test_audit_rule_sim_signal_decision_splits_grade_gate_secondary_reason():
    audit = audit_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "signal_side": "",
                    "risk_reward_ready": False,
                    "risk_reward_ratio": 0.0,
                    "latest_price": 4795.0,
                    "bid": 4794.9,
                    "ask": 4795.1,
                }
            ]
        }
    )

    row = audit["rows"][0]
    assert row["reason_key"] == "grade_gate"
    assert row["secondary_reason_key"] == "rr_not_ready"
    assert row["secondary_reason_label"] == "盈亏比未准备好"
    assert row["tertiary_reason_key"] == "no_direction"
    assert row["tertiary_reason_label"] == "方向基础不足"
    assert audit["secondary_blocked_counts"]["rr_not_ready"] == 1
    assert audit["secondary_blocked_summary"][0]["reason_label"] == "盈亏比未准备好"
    assert audit["tertiary_blocked_counts"]["no_direction"] == 1
    assert any(item["reason_key"] == "signal_side_missing" for item in row["direction_components"])


def test_build_rule_sim_signal_decision_includes_grade_gate_detail():
    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "signal_side": "",
                    "risk_reward_ready": False,
                    "latest_price": 4795.0,
                    "bid": 4794.9,
                    "ask": 4795.1,
                }
            ]
        }
    )

    assert signal is None
    assert "未触发任何高级智能规则" in reason
    assert "细分：盈亏比未准备好" in reason


def test_build_rule_sim_signal_picks_actionable_scalp_candidate():
    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "setup",
                    "setup_kind": "scalp",
                    "scalp_ready": True,
                    "scalp_rr": 1.5,
                    "scalp_direction": "long",
                    "scalp_stop_price": 4750.0,
                    "scalp_target_price": 4780.0,
                    "scalp_target_2_price": 4790.0,
                    "latest_price": 4760.0,
                    "bid": 4759.9,
                    "ask": 4760.1,
                    "atr14": 5.0,
                    "risk_reward_atr": 5.0,
                }
            ]
        }
    )

    assert signal is not None
    assert signal["symbol"] == "XAUUSD"
    assert signal["action"] == "long"
    assert signal["price"] == 4760.1
    assert signal["sl"] == 4750.0
    assert signal["tp"] == 4780.0
    assert signal["tp2"] == 4790.0
    assert signal["source_kind"] == "setup"
    assert signal["setup_kind"] == "scalp"
    assert signal["strategy_family"] == "scalp"
    assert signal["risk_decision"]["allowed"] is True

