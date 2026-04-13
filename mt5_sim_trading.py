"""
mt5_sim_trading.py - MT5 外汇/黄金专属模拟盘引擎
核心：
1. 初始 10 万美金虚拟账户。
2. 动态风控仓位：单笔强制止损为净值的 2%，自动逆运算开仓手数。
3. 严格的多空双向计算机制。
4. 实时计算浮游盈亏与强平监测。
"""
import sqlite3
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from app_config import PROJECT_DIR

SIM_DB_PATH = PROJECT_DIR / ".runtime" / "mt5_sim_trading.sqlite"

class SimTradingEngine:
    def __init__(self, db_file: str | None = None):
        self.db_file = db_file or str(SIM_DB_PATH)
        self.default_leverage = 100.0  # 默认 100 倍杠杆
        self.max_risk_pct = 0.02       # 单笔最大风险暴露：本金的 2%
        self.init_database()

    def init_database(self):
        Path(self.db_file).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_file) as conn:
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
                    opened_at TEXT,
                    status TEXT DEFAULT 'open'
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sim_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    symbol TEXT,
                    action TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    quantity REAL,
                    profit REAL,
                    closed_at TEXT,
                    reason TEXT
                )
            ''')
            conn.commit()

    def get_account(self, user_id: str = "system") -> dict:
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM sim_accounts WHERE user_id = ?", (user_id,)).fetchone()
            if not row:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO sim_accounts (user_id, balance, equity, updated_at) VALUES (?, ?, ?, ?)",
                    (user_id, 100000.0, 100000.0, now)
                )
                conn.commit()
                row = conn.execute("SELECT * FROM sim_accounts WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row)

    def _get_contract_size(self, symbol: str) -> float:
        """获取合约乘数，XAUUSD通常是100盎司/手，外汇通常是100000基础货币/手"""
        symbol = symbol.upper()
        if "XAU" in symbol:
            return 100.0
        if "XAG" in symbol:
            return 5000.0
        # 默认标准外汇为 100000
        return 100000.0

    def calculate_optimal_lots(self, equity: float, entry_price: float, stop_loss: float, symbol: str) -> float:
        """
        根据2%固定风险法，计算能够开仓的最优手数。
        """
        if stop_loss <= 0 or entry_price <= 0 or abs(entry_price - stop_loss) < 0.0001:
            return 0.1  # 遇到异常值，给个低保 0.1 手

        risk_amount = equity * self.max_risk_pct
        contract_size = self._get_contract_size(symbol)
        
        # 每手亏损金额 = 点位差 * 每手合约单位
        loss_per_lot = abs(entry_price - stop_loss) * contract_size
        if loss_per_lot <= 0:
            return 0.1

        exact_lots = risk_amount / loss_per_lot
        # 向下取整到 0.01 手
        lots = math.floor(exact_lots * 100) / 100.0
        
        # 限制在合理区间
        return max(0.01, min(lots, 50.0))

    def execute_signal(self, meta: dict, user_id: str = "system") -> Tuple[bool, str]:
        """将 AI 给出的 TRACKER_META 信号转化为真实虚拟挂单"""
        symbol = meta.get("symbol", "").upper()
        action = meta.get("action", "").lower()
        entry_price = float(meta.get("price", 0.0))
        sl = float(meta.get("sl", 0.0))
        tp = float(meta.get("tp", 0.0))

        if action not in ("long", "short"):
            return False, "非明确执行信号"
        if entry_price <= 0 or sl <= 0 or tp <= 0:
            return False, "缺失点位数据（Entry, SL, TP）"

        account = self.get_account(user_id)
        
        # 检查是否已存在该品种的持仓
        with sqlite3.connect(self.db_file) as conn:
            pos = conn.execute(
                "SELECT id FROM sim_positions WHERE user_id=? AND symbol=? AND status='open'",
                (user_id, symbol)
            ).fetchone()
            if pos:
                return False, f"{symbol} 已有活跃持仓，跳过"

        # 根据 2% 资金管理法则计算最优手数
        equity = float(account["equity"])
        lots = self.calculate_optimal_lots(equity, entry_price, sl, symbol)
        
        # 计算保证金
        contract_size = self._get_contract_size(symbol)
        notional_value = lots * contract_size * entry_price
        required_margin = notional_value / self.default_leverage
        
        # 如果保证金不够
        available_margin = float(account["balance"]) - float(account["used_margin"])
        if required_margin > available_margin:
            return False, f"可用保证金不足（需 ${required_margin:.2f}，可用 ${available_margin:.2f}）"

        # 写入数据库
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_file) as conn:
            conn.execute('''
                INSERT INTO sim_positions 
                (user_id, symbol, action, entry_price, quantity, margin, stop_loss, take_profit, opened_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, symbol, action, entry_price, lots, required_margin, sl, tp, now))
            
            # 更新已用保证金
            new_used_margin = float(account["used_margin"]) + required_margin
            conn.execute(
                "UPDATE sim_accounts SET used_margin=?, updated_at=? WHERE user_id=?", 
                (new_used_margin, now, user_id)
            )
            conn.commit()

        logging.info(f"🟢 模拟盘已开仓: {action.upper()} {symbol} (手数: {lots:.2f}, 风险率2%算力, 止损: {sl}, 止盈: {tp})")
        return True, f"成功开仓 {lots} 手 {symbol}"

    def get_open_positions(self, user_id: str = "system") -> List[dict]:
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
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
        symbol = pos["symbol"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        contract_size = self._get_contract_size(symbol)
        if action == "long":
            pnl = (exit_price - entry) * quantity * contract_size
        else:
            pnl = (entry - exit_price) * quantity * contract_size

        conn.execute("UPDATE sim_positions SET status='closed', floating_pnl=? WHERE id=?", (pnl, position_id))
        conn.execute(
            "INSERT INTO sim_trades (user_id, symbol, action, entry_price, exit_price, quantity, profit, closed_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, symbol, action, entry, exit_price, quantity, pnl, now, reason),
        )
        account = conn.execute("SELECT * FROM sim_accounts WHERE user_id=?", (user_id,)).fetchone()
        if account:
            new_balance = float(account["balance"]) + pnl
            new_used_margin = max(0.0, float(account["used_margin"]) - margin)
            # P-001 修复：pnl恰好为0应算中性，不应计入 loss（平手不是业务失败）
            win_add = 1 if pnl > 0 else 0
            loss_add = 1 if pnl < 0 else 0
            new_win = int(account["win_count"]) + win_add
            new_loss = int(account["loss_count"]) + loss_add
            new_total = float(account["total_profit"]) + pnl
            conn.execute(
                "UPDATE sim_accounts SET balance=?, used_margin=?, total_profit=?, win_count=?, loss_count=?, updated_at=? WHERE user_id=?",
                (new_balance, new_used_margin, new_total, new_win, new_loss, now, user_id),
            )
        emoji = "🟢" if pnl > 0 else "🔴"
        logging.info(f"{emoji} 模拟盘平仓通知：{action.upper()} {symbol} 以 {exit_price:.2f} 平仓。盈亏: ${pnl:.2f} ({reason})")
        return pnl

    def close_position(self, position_id: int, exit_price: float, reason: str, user_id: str = "system") -> None:
        """平仓结算逻辑（独立打开连接，供外部直接调用）。"""
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            self._close_position_with_conn(conn, position_id, exit_price, reason, user_id)
            conn.commit()

    def update_prices(self, price_map: dict, user_id: str = "system") -> None:
        """
        根据实时最新价刷新所有未平仓头寸。
        如果触及止损止盈，则自动触发平仓。
        如果净值跌破已用保证金的50%（爆仓线），强制平掉所有亏损持仓。
        price_map = {'XAUUSD': 2000.5, 'EURUSD': ...}
        """
        positions = self.get_open_positions(user_id)
        if not positions:
            return

        total_floating_pnl = 0.0
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            for pos in positions:
                symbol = pos["symbol"]
                if symbol not in price_map:
                    total_floating_pnl += float(pos["floating_pnl"] or 0.0)
                    continue

                current_price = float(price_map[symbol])
                action = pos["action"]
                entry = float(pos["entry_price"])
                qty = float(pos["quantity"])
                sl = float(pos["stop_loss"])
                tp = float(pos["take_profit"])
                contract_size = self._get_contract_size(symbol)

                # 计算浮盈
                if action == "long":
                    pnl = (current_price - entry) * qty * contract_size
                else:
                    pnl = (entry - current_price) * qty * contract_size

                total_floating_pnl += pnl
                conn.execute("UPDATE sim_positions SET floating_pnl=? WHERE id=?", (pnl, pos["id"]))

                # 检测 SL / TP 触发（共享连接，不再嵌套 connect）
                trigger_close = False
                reason = ""
                if action == "long":
                    if current_price <= sl:
                        trigger_close, reason = True, "命中系统保护止损"
                    elif current_price >= tp:
                        trigger_close, reason = True, "命中目标止盈"
                elif action == "short":
                    if current_price >= sl:
                        trigger_close, reason = True, "命中系统保护止损"
                    elif current_price <= tp:
                        trigger_close, reason = True, "命中目标止盈"

                if trigger_close:
                    # ✅ 使用共享连接版，避免嵌套 connect 导致 database is locked
                    self._close_position_with_conn(conn, pos["id"], current_price, reason, user_id)
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
            current_price = float(price_map.get(symbol, pos["entry_price"] or 0))
            if current_price <= 0:
                continue
            action = pos["action"]
            entry = float(pos["entry_price"])
            contract_size = self._get_contract_size(symbol)
            if action == "long":
                pnl = (current_price - entry) * float(pos["quantity"]) * contract_size
            else:
                pnl = (entry - current_price) * float(pos["quantity"]) * contract_size
            # 只强平亏损仓
            if pnl < 0:
                self._close_position_with_conn(conn, pos["id"], current_price, "触发爆仓强制平仓", user_id)
                logging.warning(
                    f"💥 爆仓强平：{action.upper()} {symbol} 以 {current_price:.2f} 强制平仓，亏损 ${pnl:.2f}"
                )

# 单例全局引擎实例
SIM_ENGINE = SimTradingEngine()
