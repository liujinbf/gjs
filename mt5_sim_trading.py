"""
mt5_sim_trading.py - MT5 外汇/黄金专属模拟盘引擎
核心：
1. 初始 10 万美金虚拟账户。
2. 动态风控仓位：单笔强制止损为净值的 2%，自动逆运算开仓手数。
3. 严格的多空双向计算机制。
4. 实时计算浮游盈亏与强平监测。
"""
from contextlib import contextmanager
import json
import sqlite3
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any

from app_config import (
    PROJECT_DIR,
    get_runtime_config,
    get_sim_strategy_cooldown_min,
    get_sim_strategy_daily_limit,
    get_sim_strategy_min_rr,
)
from trade_learning import record_trade_learning_close, record_trade_learning_open
from trade_contracts import RiskDecision, StrategySignal

SIM_DB_PATH = PROJECT_DIR / ".runtime" / "mt5_sim_trading.sqlite"

class SimTradingEngine:
    def __init__(self, db_file: str | None = None):
        self.db_file = db_file or str(SIM_DB_PATH)
        self.default_leverage = 100.0  # 默认 100 倍杠杆
        self.max_risk_pct = 0.02       # 单笔最大风险暴露：本金的 2%
        self.init_database()

    def _sync_meta_payload(self, original_meta: dict | None, payload: dict) -> None:
        if isinstance(original_meta, dict):
            original_meta.clear()
            original_meta.update(dict(payload or {}))

    def _get_no_tp2_lock_settings(self) -> tuple[float, float]:
        try:
            config = get_runtime_config()
            lock_r = max(0.10, min(5.0, float(config.sim_no_tp2_lock_r)))
            partial_ratio = max(0.10, min(0.90, float(config.sim_no_tp2_partial_close_ratio)))
            return lock_r, partial_ratio
        except Exception as exc:
            logging.exception(f"读取无 TP2 锁盈配置失败，回退默认值：{exc}")
            return 0.5, 0.5

    def _get_initial_balance(self) -> float:
        try:
            config = get_runtime_config()
            return max(100.0, min(1000000.0, float(config.sim_initial_balance)))
        except Exception as exc:
            logging.exception(f"读取模拟盘起始本金配置失败，回退默认值：{exc}")
            return 1000.0

    def _get_exploratory_base_balance(self) -> float:
        try:
            config = get_runtime_config()
            configured = float(getattr(config, "sim_exploratory_base_balance", 0.0) or 0.0)
            if configured > 0:
                return max(100.0, min(1000000.0, configured))
            return self._get_initial_balance()
        except Exception as exc:
            logging.exception(f"读取探索试仓基准本金失败，回退默认值：{exc}")
            return self._get_initial_balance()

    def _get_baseline_atr_ratio(self, symbol: str) -> float:
        sym = str(symbol or "").upper()
        if "XAU" in sym:
            return 0.0040
        if "XAG" in sym:
            return 0.0120
        if sym.endswith("JPY"):
            return 0.0030
        return 0.0020

    def _resolve_dynamic_risk_pct(self, meta: dict, symbol: str, entry_price: float) -> tuple[float, str]:
        atr_candidates = (
            float(meta.get("atr14", 0.0) or 0.0),
            float(meta.get("risk_reward_atr", 0.0) or 0.0),
            float(meta.get("atr14_h4", 0.0) or 0.0),
        )
        atr = max(atr_candidates)
        if entry_price <= 0 or atr <= 0:
            return self.max_risk_pct, "未提供 ATR，沿用固定 2% 风险。"

        atr_ratio = atr / entry_price
        if atr_ratio <= 0:
            return self.max_risk_pct, "ATR 无效，沿用固定 2% 风险。"

        baseline_ratio = self._get_baseline_atr_ratio(symbol)
        multiplier = baseline_ratio / atr_ratio
        multiplier = max(0.60, min(multiplier, 1.35))
        risk_pct = self.max_risk_pct * multiplier
        risk_pct = max(0.012, min(risk_pct, 0.027))
        return (
            risk_pct,
            f"ATR 风险系数已启用（ATR/价格={atr_ratio:.4%}，风险预算={risk_pct:.2%}）。",
        )

    def _resolve_take_profit_2(self, meta: dict) -> float:
        for key in ("tp2", "take_profit_2", "target_2"):
            value = float(meta.get(key, 0.0) or 0.0)
            if value > 0:
                return value
        return 0.0

    def _build_strategy_param_snapshot(self, meta: dict) -> tuple[dict, str]:
        config = get_runtime_config()
        family = str(
            meta.get("strategy_family", "") or meta.get("setup_kind", "") or meta.get("trade_grade_source", "") or ""
        ).strip().lower()
        execution_profile = str(meta.get("execution_profile", "standard") or "standard").strip().lower()
        snapshot = {
            "strategy_family": family,
            "execution_profile": execution_profile,
            "min_rr": float(get_sim_strategy_min_rr(family, default=float(getattr(config, "sim_min_rr", 1.6) or 1.6), config=config)),
            "daily_limit": int(get_sim_strategy_daily_limit(family, default=int(getattr(config, "sim_exploratory_daily_limit", 3) or 3), config=config)),
            "cooldown_min": int(get_sim_strategy_cooldown_min(family, default=int(getattr(config, "sim_exploratory_cooldown_min", 10) or 10), config=config)),
        }
        family_label_map = {
            "pullback_sniper_probe": "回调狙击",
            "directional_probe": "方向试仓",
            "direct_momentum": "直线动能",
            "early_momentum": "早期动能",
            "structure": "结构候选",
            "setup": "Setup",
        }
        family_label = family_label_map.get(family, family or "未分类")
        summary = (
            f"{family_label} / {'探索' if execution_profile == 'exploratory' else '标准'}"
            f" / RR {snapshot['min_rr']:.2f}R"
            f" / 日上限 {snapshot['daily_limit']} 次"
            f" / 冷却 {snapshot['cooldown_min']} 分钟"
        )
        return snapshot, summary

    def _calculate_favorable_r_multiple(self, action: str, entry_price: float, stop_loss: float, trigger_price: float) -> float:
        initial_risk = abs(float(entry_price or 0.0) - float(stop_loss or 0.0))
        if initial_risk <= 1e-6:
            return 0.0
        if str(action or "").strip().lower() == "long":
            favorable_move = float(trigger_price or 0.0) - float(entry_price or 0.0)
        else:
            favorable_move = float(entry_price or 0.0) - float(trigger_price or 0.0)
        if favorable_move <= 0:
            return 0.0
        return favorable_move / initial_risk

    def init_database(self):
        Path(self.db_file).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sim_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT UNIQUE,
                    balance REAL DEFAULT 100000.0,
                    equity REAL DEFAULT 100000.0,
                    used_margin REAL DEFAULT 0.0,
                    total_profit REAL DEFAULT 0.0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    updated_at TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sim_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    symbol TEXT,
                    action TEXT,      -- 'long' or 'short'
                    entry_price REAL,
                    quantity REAL,    -- 手数 (Lots)
                    floating_pnl REAL DEFAULT 0.0,
                    margin REAL DEFAULT 0.0,
                    stop_loss REAL,
                    take_profit REAL,
                    take_profit_2 REAL DEFAULT 0.0,
                    break_even_armed INTEGER DEFAULT 0,
                    partial_closed_quantity REAL DEFAULT 0.0,
                    partial_realized_profit REAL DEFAULT 0.0,
                    execution_profile TEXT DEFAULT 'standard',
                    strategy_family TEXT DEFAULT '',
                    strategy_param_json TEXT DEFAULT '',
                    opened_at TEXT,
                    status TEXT DEFAULT 'open'
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sim_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sim_position_id INTEGER DEFAULT 0,
                    user_id TEXT,
                    symbol TEXT,
                    action TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    quantity REAL,
                    profit REAL,
                    closed_at TEXT,
                    reason TEXT,
                    execution_profile TEXT DEFAULT 'standard',
                    strategy_family TEXT DEFAULT '',
                    strategy_param_json TEXT DEFAULT ''
                )
            ''')
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_sim_positions_user_symbol_open
                ON sim_positions(user_id, symbol)
                WHERE status='open'
                """
            )
            self._ensure_column(conn, "sim_positions", "take_profit_2", "REAL DEFAULT 0.0")
            self._ensure_column(conn, "sim_positions", "break_even_armed", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "sim_positions", "partial_closed_quantity", "REAL DEFAULT 0.0")
            self._ensure_column(conn, "sim_positions", "partial_realized_profit", "REAL DEFAULT 0.0")
            self._ensure_column(conn, "sim_positions", "snapshot_id", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "sim_positions", "execution_profile", "TEXT DEFAULT 'standard'")
            self._ensure_column(conn, "sim_positions", "strategy_family", "TEXT DEFAULT ''")
            self._ensure_column(conn, "sim_positions", "strategy_param_json", "TEXT DEFAULT ''")
            self._ensure_column(conn, "sim_trades", "sim_position_id", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "sim_trades", "execution_profile", "TEXT DEFAULT 'standard'")
            self._ensure_column(conn, "sim_trades", "strategy_family", "TEXT DEFAULT ''")
            self._ensure_column(conn, "sim_trades", "strategy_param_json", "TEXT DEFAULT ''")
            conn.commit()

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        existing = {
            str(row["name"]).strip().lower()
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if str(column_name).strip().lower() in existing:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_file, timeout=15.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            yield conn
        finally:
            conn.close()

    def _normalize_volume_meta(
        self,
        volume_step: float | None = None,
        volume_min: float | None = None,
    ) -> tuple[float, float, int]:
        step = float(volume_step or 0.0)
        if step <= 0:
            step = 0.01
        minimum = float(volume_min or 0.0)
        if minimum <= 0:
            minimum = step
        minimum = max(minimum, step)
        step_text = f"{step:.10f}".rstrip("0").rstrip(".")
        decimals = 0
        if "." in step_text:
            decimals = len(step_text.split(".")[1])
        return step, minimum, decimals

    def get_account(self, user_id: str = "system") -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sim_accounts WHERE user_id = ?", (user_id,)).fetchone()
            if not row:
                initial_balance = self._get_initial_balance()
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT OR IGNORE INTO sim_accounts (user_id, balance, equity, updated_at) VALUES (?, ?, ?, ?)",
                    (user_id, initial_balance, initial_balance, now)
                )
                conn.commit()
                row = conn.execute("SELECT * FROM sim_accounts WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row)

    def reset_account(
        self,
        user_id: str = "system",
        initial_balance: float | None = None,
        clear_history: bool = True,
    ) -> dict:
        target_balance = max(100.0, min(1000000.0, float(initial_balance or self._get_initial_balance())))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if clear_history:
                conn.execute("DELETE FROM sim_positions WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM sim_trades WHERE user_id = ?", (user_id,))
            else:
                conn.execute(
                    "UPDATE sim_positions SET status='closed', floating_pnl=0.0 WHERE user_id=? AND status='open'",
                    (user_id,),
                )
            conn.execute(
                """
                INSERT INTO sim_accounts (
                    user_id, balance, equity, used_margin, total_profit, win_count, loss_count, updated_at
                ) VALUES (?, ?, ?, 0.0, 0.0, 0, 0, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    balance=excluded.balance,
                    equity=excluded.equity,
                    used_margin=0.0,
                    total_profit=0.0,
                    win_count=0,
                    loss_count=0,
                    updated_at=excluded.updated_at
                """,
                (user_id, target_balance, target_balance, now),
            )
            conn.commit()
        logging.info(f"🔄 模拟盘账户已重置：user={user_id}，起始本金=${target_balance:,.2f}")
        return self.get_account(user_id)

    def _get_contract_size(self, symbol: str) -> float:
        """获取合约乘数，XAUUSD通常是100盎司/手，外汇通常是100000基础货币/手"""
        symbol = symbol.upper()
        if "XAU" in symbol:
            return 100.0
        if "XAG" in symbol:
            return 5000.0
        # 默认标准外汇为 100000
        return 100000.0

    def _calculate_margin_and_pnl(
        self,
        symbol: str,
        lots: float,
        entry_price: float,
        current_price: float,
        is_long: bool,
        usdjpy_rate: float = 150.0,
    ) -> Tuple[float, float]:
        """
        BUG-010 修复：根据货币对属性，智能计算所需的美元保证金和美元盈亏。

        支持四类品种：
          ① XXX/USD（如 XAUUSD / EURUSD / GBPUSD / XAGUSD）
              保证金 = lots × contract_size × entry_price ÷ leverage（天然 USD）
              PnL    = price_diff × lots × contract_size（天然 USD）
          ② USD/JPY（基础货币为 USD，计价为 JPY）
              保证金 = lots × contract_size ÷ leverage（contract 本就是 USD 面值）
              PnL    = price_diff × lots × contract_size ÷ current_price（JPY→USD）
          ③ 交叉 JPY 盘（如 EURJPY / GBPJPY）
              保证金 ≈ lots × contract_size × entry_price ÷ leverage ÷ usdjpy_rate
              PnL    = price_diff × lots × contract_size ÷ current_price（JPY→USD）
          ④ 其余交叉盘（兜底，近似以 entry_price 折 USD）
              保证金 = lots × contract_size × entry_price ÷ leverage
              PnL    = price_diff × lots × contract_size

        :param usdjpy_rate: 交叉 JPY 盘折算用的近似 USDJPY 汇率，默认 150。
        :return: (required_margin_usd, pnl_usd)
        """
        sym = symbol.upper()
        contract_size = self._get_contract_size(sym)

        # 计价货币维度的原始价差盈亏
        price_diff = (current_price - entry_price) if is_long else (entry_price - current_price)
        pnl_quote = price_diff * lots * contract_size

        required_margin_usd = 0.0
        pnl_usd = 0.0

        if sym.endswith("USD"):
            # ① XXX/USD：计价货币即为 USD
            required_margin_usd = (lots * contract_size * entry_price) / self.default_leverage
            pnl_usd = pnl_quote

        elif sym.startswith("USD") and sym.endswith("JPY"):
            # ② USD/JPY：基础货币就是 USD，1 手等于 10 万 USD
            required_margin_usd = (lots * contract_size) / self.default_leverage
            # PnL 为日元，除以当前 USDJPY 汇率折为美元
            safe_rate = current_price if current_price > 0 else (entry_price if entry_price > 0 else 150.0)
            pnl_usd = pnl_quote / safe_rate

        elif sym.endswith("JPY"):
            # ③ 交叉 JPY 盘（EURJPY / GBPJPY 等）
            safe_ujpy = usdjpy_rate if usdjpy_rate > 0 else 150.0
            required_margin_usd = (lots * contract_size * entry_price) / self.default_leverage / safe_ujpy
            safe_rate = current_price if current_price > 0 else (entry_price if entry_price > 0 else safe_ujpy)
            pnl_usd = pnl_quote / safe_rate

        else:
            # ④ 兜底：其余交叉盘，近似处理
            required_margin_usd = (lots * contract_size * entry_price) / self.default_leverage
            pnl_usd = pnl_quote

        return required_margin_usd, pnl_usd

    def calculate_optimal_lots(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
        symbol: str,
        risk_pct: float | None = None,
        volume_step: float | None = None,
        volume_min: float | None = None,
    ) -> float:
        """
        根据2%固定风险法，计算能够开仓的最优手数。
        BUG-010 修复：loss_per_lot 须折算为 USD，与 risk_amount（USD）量纲一致。
        额外修复：按品种的 volume_step / volume_min 动态对齐手数，避免跨品种出现无效手数。
        """
        if stop_loss <= 0 or entry_price <= 0 or abs(entry_price - stop_loss) < 0.0001:
            volume_step_value, minimum_lot, _decimals = self._normalize_volume_meta(volume_step, volume_min)
            return max(minimum_lot, volume_step_value)

        effective_risk_pct = float(risk_pct if risk_pct is not None else self.max_risk_pct)
        effective_risk_pct = max(0.002, min(effective_risk_pct, 0.05))
        risk_amount = equity * effective_risk_pct
        sym = symbol.upper()
        contract_size = self._get_contract_size(sym)

        # 计价货币维度的每手亏损
        raw_loss_per_lot = abs(entry_price - stop_loss) * contract_size
        if raw_loss_per_lot <= 0:
            volume_step_value, minimum_lot, _decimals = self._normalize_volume_meta(volume_step, volume_min)
            return max(minimum_lot, volume_step_value)

        # 折算为 USD 维度
        if sym.endswith("USD"):
            loss_per_lot_usd = raw_loss_per_lot
        elif sym.endswith("JPY"):
            # 不管是 USDJPY 还是 EURJPY，PnL 都是 JPY，需除以入场价近似折美元
            loss_per_lot_usd = raw_loss_per_lot / entry_price if entry_price > 0 else raw_loss_per_lot
        else:
            loss_per_lot_usd = raw_loss_per_lot

        exact_lots = risk_amount / loss_per_lot_usd
        volume_step_value, minimum_lot, decimals = self._normalize_volume_meta(volume_step, volume_min)
        lots = math.floor(exact_lots / volume_step_value) * volume_step_value
        lots = round(lots, decimals)
        if lots < minimum_lot:
            lots = minimum_lot

        # 限制在合理区间
        return max(minimum_lot, min(lots, 50.0))

    def execute_signal(self, meta: dict, user_id: str = "system") -> Tuple[bool, str]:
        """将 AI 给出的机器信号转化为真实虚拟挂单"""
        original_meta = meta if isinstance(meta, dict) else None
        meta = StrategySignal.from_payload(meta).to_signal_meta()
        self._sync_meta_payload(original_meta, meta)
        symbol = meta.get("symbol", "").upper()
        action = meta.get("action", "").lower()
        entry_price = float(meta.get("price", 0.0))
        sl = float(meta.get("sl", 0.0))
        tp = float(meta.get("tp", 0.0))
        tp2 = self._resolve_take_profit_2(meta)
        execution_profile = str(meta.get("execution_profile", "standard") or "standard").strip().lower()
        if execution_profile not in {"standard", "exploratory"}:
            execution_profile = "standard"
        strategy_family = str(meta.get("strategy_family", "") or meta.get("setup_kind", "") or meta.get("trade_grade_source", "") or "").strip()
        strategy_param_snapshot, strategy_param_summary = self._build_strategy_param_snapshot(meta)
        meta["strategy_family"] = strategy_family
        meta["strategy_param_snapshot"] = dict(strategy_param_snapshot)
        meta["strategy_param_summary"] = strategy_param_summary

        if action not in ("long", "short"):
            return False, "非明确执行信号"
        if entry_price <= 0 or sl <= 0 or tp <= 0:
            return False, "缺失点位数据（Entry, SL, TP）"

        account = self.get_account(user_id)

        # 根据 2% 资金管理法则计算最优手数
        equity = float(account["equity"])
        risk_pct_used, risk_note = self._resolve_dynamic_risk_pct(meta, symbol, entry_price)
        sizing_reference_balance = equity
        if execution_profile == "exploratory":
            risk_pct_used = min(risk_pct_used, 0.005)
            sizing_reference_balance = self._get_exploratory_base_balance()
            risk_note = (
                f"探索试仓模式，风险预算已压低至 {risk_pct_used:.2%}，"
                f"并锁定按基准本金 ${sizing_reference_balance:,.2f} 计算仓位。"
            )
        meta["risk_budget_pct"] = float(risk_pct_used)
        meta["sizing_reference_balance"] = float(sizing_reference_balance)
        meta["risk_decision"] = RiskDecision(
            allowed=True,
            reason=risk_note,
            risk_budget_pct=float(risk_pct_used),
            sizing_reference_balance=float(sizing_reference_balance),
        ).to_dict()
        self._sync_meta_payload(original_meta, meta)
        lots = self.calculate_optimal_lots(
            sizing_reference_balance,
            entry_price,
            sl,
            symbol,
            risk_pct=risk_pct_used,
            volume_step=float(meta.get("volume_step", 0.0) or 0.0),
            volume_min=float(meta.get("volume_min", 0.0) or 0.0),
        )

        # BUG-010 修复：用汇率感知函数计算正确的 USD 保证金
        # current_price = entry_price（开仓瞬间，浮动为零）
        required_margin, _ = self._calculate_margin_and_pnl(
            symbol, lots, entry_price, entry_price, action == "long"
        )

        # 如果保证金不够
        available_margin = float(account["balance"]) - float(account["used_margin"])
        if required_margin > available_margin:
            return False, f"可用保证金不足（需 ${required_margin:.2f}，可用 ${available_margin:.2f}）"

        # 写入数据库
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                updated = conn.execute(
                    """
                    UPDATE sim_accounts
                    SET used_margin = used_margin + ?, updated_at = ?
                    WHERE user_id = ?
                      AND (balance - used_margin) >= ?
                    """,
                    (required_margin, now, user_id, required_margin),
                )
                if int(updated.rowcount or 0) <= 0:
                    conn.rollback()
                    return False, f"可用保证金不足（需 ${required_margin:.2f}，可用 ${available_margin:.2f}）"
                conn.execute(
                    """
                    INSERT INTO sim_positions
                    (
                        user_id, symbol, action, entry_price, quantity, margin,
                        stop_loss, take_profit, take_profit_2, opened_at, snapshot_id, execution_profile, strategy_family, strategy_param_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        symbol,
                        action,
                        entry_price,
                        lots,
                        required_margin,
                        sl,
                        tp,
                        tp2,
                        now,
                        int(meta.get("snapshot_id", 0)),
                        execution_profile,
                        strategy_family,
                        json.dumps(strategy_param_snapshot, ensure_ascii=False),
                    ),
                )
                position_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0] or 0)
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                return False, f"{symbol} 已有活跃持仓，跳过"
        try:
            record_trade_learning_open(
                sim_position_id=position_id,
                user_id=user_id,
                meta=meta,
                quantity=lots,
                required_margin=required_margin,
                sizing_balance=sizing_reference_balance,
                risk_budget_pct=risk_pct_used,
            )
        except Exception as exc:
            logging.exception(f"探索/标准试仓开仓学习日志写入失败：{exc}")

        logging.info(
            f"🟢 模拟盘已开仓: {action.upper()} {symbol} "
            f"(手数: {lots:.2f}, 风险预算: {risk_pct_used:.2%}, 基准资金: ${sizing_reference_balance:,.2f}, 止损: {sl}, 止盈1: {tp}, 止盈2: {tp2 or 0})"
        )
        return True, f"成功开仓 {lots:.2f} 手 {symbol}（{risk_note}）"

    def get_open_positions(self, user_id: str = "system") -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sim_positions WHERE user_id=? AND status='open'", (user_id,)).fetchall()
            return [dict(r) for r in rows]

    def _close_position_with_conn(
        self,
        conn: sqlite3.Connection,
        position_id: int,
        exit_price: float,
        reason: str,
        user_id: str = "system",
    ) -> float:
        """在已有连接内执行平仓结算，返回PnL（供 update_prices 共享连接时调用）。"""
        conn.row_factory = sqlite3.Row
        pos = conn.execute("SELECT * FROM sim_positions WHERE id=? AND status='open'", (position_id,)).fetchone()
        if not pos:
            return 0.0

        action = pos["action"]
        entry = float(pos["entry_price"])
        quantity = float(pos["quantity"])
        margin = float(pos["margin"] or 0.0)
        partial_realized_profit = float(pos["partial_realized_profit"] or 0.0)
        symbol = pos["symbol"]
        execution_profile = str(pos["execution_profile"] or "standard").strip().lower() if "execution_profile" in pos.keys() else "standard"
        strategy_family = str(pos["strategy_family"] or "").strip() if "strategy_family" in pos.keys() else ""
        strategy_param_json = str(pos["strategy_param_json"] or "").strip() if "strategy_param_json" in pos.keys() else ""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # BUG-010 修复：使用汇率感知函数计算正确的 USD 盈亏
        _, pnl = self._calculate_margin_and_pnl(
            symbol, quantity, entry, exit_price, action == "long"
        )

        conn.execute("UPDATE sim_positions SET status='closed', floating_pnl=? WHERE id=?", (pnl, position_id))
        conn.execute(
            """
            INSERT INTO sim_trades (
                sim_position_id, user_id, symbol, action, entry_price, exit_price, quantity, profit,
                closed_at, reason, execution_profile, strategy_family, strategy_param_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(position_id), user_id, symbol, action, entry, exit_price, quantity, pnl, now, reason, execution_profile, strategy_family, strategy_param_json),
        )
        total_position_pnl = partial_realized_profit + pnl
        win_add = 1 if total_position_pnl > 0 else 0
        loss_add = 1 if total_position_pnl < 0 else 0
        conn.execute(
            """
            UPDATE sim_accounts
            SET balance = balance + ?,
                used_margin = MAX(0, used_margin - ?),
                total_profit = total_profit + ?,
                win_count = win_count + ?,
                loss_count = loss_count + ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (pnl, margin, pnl, win_add, loss_add, now, user_id),
        )
        emoji = "🟢" if pnl > 0 else "🔴"
        logging.info(f"{emoji} 模拟盘平仓通知：{action.upper()} {symbol} 以 {exit_price:.2f} 平仓。盈亏: ${pnl:.2f} ({reason})")

        try:
            from execution_audit import record_execution_audit

            record_execution_audit(
                source_kind="sim_engine",
                decision_status="closed",
                snapshot_id=int(pos["snapshot_id"] or 0),
                meta={
                    "symbol": symbol,
                    "action": action,
                    "price": exit_price,
                    "sl": float(pos["stop_loss"] or 0.0),
                    "tp": float(pos["take_profit"] or 0.0),
                },
                result_message=f"{reason}；本次盈亏 {pnl:.2f} 美元",
                trade_mode="simulation",
                user_id=user_id,
            )
        except Exception as exc:
            logging.exception(f"模拟盘平仓审计写入失败：{exc}")

        # 反哺知识库
        snapshot_id = int(pos["snapshot_id"]) if "snapshot_id" in pos.keys() and pos["snapshot_id"] else 0
        if snapshot_id > 0:
            self._feedback_to_knowledge_base(
                snapshot_id=snapshot_id,
                symbol=symbol,
                action=action,
                entry_price=entry,
                exit_price=exit_price,
                reason=reason
            )
        try:
            record_trade_learning_close(
                sim_position_id=int(position_id),
                exit_price=exit_price,
                profit=total_position_pnl,
                reason=reason,
            )
        except Exception as exc:
            logging.exception(f"交易学习日志平仓更新失败：{exc}")

        return pnl

    def _feedback_to_knowledge_base(
        self,
        snapshot_id: int,
        symbol: str,
        action: str,
        entry_price: float,
        exit_price: float,
        reason: str,
    ) -> None:
        if snapshot_id <= 0:
            return

        outcome_label = "mixed"
        if "爆仓" in reason or "保护止损" in reason or "止损" in reason:
            outcome_label = "fail"
        elif "止盈" in reason:
            outcome_label = "success"

        price_change_pct = (exit_price - entry_price) / entry_price * 100.0
        if action == "short":
            price_change_pct = -price_change_pct

        try:
            from knowledge_base import open_knowledge_connection, KNOWLEDGE_DB_FILE

            with open_knowledge_connection(KNOWLEDGE_DB_FILE) as kb_conn:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                kb_conn.execute(
                    """
                    INSERT OR IGNORE INTO snapshot_outcomes (
                        snapshot_id, symbol, snapshot_time, horizon_min, future_snapshot_time,
                        future_price, future_spread_points, price_change_pct, max_price, min_price,
                        mfe_pct, mae_pct, outcome_label, signal_quality, labeled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        symbol,
                        now_str,
                        888,  # 用 888 作为模拟盘真实成交的独立周期类型
                        now_str,
                        exit_price,
                        0.0,
                        price_change_pct,
                        exit_price,
                        exit_price,
                        price_change_pct if price_change_pct > 0 else 0.0,
                        -price_change_pct if price_change_pct < 0 else 0.0,
                        outcome_label,
                        "sim_trade",
                        now_str
                    )
                )
                kb_conn.commit()
                logging.info(f"✅ 模拟盘反哺：将交易结果直接回标给知识库 (ID: {snapshot_id}, 结果: {outcome_label})")
        except Exception as exc:
            logging.exception(f"模拟盘反哺知识库失败：{exc}")

    def close_position(self, position_id: int, exit_price: float, reason: str, user_id: str = "system") -> None:
        """平仓结算逻辑（独立打开连接，供外部直接调用）。"""
        with self._connect() as conn:
            self._close_position_with_conn(conn, position_id, exit_price, reason, user_id)
            conn.commit()

    def _partially_close_position_with_conn(
        self,
        conn: sqlite3.Connection,
        position_id: int,
        exit_price: float,
        close_quantity: float,
        reason: str,
        user_id: str = "system",
    ) -> float:
        """分批止盈：减仓一部分，并把剩余仓位止损上移到保本。"""
        conn.row_factory = sqlite3.Row
        pos = conn.execute("SELECT * FROM sim_positions WHERE id=? AND status='open'", (position_id,)).fetchone()
        if not pos:
            return 0.0

        total_quantity = float(pos["quantity"] or 0.0)
        if close_quantity <= 0 or total_quantity <= 0 or close_quantity >= total_quantity:
            return 0.0

        action = str(pos["action"] or "").strip().lower()
        symbol = str(pos["symbol"] or "").strip().upper()
        entry = float(pos["entry_price"] or 0.0)
        margin = float(pos["margin"] or 0.0)
        execution_profile = str(pos["execution_profile"] or "standard").strip().lower() if "execution_profile" in pos.keys() else "standard"
        strategy_family = str(pos["strategy_family"] or "").strip() if "strategy_family" in pos.keys() else ""
        strategy_param_json = str(pos["strategy_param_json"] or "").strip() if "strategy_param_json" in pos.keys() else ""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        close_ratio = close_quantity / total_quantity
        released_margin = margin * close_ratio
        remaining_quantity = max(0.0, total_quantity - close_quantity)
        remaining_margin = max(0.0, margin - released_margin)

        _, pnl = self._calculate_margin_and_pnl(symbol, close_quantity, entry, exit_price, action == "long")
        partial_closed_quantity = float(pos["partial_closed_quantity"] or 0.0) + close_quantity
        partial_realized_profit = float(pos["partial_realized_profit"] or 0.0) + pnl

        conn.execute(
            """
            UPDATE sim_positions
            SET quantity = ?,
                margin = ?,
                break_even_armed = 1,
                stop_loss = entry_price,
                take_profit = CASE
                    WHEN COALESCE(take_profit_2, 0) > 0 THEN take_profit_2
                    ELSE take_profit
                END,
                partial_closed_quantity = ?,
                partial_realized_profit = ?,
                floating_pnl = 0.0
            WHERE id = ?
            """,
            (remaining_quantity, remaining_margin, partial_closed_quantity, partial_realized_profit, position_id),
        )
        conn.execute(
            """
            INSERT INTO sim_trades (
                sim_position_id, user_id, symbol, action, entry_price, exit_price, quantity, profit,
                closed_at, reason, execution_profile, strategy_family, strategy_param_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(position_id), user_id, symbol, action, entry, exit_price, close_quantity, pnl, now, reason, execution_profile, strategy_family, strategy_param_json),
        )
        conn.execute(
            """
            UPDATE sim_accounts
            SET balance = balance + ?,
                used_margin = MAX(0, used_margin - ?),
                total_profit = total_profit + ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (pnl, released_margin, pnl, now, user_id),
        )
        logging.info(
            f"🟡 模拟盘分批止盈：{action.upper()} {symbol} 以 {exit_price:.2f} 减仓 {close_quantity:.2f} 手，"
            f"已实现盈亏 ${pnl:.2f}，剩余 {remaining_quantity:.2f} 手并上移保本止损。"
        )
        try:
            from execution_audit import record_execution_audit

            record_execution_audit(
                source_kind="sim_engine",
                decision_status="closed",
                snapshot_id=int(pos["snapshot_id"] or 0),
                meta={
                    "symbol": symbol,
                    "action": action,
                    "price": exit_price,
                    "sl": float(pos["stop_loss"] or 0.0),
                    "tp": float(pos["take_profit"] or 0.0),
                },
                result_message=f"{reason}；本次减仓盈亏 {pnl:.2f} 美元",
                trade_mode="simulation",
                user_id=user_id,
            )
        except Exception as exc:
            logging.exception(f"模拟盘分批止盈审计写入失败：{exc}")
        return pnl

    def _arm_break_even_only_with_conn(self, conn: sqlite3.Connection, position_id: int) -> bool:
        """不上移分批止盈时，也允许把止损抬到保本，避免盈利单重新变大亏。"""
        conn.row_factory = sqlite3.Row
        pos = conn.execute("SELECT * FROM sim_positions WHERE id=? AND status='open'", (position_id,)).fetchone()
        if not pos:
            return False
        if bool(int(pos["break_even_armed"] or 0)):
            return False
        conn.execute(
            """
            UPDATE sim_positions
            SET break_even_armed = 1,
                stop_loss = entry_price
            WHERE id = ?
            """,
            (position_id,),
        )
        logging.info(
            f"🟦 模拟盘保本保护：{str(pos['action']).upper()} {str(pos['symbol']).upper()} "
            f"已将止损上移到保本位 {float(pos['entry_price'] or 0.0):.2f}。"
        )
        return True

    def _normalize_price_quote(self, value: Any) -> dict:
        if isinstance(value, dict):
            latest = float(value.get("latest", 0.0) or 0.0)
            bid = float(value.get("bid", 0.0) or 0.0)
            ask = float(value.get("ask", 0.0) or 0.0)
        else:
            latest = float(value or 0.0)
            bid = 0.0
            ask = 0.0
        if latest <= 0 and bid > 0 and ask > 0:
            latest = (bid + ask) / 2.0
        elif latest <= 0 and bid > 0:
            latest = bid
        elif latest <= 0 and ask > 0:
            latest = ask
        return {
            "latest": latest,
            "bid": bid,
            "ask": ask,
        }

    def update_prices(self, price_map: dict, user_id: str = "system") -> None:
        """
        根据实时最新价刷新所有未平仓头寸。
        如果触及止损止盈，则自动触发平仓。
        如果净值跌破已用保证金的50%（爆仓线），强制平掉所有亏损持仓。
        price_map = {'XAUUSD': 2000.5, ...}
        """
        positions = self.get_open_positions(user_id)
        if not positions:
            return

        with self._connect() as conn:
            for pos in positions:
                symbol = pos["symbol"]
                if symbol not in price_map:
                    continue

                quote = self._normalize_price_quote(price_map[symbol])
                latest_price = float(quote["latest"] or 0.0)
                current_bid = float(quote["bid"] or 0.0)
                current_ask = float(quote["ask"] or 0.0)
                action = pos["action"]
                entry = float(pos["entry_price"])
                qty = float(pos["quantity"])
                sl = float(pos["stop_loss"])
                tp = float(pos["take_profit"])
                tp2 = float(pos["take_profit_2"] or 0.0)
                break_even_armed = bool(int(pos["break_even_armed"] or 0))
                mark_price = latest_price
                trigger_price = latest_price
                if action == "long":
                    if current_bid > 0:
                        mark_price = current_bid
                        trigger_price = current_bid
                else:
                    if current_ask > 0:
                        mark_price = current_ask
                        trigger_price = current_ask
                if mark_price <= 0:
                    continue

                # BUG-010 修复：使用汇率感知函数计算正确的 USD 浮动盈亏
                _, pnl = self._calculate_margin_and_pnl(
                    symbol, qty, entry, mark_price, action == "long"
                )

                # 风控判断必须使用本轮实时浮盈，不能等节流写库。
                conn.execute("UPDATE sim_positions SET floating_pnl=? WHERE id=?", (pnl, pos["id"]))

                if not break_even_armed:
                    lock_r_threshold, partial_close_ratio = self._get_no_tp2_lock_settings()
                    partial_qty = math.floor((qty * partial_close_ratio) * 100) / 100.0
                    can_partial_close = partial_qty >= 0.01 and (qty - partial_qty) >= 0.01
                    if tp2 > 0:
                        if can_partial_close:
                            tp1_hit = (action == "long" and trigger_price >= tp) or (action == "short" and trigger_price <= tp)
                            if tp1_hit:
                                self._partially_close_position_with_conn(
                                    conn,
                                    pos["id"],
                                    trigger_price,
                                    partial_qty,
                                    "命中目标1止盈（分批减仓并上移保本止损）",
                                    user_id,
                                )
                                conn.commit()
                                continue
                    else:
                        favorable_r = self._calculate_favorable_r_multiple(action, entry, sl, trigger_price)
                        tp_hit_without_tp2 = (action == "long" and trigger_price >= tp) or (action == "short" and trigger_price <= tp)
                        if favorable_r >= lock_r_threshold and not tp_hit_without_tp2:
                            if can_partial_close:
                                self._partially_close_position_with_conn(
                                    conn,
                                    pos["id"],
                                    trigger_price,
                                    partial_qty,
                                    (
                                        f"浮盈达到 {favorable_r:.2f}R（阈值 {lock_r_threshold:.2f}R），"
                                        "先减仓并上移保本止损"
                                    ),
                                    user_id,
                                )
                                conn.commit()
                                continue
                            if self._arm_break_even_only_with_conn(conn, pos["id"]):
                                conn.commit()
                                continue

                # 检测 SL / TP 触发（共享连接，不再嵌套 connect）
                trigger_close = False
                reason = ""
                if action == "long":
                    if trigger_price <= sl:
                        trigger_close, reason = True, ("回撤至保本止损" if break_even_armed else "命中系统保护止损")
                    elif trigger_price >= tp:
                        trigger_close, reason = True, ("命中目标2止盈" if break_even_armed and tp2 > 0 else "命中目标止盈")
                elif action == "short":
                    if trigger_price >= sl:
                        trigger_close, reason = True, ("回撤至保本止损" if break_even_armed else "命中系统保护止损")
                    elif trigger_price <= tp:
                        trigger_close, reason = True, ("命中目标2止盈" if break_even_armed and tp2 > 0 else "命中目标止盈")

                if trigger_close:
                    # ✅ 使用共享连接版，避免嵌套 connect 导致 database is locked
                    self._close_position_with_conn(conn, pos["id"], trigger_price, reason, user_id)
                    conn.commit()

            # P-002 修复：只统计仍然 open 的仓位浮动盈亏，已平仓位的 pnl 已经结算进 balance，不再是「浮动」
            open_positions = conn.execute(
                "SELECT floating_pnl FROM sim_positions WHERE user_id=? AND status='open'", (user_id,)
            ).fetchall()
            total_floating_pnl = sum(float(r[0] or 0.0) for r in open_positions)
            try:
                account = conn.execute(
                    "SELECT balance, used_margin FROM sim_accounts WHERE user_id=?", (user_id,)
                ).fetchone()
                if account:
                    balance = float(account[0])
                    used_margin = float(account[1])
                    new_equity = balance + total_floating_pnl
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute(
                        "UPDATE sim_accounts SET equity=?, updated_at=? WHERE user_id=?",
                        (new_equity, now_str, user_id),
                    )
                    conn.commit()

                    # ── 爆仓检测：净值 < 已用保证金 × 50% ──
                    margin_call_threshold = used_margin * 0.5
                    if used_margin > 0 and new_equity < margin_call_threshold:
                        logging.warning(
                            f"⚠️ 模拟盘爆仓触发！净值 ${new_equity:.2f} 低于保证金线"
                            f" ${margin_call_threshold:.2f}（已用保证金 ${used_margin:.2f} × 50%）"
                        )
                        # ✅ 爆仓也使用共享连接版，避免死锁
                        self._force_close_all_losing_with_conn(conn, price_map, user_id)
                        conn.commit()
            except Exception as exc:
                logging.exception(f"模拟盘净值更新失败：{exc}")

    def _force_close_all_losing_with_conn(self, conn: sqlite3.Connection, price_map: dict, user_id: str = "system") -> None:
        """爆仓强制平仓（共享连接版）：平掉所有亏损持仓，保留盈利持仓（减轻损失）。"""
        conn.row_factory = sqlite3.Row
        positions = conn.execute(
            "SELECT * FROM sim_positions WHERE user_id=? AND status='open'", (user_id,)
        ).fetchall()
        for pos in positions:
            symbol = pos["symbol"]
            quote = self._normalize_price_quote(price_map.get(symbol, pos["entry_price"] or 0))
            current_price = float(quote["latest"] or 0.0)
            if pos["action"] == "long" and float(quote["bid"] or 0.0) > 0:
                current_price = float(quote["bid"] or 0.0)
            elif pos["action"] == "short" and float(quote["ask"] or 0.0) > 0:
                current_price = float(quote["ask"] or 0.0)
            if current_price <= 0:
                continue
            action = pos["action"]
            entry = float(pos["entry_price"])
            # BUG-010 修复：使用汇率感知函数计算正确的 USD 盈亏，再决定是否需要强平
            _, pnl = self._calculate_margin_and_pnl(
                symbol, float(pos["quantity"]), entry, current_price, action == "long"
            )
            # 只强平亏损仓
            if pnl < 0:
                self._close_position_with_conn(conn, pos["id"], current_price, "触发爆仓强制平仓", user_id)
                logging.warning(
                    f"💥 爆仓强平：{action.upper()} {symbol} 以 {current_price:.2f} 强制平仓，亏损 ${pnl:.2f}"
                )

# 单例全局引擎实例
SIM_ENGINE = SimTradingEngine()
