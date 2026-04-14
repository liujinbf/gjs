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
from typing import Optional, Dict, List, Tuple, Any

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
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_sim_positions_user_symbol_open
                ON sim_positions(user_id, symbol)
                WHERE status='open'
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_file, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def get_account(self, user_id: str = "system") -> dict:
        with self._connect() as conn:
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

    def calculate_optimal_lots(self, equity: float, entry_price: float, stop_loss: float, symbol: str) -> float:
        """
        根据2%固定风险法，计算能够开仓的最优手数。
        BUG-010 修复：loss_per_lot 须折算为 USD，与 risk_amount（USD）量纲一致。
        """
        if stop_loss <= 0 or entry_price <= 0 or abs(entry_price - stop_loss) < 0.0001:
            return 0.1  # 遇到异常值，给个低保 0.1 手

        risk_amount = equity * self.max_risk_pct
        sym = symbol.upper()
        contract_size = self._get_contract_size(sym)

        # 计价货币维度的每手亏损
        raw_loss_per_lot = abs(entry_price - stop_loss) * contract_size
        if raw_loss_per_lot <= 0:
            return 0.1

        # 折算为 USD 维度
        if sym.endswith("USD"):
            loss_per_lot_usd = raw_loss_per_lot
        elif sym.endswith("JPY"):
            # 不管是 USDJPY 还是 EURJPY，PnL 都是 JPY，需除以入场价近似折美元
            loss_per_lot_usd = raw_loss_per_lot / entry_price if entry_price > 0 else raw_loss_per_lot
        else:
            loss_per_lot_usd = raw_loss_per_lot

        exact_lots = risk_amount / loss_per_lot_usd
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

        # 根据 2% 资金管理法则计算最优手数
        equity = float(account["equity"])
        lots = self.calculate_optimal_lots(equity, entry_price, sl, symbol)
        
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
                    (user_id, symbol, action, entry_price, quantity, margin, stop_loss, take_profit, opened_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, symbol, action, entry_price, lots, required_margin, sl, tp, now),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                return False, f"{symbol} 已有活跃持仓，跳过"

        logging.info(f"🟢 模拟盘已开仓: {action.upper()} {symbol} (手数: {lots:.2f}, 风险率2%算力, 止损: {sl}, 止盈: {tp})")
        return True, f"成功开仓 {lots} 手 {symbol}"

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
        symbol = pos["symbol"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # BUG-010 修复：使用汇率感知函数计算正确的 USD 盈亏
        _, pnl = self._calculate_margin_and_pnl(
            symbol, quantity, entry, exit_price, action == "long"
        )

        conn.execute("UPDATE sim_positions SET status='closed', floating_pnl=? WHERE id=?", (pnl, position_id))
        conn.execute(
            "INSERT INTO sim_trades (user_id, symbol, action, entry_price, exit_price, quantity, profit, closed_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, symbol, action, entry, exit_price, quantity, pnl, now, reason),
        )
        win_add = 1 if pnl > 0 else 0
        loss_add = 1 if pnl < 0 else 0
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
        return pnl

    def close_position(self, position_id: int, exit_price: float, reason: str, user_id: str = "system") -> None:
        """平仓结算逻辑（独立打开连接，供外部直接调用）。"""
        with self._connect() as conn:
            self._close_position_with_conn(conn, position_id, exit_price, reason, user_id)
            conn.commit()

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
        price_map = {'XAUUSD': 2000.5, 'EURUSD': ...}
        或 {'XAUUSD': {'latest': 2000.5, 'bid': 2000.2, 'ask': 2000.8}}
        """
        positions = self.get_open_positions(user_id)
        if not positions:
            return

        total_floating_pnl = 0.0
        with self._connect() as conn:
            for pos in positions:
                symbol = pos["symbol"]
                if symbol not in price_map:
                    total_floating_pnl += float(pos["floating_pnl"] or 0.0)
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

                total_floating_pnl += pnl
                conn.execute("UPDATE sim_positions SET floating_pnl=? WHERE id=?", (pnl, pos["id"]))

                # 检测 SL / TP 触发（共享连接，不再嵌套 connect）
                trigger_close = False
                reason = ""
                if action == "long":
                    if trigger_price <= sl:
                        trigger_close, reason = True, "命中系统保护止损"
                    elif trigger_price >= tp:
                        trigger_close, reason = True, "命中目标止盈"
                elif action == "short":
                    if trigger_price >= sl:
                        trigger_close, reason = True, "命中系统保护止损"
                    elif trigger_price <= tp:
                        trigger_close, reason = True, "命中目标止盈"

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
