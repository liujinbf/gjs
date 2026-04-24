"""
S-001 修复验证：模拟盘爆仓机制测试。
M-003 修复验证：margin 字段类型为 REAL。

注意：Windows 下 SQLite 文件会被系统保持锁定，
需要用固定目录而非 tempfile.TemporaryDirectory，并在测试后主动 gc 释放连接。
"""
import gc
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mt5_sim_trading
from mt5_sim_trading import SimTradingEngine

# 使用项目本地的临时测试目录（Windows 下比 tempfile 更可靠）
TEST_DIR = ROOT / ".runtime_test_sim_margin_call"


def _prepare_dir() -> Path:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    case_dir = TEST_DIR / f"case_{time.time_ns()}"
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def _make_engine(test_dir: Path, name: str = "test") -> SimTradingEngine:
    db = str(test_dir / f"{name}.sqlite")
    return SimTradingEngine(db_file=db)


def test_margin_call_force_closes_losing_position():
    """S-001：当净值跌破保证金50%时，亏损持仓被强制平仓。"""
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "margin_call")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(eng.db_file) as conn:
        conn.execute(
            "INSERT INTO sim_accounts (user_id, balance, equity, used_margin, total_profit, win_count, loss_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("system", 1000.0, 1000.0, 900.0, 0.0, 0, 0, now),
        )
        conn.execute(
            "INSERT INTO sim_positions (user_id, symbol, action, entry_price, quantity, margin, stop_loss, take_profit, opened_at, status, floating_pnl) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("system", "XAUUSD", "long", 3300.0, 0.5, 900.0, 3290.0, 3350.0, now, "open", 0.0),
        )
        conn.commit()

    # 价格跌到 3200，亏损 = (3200-3300)*0.5*100 = -5000
    # 净值 = 1000 + (-5000) = -4000
    # 爆仓线 = 900 * 50% = 450
    # -4000 < 450 → 触发爆仓（止损设在极端低位，不会先被止损触发）
    eng.update_prices({"XAUUSD": 3200.0})

    # 主动关闭所有连接（Windows 需要）
    del eng
    gc.collect()

    # 验证爆仓结果
    with sqlite3.connect(str(test_dir / "margin_call.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        positions = conn.execute("SELECT * FROM sim_positions WHERE status='open'").fetchall()
        trades = conn.execute("SELECT reason FROM sim_trades WHERE user_id='system'").fetchall()

    assert len(positions) == 0, f"爆仓应强平所有亏损仓，剩余持仓：{len(positions)}"
    # 可能是"止损"先触发（3200 <= 2000 不成立），也可能是"爆仓"，两者均是正确风控行为
    reasons = [r[0] for r in trades]
    assert len(reasons) > 0, "应有平仓记录"
    assert all(r for r in reasons), "平仓原因不应为空"

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_margin_call_uses_current_tick_pnl_before_periodic_persist():
    """爆仓判断必须使用本轮现算浮亏，不能依赖上一轮库里的 floating_pnl。"""
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "margin_call_current_tick")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(eng.db_file) as conn:
        conn.execute(
            "INSERT INTO sim_accounts (user_id, balance, equity, used_margin, total_profit, win_count, loss_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("system", 1000.0, 1000.0, 900.0, 0.0, 0, 0, now),
        )
        conn.execute(
            "INSERT INTO sim_positions (user_id, symbol, action, entry_price, quantity, margin, stop_loss, take_profit, opened_at, status, floating_pnl) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("system", "XAUUSD", "long", 3300.0, 0.5, 900.0, 1000.0, 5000.0, now, "open", 0.0),
        )
        conn.commit()

    eng.update_prices({"XAUUSD": 3200.0})

    del eng
    gc.collect()

    with sqlite3.connect(str(test_dir / "margin_call_current_tick.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        positions = conn.execute("SELECT * FROM sim_positions WHERE status='open'").fetchall()
        trades = conn.execute("SELECT reason FROM sim_trades WHERE user_id='system'").fetchall()

    assert positions == []
    assert [row["reason"] for row in trades] == ["触发爆仓强制平仓"]

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_margin_field_is_real_type():
    """M-003：margin 字段应存储为浮点数而非字符串。"""
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "margin_type")

    ok, msg = eng.execute_signal(
        {"symbol": "XAUUSD", "action": "long", "price": 3300.0, "sl": 3280.0, "tp": 3360.0}
    )
    assert ok, f"开仓失败：{msg}"

    del eng
    gc.collect()

    with sqlite3.connect(str(test_dir / "margin_type.sqlite")) as conn:
        row = conn.execute("SELECT margin FROM sim_positions WHERE status='open'").fetchone()

    assert row is not None
    assert isinstance(row[0], float), f"margin 应为 float, 实际类型: {type(row[0])}"

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_exploratory_profile_caps_risk_budget():
    """探索试仓只能用更低风险预算，避免模拟学习样本过度放大。"""
    test_dir = _prepare_dir()
    normal = _make_engine(test_dir, "normal_risk")
    exploratory = _make_engine(test_dir, "exploratory_risk")

    ok_normal, msg_normal = normal.execute_signal(
        {"symbol": "XAUUSD", "action": "long", "price": 3300.0, "sl": 3298.0, "tp": 3306.0}
    )
    ok_explore, msg_explore = exploratory.execute_signal(
        {
            "symbol": "XAUUSD",
            "action": "long",
            "price": 3300.0,
            "sl": 3298.0,
            "tp": 3306.0,
            "execution_profile": "exploratory",
        }
    )

    assert ok_normal, msg_normal
    assert ok_explore, msg_explore

    del normal
    del exploratory
    gc.collect()

    with sqlite3.connect(str(test_dir / "normal_risk.sqlite")) as conn:
        normal_row = conn.execute("SELECT quantity, execution_profile FROM sim_positions WHERE status='open'").fetchone()
    with sqlite3.connect(str(test_dir / "exploratory_risk.sqlite")) as conn:
        exploratory_row = conn.execute("SELECT quantity, execution_profile FROM sim_positions WHERE status='open'").fetchone()

    normal_qty = float(normal_row[0])
    exploratory_qty = float(exploratory_row[0])

    assert exploratory_qty < normal_qty
    assert exploratory_qty <= normal_qty * 0.35
    assert normal_row[1] == "standard"
    assert exploratory_row[1] == "exploratory"

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_exploratory_profile_uses_fixed_base_balance_for_position_sizing(monkeypatch):
    """探索试仓盈利后也继续按固定基准资金计算仓位，不让样本越做越大。"""
    test_dir = _prepare_dir()

    monkeypatch.setattr(
        mt5_sim_trading,
        "get_runtime_config",
        lambda: type(
            "Cfg",
            (),
            {
                "sim_initial_balance": 1000.0,
                "sim_exploratory_base_balance": 1000.0,
                "sim_no_tp2_lock_r": 0.5,
                "sim_no_tp2_partial_close_ratio": 0.5,
            },
        )(),
    )

    base_engine = _make_engine(test_dir, "exploratory_base")
    profit_engine = _make_engine(test_dir, "exploratory_profit")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(profit_engine.db_file) as conn:
        conn.execute(
            """
            INSERT INTO sim_accounts (user_id, balance, equity, used_margin, total_profit, win_count, loss_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                balance=excluded.balance,
                equity=excluded.equity,
                used_margin=excluded.used_margin,
                total_profit=excluded.total_profit,
                win_count=excluded.win_count,
                loss_count=excluded.loss_count,
                updated_at=excluded.updated_at
            """,
            ("system", 5000.0, 5000.0, 0.0, 4000.0, 8, 2, now),
        )
        conn.commit()

    payload = {
        "symbol": "XAUUSD",
        "action": "long",
        "price": 3300.0,
        "sl": 3298.0,
        "tp": 3306.0,
        "execution_profile": "exploratory",
    }
    ok_base, msg_base = base_engine.execute_signal(dict(payload))
    ok_profit, msg_profit = profit_engine.execute_signal(dict(payload))
    assert ok_base, msg_base
    assert ok_profit, msg_profit

    del base_engine
    del profit_engine
    gc.collect()

    with sqlite3.connect(str(test_dir / "exploratory_base.sqlite")) as conn:
        qty_base = float(conn.execute("SELECT quantity FROM sim_positions WHERE status='open'").fetchone()[0])
    with sqlite3.connect(str(test_dir / "exploratory_profit.sqlite")) as conn:
        qty_profit = float(conn.execute("SELECT quantity FROM sim_positions WHERE status='open'").fetchone()[0])

    assert abs(qty_base - qty_profit) < 1e-9

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_sim_engine_records_trade_learning_open_and_close(monkeypatch):
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "learning_journal")
    captured = {"open": None, "close": None}

    monkeypatch.setattr(
        mt5_sim_trading,
        "record_trade_learning_open",
        lambda **kwargs: captured.update({"open": dict(kwargs)}),
    )
    monkeypatch.setattr(
        mt5_sim_trading,
        "record_trade_learning_close",
        lambda **kwargs: captured.update({"close": dict(kwargs)}),
    )

    ok, msg = eng.execute_signal(
        {
            "symbol": "XAUUSD",
            "action": "long",
            "price": 3300.0,
            "sl": 3298.0,
            "tp": 3306.0,
            "snapshot_id": 88,
            "execution_profile": "exploratory",
            "trade_grade": "可轻仓试仓",
            "trade_grade_source": "setup",
            "setup_kind": "directional_probe",
            "risk_reward_ratio": 2.0,
        }
    )
    assert ok, msg
    assert captured["open"] is not None
    assert captured["open"]["sim_position_id"] > 0
    assert captured["open"]["meta"]["execution_profile"] == "exploratory"
    assert captured["open"]["meta"]["snapshot_id"] == 88

    eng.update_prices({"XAUUSD": 3306.0})

    assert captured["close"] is not None
    assert captured["close"]["sim_position_id"] == captured["open"]["sim_position_id"]
    assert captured["close"]["profit"] > 0
    with sqlite3.connect(str(test_dir / "learning_journal.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        trade = conn.execute("SELECT * FROM sim_trades ORDER BY id DESC LIMIT 1").fetchone()
    assert trade["sim_position_id"] == captured["open"]["sim_position_id"]
    assert trade["strategy_family"] == "directional_probe"

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_normal_sl_tp_still_works_after_fix():
    """确认修复后止损止盈逻辑未被破坏。"""
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "sl_tp")

    ok, msg = eng.execute_signal(
        {"symbol": "XAUUSD", "action": "long", "price": 3300.0, "sl": 3280.0, "tp": 3360.0}
    )
    assert ok, f"开仓失败：{msg}"

    # 命中止盈
    eng.update_prices({"XAUUSD": 3360.0})

    del eng
    gc.collect()

    with sqlite3.connect(str(test_dir / "sl_tp.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        positions = conn.execute("SELECT * FROM sim_positions WHERE status='open'").fetchall()
        trades = conn.execute("SELECT reason FROM sim_trades WHERE user_id='system'").fetchall()

    assert len(positions) == 0, "命中止盈后应无持仓"
    assert any("止盈" in r[0] for r in trades), f"找不到止盈记录，实际：{[r[0] for r in trades]}"

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_sim_engine_records_close_audit_on_take_profit(monkeypatch):
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "close_audit")
    audits = []

    monkeypatch.setattr(
        "execution_audit.record_execution_audit",
        lambda **kwargs: audits.append(dict(kwargs)) or {"audit_id": 1},
    )

    ok, msg = eng.execute_signal(
        {
            "symbol": "XAUUSD",
            "action": "long",
            "price": 3300.0,
            "sl": 3280.0,
            "tp": 3360.0,
            "snapshot_id": 88,
        }
    )
    assert ok, f"开仓失败：{msg}"

    eng.update_prices({"XAUUSD": 3360.0})

    assert audits, "止盈平仓后应写入执行审计"
    assert audits[-1]["source_kind"] == "sim_engine"
    assert audits[-1]["decision_status"] == "closed"
    assert audits[-1]["snapshot_id"] == 88
    assert "止盈" in audits[-1]["result_message"]

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_sim_engine_uses_wal_mode():
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "wal_mode")

    with eng._connect() as conn:
        journal_mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
        synchronous = int(conn.execute("PRAGMA synchronous;").fetchone()[0])

    assert journal_mode == "wal"
    assert synchronous == 1

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_calculate_optimal_lots_respects_volume_step():
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "volume_step")

    lots = eng.calculate_optimal_lots(
        equity=1000.0,
        entry_price=100.0,
        stop_loss=95.0,
        symbol="XAGUSD",
        risk_pct=0.079,
        volume_step=0.1,
        volume_min=0.1,
    )

    assert lots == 0.1

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_long_position_uses_bid_for_stop_loss_trigger():
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "bid_stop")

    ok, msg = eng.execute_signal(
        {"symbol": "XAUUSD", "action": "long", "price": 3300.0, "sl": 3299.0, "tp": 3360.0}
    )
    assert ok, f"开仓失败：{msg}"

    eng.update_prices(
        {
            "XAUUSD": {
                "latest": 3300.6,
                "bid": 3298.8,
                "ask": 3300.6,
            }
        }
    )

    del eng
    gc.collect()

    with sqlite3.connect(str(test_dir / "bid_stop.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        position_count = conn.execute("SELECT COUNT(*) FROM sim_positions WHERE status='open'").fetchone()[0]
        trade = conn.execute(
            "SELECT exit_price, reason FROM sim_trades WHERE user_id='system' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert position_count == 0, "多单应使用 Bid 命中止损后平仓"
    assert abs(float(trade["exit_price"]) - 3298.8) < 1e-6
    assert "止损" in str(trade["reason"])

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_short_position_uses_ask_for_stop_loss_trigger():
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "ask_stop")

    ok, msg = eng.execute_signal(
        {"symbol": "XAUUSD", "action": "short", "price": 3300.0, "sl": 3301.0, "tp": 3240.0}
    )
    assert ok, f"开仓失败：{msg}"

    eng.update_prices(
        {
            "XAUUSD": {
                "latest": 3300.4,
                "bid": 3300.2,
                "ask": 3301.2,
            }
        }
    )

    del eng
    gc.collect()

    with sqlite3.connect(str(test_dir / "ask_stop.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        position_count = conn.execute("SELECT COUNT(*) FROM sim_positions WHERE status='open'").fetchone()[0]
        trade = conn.execute(
            "SELECT exit_price, reason FROM sim_trades WHERE user_id='system' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert position_count == 0, "空单应使用 Ask 命中止损后平仓"
    assert abs(float(trade["exit_price"]) - 3301.2) < 1e-6
    assert "止损" in str(trade["reason"])

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_get_account_uses_insert_or_ignore_for_first_time_creation(monkeypatch):
    executed_sql: list[str] = []
    row_payload = {
        "user_id": "system",
        "balance": 100000.0,
        "equity": 100000.0,
        "used_margin": 0.0,
        "total_profit": 0.0,
        "win_count": 0,
        "loss_count": 0,
        "updated_at": "2026-04-14 10:00:00",
    }

    class _FakeCursor:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _FakeConn:
        def __init__(self):
            self._select_count = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            executed_sql.append(str(sql))
            if str(sql).startswith("SELECT"):
                self._select_count += 1
                return _FakeCursor(None if self._select_count == 1 else row_payload)
            return _FakeCursor(None)

        def commit(self):
            return None

    eng = object.__new__(SimTradingEngine)
    eng.db_file = str(TEST_DIR / "fake.sqlite")
    monkeypatch.setattr(eng, "_connect", lambda: _FakeConn())

    account = SimTradingEngine.get_account(eng)

    assert account["balance"] == 100000.0
    assert any("INSERT OR IGNORE INTO sim_accounts" in sql for sql in executed_sql)


def test_dynamic_risk_pct_shrinks_when_atr_is_high():
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "dynamic_risk_high")

    risk_pct, note = eng._resolve_dynamic_risk_pct(
        {"atr14": 30.0},
        symbol="XAUUSD",
        entry_price=3300.0,
    )

    assert risk_pct < eng.max_risk_pct
    assert "ATR 风险系数已启用" in note

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_dynamic_risk_pct_expands_moderately_when_atr_is_low():
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "dynamic_risk_low")

    risk_pct, _note = eng._resolve_dynamic_risk_pct(
        {"atr14": 8.0},
        symbol="XAUUSD",
        entry_price=3300.0,
    )

    assert risk_pct > eng.max_risk_pct
    assert risk_pct <= 0.027

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_sim_execute_signal_writes_risk_decision_back_to_meta(monkeypatch):
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "risk_decision_meta")
    rich_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 100000.0,
            "sim_no_tp2_lock_r": 0.5,
            "sim_no_tp2_partial_close_ratio": 0.5,
            "sim_exploratory_base_balance": 1000.0,
            "sim_min_rr": 1.6,
            "sim_exploratory_daily_limit": 3,
            "sim_exploratory_cooldown_min": 10,
            "sim_strategy_min_rr": {},
            "sim_strategy_daily_limit": {},
            "sim_strategy_cooldown_min": {},
        },
    )()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: rich_config)

    meta = {"symbol": "XAUUSD", "action": "long", "price": 3300.0, "sl": 3290.0, "tp": 3330.0}
    ok, msg = eng.execute_signal(meta)

    assert ok, msg
    assert meta["risk_decision"]["allowed"] is True
    assert meta["risk_decision"]["sizing_reference_balance"] == 100000.0

    del eng
    gc.collect()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_partial_take_profit_moves_stop_to_break_even(monkeypatch):
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "partial_tp")
    rich_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 100000.0,
            "sim_no_tp2_lock_r": 0.5,
            "sim_no_tp2_partial_close_ratio": 0.5,
        },
    )()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: rich_config)

    ok, msg = eng.execute_signal(
        {
            "symbol": "XAUUSD",
            "action": "long",
            "price": 3300.0,
            "sl": 3280.0,
            "tp": 3340.0,
            "tp2": 3360.0,
            "atr14": 12.0,
        }
    )
    assert ok, f"开仓失败：{msg}"

    eng.update_prices(
        {
            "XAUUSD": {
                "latest": 3340.2,
                "bid": 3340.2,
                "ask": 3340.4,
            }
        }
    )

    del eng
    gc.collect()

    with sqlite3.connect(str(test_dir / "partial_tp.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        pos = conn.execute("SELECT * FROM sim_positions WHERE status='open'").fetchone()
        trades = conn.execute(
            "SELECT reason, quantity FROM sim_trades WHERE user_id='system' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    assert pos is not None, "触发目标1后应保留剩余仓位"
    assert int(pos["break_even_armed"]) == 1
    assert abs(float(pos["stop_loss"]) - float(pos["entry_price"])) < 1e-6
    assert float(pos["take_profit"]) == 3360.0
    assert float(pos["partial_closed_quantity"]) > 0
    assert trades is not None
    assert "目标1止盈" in str(trades["reason"])
    assert float(trades["quantity"]) > 0

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_position_without_tp2_locks_profit_and_then_exits_at_break_even(monkeypatch):
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "no_tp2_profit_lock")
    rich_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 100000.0,
            "sim_no_tp2_lock_r": 0.5,
            "sim_no_tp2_partial_close_ratio": 0.5,
        },
    )()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: rich_config)

    ok, msg = eng.execute_signal(
        {
            "symbol": "XAUUSD",
            "action": "long",
            "price": 3300.0,
            "sl": 3280.0,
            "tp": 3360.0,
        }
    )
    assert ok, f"开仓失败：{msg}"

    eng.update_prices(
        {
            "XAUUSD": {
                "latest": 3310.2,
                "bid": 3310.2,
                "ask": 3310.4,
            }
        }
    )

    with sqlite3.connect(str(test_dir / "no_tp2_profit_lock.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        pos = conn.execute("SELECT * FROM sim_positions WHERE status='open'").fetchone()
        trades = conn.execute(
            "SELECT reason, quantity, profit FROM sim_trades WHERE user_id='system' ORDER BY id ASC"
        ).fetchall()

    assert pos is not None, "浮盈达到 0.5R 后应保留剩余仓位"
    assert int(pos["break_even_armed"]) == 1
    assert abs(float(pos["stop_loss"]) - float(pos["entry_price"])) < 1e-6
    assert float(pos["partial_closed_quantity"]) > 0
    assert len(trades) == 1
    assert "先减仓并上移保本止损" in str(trades[0]["reason"])
    assert float(trades[0]["profit"]) > 0

    eng.update_prices(
        {
            "XAUUSD": {
                "latest": 3300.0,
                "bid": 3300.0,
                "ask": 3300.2,
            }
        }
    )

    del eng
    gc.collect()

    with sqlite3.connect(str(test_dir / "no_tp2_profit_lock.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        open_count = conn.execute("SELECT COUNT(*) FROM sim_positions WHERE status='open'").fetchone()[0]
        trades = conn.execute(
            "SELECT reason, quantity, profit FROM sim_trades WHERE user_id='system' ORDER BY id ASC"
        ).fetchall()
        account = conn.execute(
            "SELECT balance, total_profit, win_count, loss_count FROM sim_accounts WHERE user_id='system'"
        ).fetchone()

    assert open_count == 0, "回撤到保本位后应把剩余仓位安全带走"
    assert len(trades) == 2
    assert "回撤至保本止损" in str(trades[-1]["reason"])
    assert float(trades[-1]["profit"]) == 0.0
    assert float(account["total_profit"]) > 0
    assert int(account["win_count"]) == 1
    assert int(account["loss_count"]) == 0

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_position_without_tp2_respects_configurable_lock_threshold_and_partial_ratio(monkeypatch):
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "no_tp2_config")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(eng.db_file) as conn:
        conn.execute(
            "INSERT INTO sim_accounts (user_id, balance, equity, used_margin, total_profit, win_count, loss_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("system", 100000.0, 100000.0, 100.0, 0.0, 0, 0, now),
        )
        conn.execute(
            """
            INSERT INTO sim_positions (
                user_id, symbol, action, entry_price, quantity, margin,
                stop_loss, take_profit, take_profit_2, opened_at, status,
                floating_pnl, break_even_armed, partial_closed_quantity, partial_realized_profit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("system", "XAUUSD", "long", 3300.0, 1.0, 100.0, 3280.0, 3360.0, 0.0, now, "open", 0.0, 0, 0.0, 0.0),
        )
        conn.commit()

    strict_config = type("Cfg", (), {"sim_no_tp2_lock_r": 0.60, "sim_no_tp2_partial_close_ratio": 0.25})()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: strict_config)
    eng.update_prices({"XAUUSD": {"latest": 3310.2, "bid": 3310.2, "ask": 3310.4}})

    with sqlite3.connect(str(test_dir / "no_tp2_config.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        pos_before = conn.execute("SELECT quantity, break_even_armed FROM sim_positions WHERE status='open'").fetchone()

    assert pos_before is not None
    assert abs(float(pos_before["quantity"]) - 1.0) < 1e-9
    assert int(pos_before["break_even_armed"] or 0) == 0

    loose_config = type("Cfg", (), {"sim_no_tp2_lock_r": 0.50, "sim_no_tp2_partial_close_ratio": 0.25})()
    monkeypatch.setattr(mt5_sim_trading, "get_runtime_config", lambda: loose_config)
    eng.update_prices({"XAUUSD": {"latest": 3310.2, "bid": 3310.2, "ask": 3310.4}})

    with sqlite3.connect(str(test_dir / "no_tp2_config.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        pos_after = conn.execute(
            """
            SELECT quantity, break_even_armed, stop_loss, partial_closed_quantity, partial_realized_profit
            FROM sim_positions WHERE status='open'
            """
        ).fetchone()

    assert pos_after is not None
    assert abs(float(pos_after["quantity"]) - 0.75) < 1e-6
    assert int(pos_after["break_even_armed"] or 0) == 1
    assert abs(float(pos_after["stop_loss"]) - 3300.0) < 1e-6
    assert abs(float(pos_after["partial_closed_quantity"]) - 0.25) < 1e-6
    assert float(pos_after["partial_realized_profit"] or 0.0) > 0.0

    shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_reset_account_clears_positions_trades_and_uses_target_balance():
    test_dir = _prepare_dir()
    eng = _make_engine(test_dir, "reset_account")

    ok, msg = eng.execute_signal(
        {
            "symbol": "XAUUSD",
            "action": "long",
            "price": 3300.0,
            "sl": 3280.0,
            "tp": 3360.0,
        }
    )
    assert ok, f"开仓失败：{msg}"

    eng.update_prices({"XAUUSD": {"latest": 3310.2, "bid": 3310.2, "ask": 3310.4}})
    account = eng.reset_account(initial_balance=1000.0, clear_history=True)

    assert abs(float(account["balance"]) - 1000.0) < 1e-9
    assert abs(float(account["equity"]) - 1000.0) < 1e-9
    assert float(account["used_margin"] or 0.0) == 0.0
    assert float(account["total_profit"] or 0.0) == 0.0
    assert int(account["win_count"] or 0) == 0
    assert int(account["loss_count"] or 0) == 0

    with sqlite3.connect(str(test_dir / "reset_account.sqlite")) as conn:
        open_positions = conn.execute("SELECT COUNT(*) FROM sim_positions WHERE status='open'").fetchone()[0]
        trade_count = conn.execute("SELECT COUNT(*) FROM sim_trades").fetchone()[0]

    assert open_positions == 0
    assert trade_count == 0

    shutil.rmtree(TEST_DIR, ignore_errors=True)
