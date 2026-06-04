import sys
import gc
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
import mt5_sim_trading
from mt5_sim_trading import SimTradingEngine

TEST_DIR = ROOT / ".runtime_test_sim_kelly"

def _prepare_dir() -> Path:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    case_dir = TEST_DIR / f"case_{time.time_ns()}"
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir

def _make_engine(test_dir: Path, name: str = "test") -> SimTradingEngine:
    db = str(test_dir / f"{name}.sqlite")
    return SimTradingEngine(db_file=db)

def _insert_trade_record(db_file: str, strategy: str, profit: float, outcome: str):
    """往知识库插入已结算单条订单战绩，outcome 须为 'success' 或 'fail'"""
    from knowledge_base import open_knowledge_connection
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    import json
    entry_payload = {"strategy_family": strategy}
    with open_knowledge_connection(db_file, ensure_schema=True) as conn:
        conn.execute(
            """
            INSERT INTO trade_learning_journal (
                sim_position_id, user_id, snapshot_id, snapshot_time, symbol, action,
                execution_profile, trade_grade, trade_grade_source, signal_side,
                setup_kind, risk_reward_ratio, risk_reward_state,
                model_win_probability, execution_open_probability, entry_zone_side,
                regime_tag, event_risk_mode_text, sizing_reference_balance,
                risk_budget_pct, entry_price, stop_loss, take_profit, take_profit_2,
                quantity, required_margin, execution_note, entry_payload_json,
                opened_at, closed_at, exit_price, profit, outcome_label, close_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time_ns() % 10000000), "system", 999, now_str, "XAUUSD", "long",
                "standard", "trade", "setup", "buy",
                strategy, 2.0, "acceptable",
                0.70, 0.70, "mid",
                "trend", "normal", 1000.0,
                0.02, 3300.0, 3290.0, 3320.0, 0.0,
                0.1, 10.0, "note", json.dumps(entry_payload),
                now_str, now_str, 3320.0 if profit > 0 else 3290.0, profit, outcome, "reason"
            )
        )
        conn.commit()

def test_kelly_fallback_when_insufficient_samples(monkeypatch):
    """验证当成交样本数少于 sim_kelly_min_samples 时自动安全回退到 ATR 默认 2% 风险。"""
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "insufficient_samples")

    # 模拟知识库路径指向测试路径
    import knowledge_base
    monkeypatch.setattr(knowledge_base, "KNOWLEDGE_DB_FILE", str(test_dir / "insufficient_samples.sqlite"))

    config = type(
        "Cfg",
        (),
        {
            "sim_kelly_enabled": True,
            "sim_kelly_min_samples": 5,
            "sim_kelly_fraction": 0.25,
            "sim_kelly_max_risk_pct": 0.02,
        },
    )()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: config)

    # 写入 3 笔单（少于 5 笔）
    for _ in range(3):
        _insert_trade_record(str(test_dir / "insufficient_samples.sqlite"), "scalp", 50.0, "success")

    risk_pct, note = eng._resolve_kelly_risk_pct("scalp")
    assert risk_pct == eng.max_risk_pct
    assert "历史成交样本不足" in note

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)

def test_kelly_hibernate_on_negative_expectation(monkeypatch):
    """验证当期望值为负时（逆风期），凯利系统正确拦截并拦截开仓信号（冬眠机制）。"""
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "negative_exp")

    import knowledge_base
    monkeypatch.setattr(knowledge_base, "KNOWLEDGE_DB_FILE", str(test_dir / "negative_exp.sqlite"))

    config = type(
        "Cfg",
        (),
        {
            "sim_kelly_enabled": True,
            "sim_kelly_min_samples": 4,
            "sim_kelly_fraction": 0.25,
            "sim_kelly_max_risk_pct": 0.02,
            "sim_initial_balance": 10000.0,
            "sim_strategy_min_rr": {},
            "sim_strategy_daily_limit": {},
            "sim_strategy_cooldown_min": {},
        },
    )()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: config)

    # 写入 5 笔巨亏单，胜率 0%
    for _ in range(5):
        _insert_trade_record(str(test_dir / "negative_exp.sqlite"), "scalp", -20.0, "fail")

    risk_pct, note = eng._resolve_kelly_risk_pct("scalp")
    assert risk_pct == 0.0
    assert "胜率为 0%" in note

    # 执行信号触发冬眠拦截，返回开仓失败
    ok, msg = eng.execute_signal(
        {
            "symbol": "XAUUSD",
            "action": "long",
            "price": 3300.0,
            "sl": 3290.0,
            "tp": 3320.0,
            "strategy_family": "scalp",
        }
    )
    assert not ok
    assert "被凯利风控冬眠拦截" in msg

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_kelly_uses_au_ag_pair_alias_history(monkeypatch):
    """验证 au_ag_pair 会合并读取 au_ag_zscore 的历史战绩，避免配对策略绕过亏损样本。"""
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "au_ag_alias")

    import knowledge_base
    monkeypatch.setattr(knowledge_base, "KNOWLEDGE_DB_FILE", str(test_dir / "au_ag_alias.sqlite"))

    config = type(
        "Cfg",
        (),
        {
            "sim_kelly_enabled": True,
            "sim_kelly_min_samples": 4,
            "sim_kelly_fraction": 0.25,
            "sim_kelly_max_risk_pct": 0.02,
        },
    )()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: config)

    for _ in range(5):
        _insert_trade_record(str(test_dir / "au_ag_alias.sqlite"), "au_ag_zscore", -2.0, "fail")

    risk_pct, note = eng._resolve_kelly_risk_pct("au_ag_pair")

    assert risk_pct == 0.0
    assert "策略 au_ag_zscore 近期胜率为 0%" in note

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_kelly_calculates_correct_fractional_kelly_and_respects_cap(monkeypatch):
    """验证在正期望下正确计算四分一凯利比例，并且严格受到 2% 上限保护。"""
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "kelly_ok")

    import knowledge_base
    monkeypatch.setattr(knowledge_base, "KNOWLEDGE_DB_FILE", str(test_dir / "kelly_ok.sqlite"))

    config = type(
        "Cfg",
        (),
        {
            "sim_kelly_enabled": True,
            "sim_kelly_min_samples": 5,
            "sim_kelly_fraction": 0.25,
            "sim_kelly_max_risk_pct": 0.02,
            "sim_initial_balance": 10000.0,
        },
    )()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: config)

    # 胜 6 场（每场赢 30 美元），负 4 场（每场输 10 美元）
    # 胜率 p = 60%, 败率 q = 40%
    # 盈亏比 b = 30 / 10 = 3.0:1
    # 期望值 = 3 * 0.6 - 0.4 = +1.4
    # 凯利比例 f* = 1.4 / 3 = 46.66%
    # 四分一凯利 = 46.66% * 0.25 = 11.66%
    # 2% 上限强力拦截下，最终风险应精确等于 2.0% (0.02)
    for _ in range(6):
        _insert_trade_record(str(test_dir / "kelly_ok.sqlite"), "scalp", 30.0, "success")
    for _ in range(4):
        _insert_trade_record(str(test_dir / "kelly_ok.sqlite"), "scalp", -10.0, "fail")

    risk_pct, note = eng._resolve_kelly_risk_pct("scalp")
    assert abs(risk_pct - 0.02) < 1e-6
    assert "凯利动态风险预算已启用" in note
    assert "最终执行预算: 2.00%" in note

    # 若把上限放宽到 15% (0.15)，最终预算应为四分一凯利 11.66%
    config_loose = type(
        "Cfg",
        (),
        {
            "sim_kelly_enabled": True,
            "sim_kelly_min_samples": 5,
            "sim_kelly_fraction": 0.25,
            "sim_kelly_max_risk_pct": 0.15,
        },
    )()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: config_loose)

    risk_pct_loose, note_loose = eng._resolve_kelly_risk_pct("scalp")
    assert abs(risk_pct_loose - 0.116666) < 1e-3
    assert "最终执行预算: 11.67%" in note_loose

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)
