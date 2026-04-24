import logging
import math
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Tuple, List

try:
    import MetaTrader5 as mt5

    HAS_MT5 = True
except ImportError:
    mt5 = None
    HAS_MT5 = False
from app_config import get_runtime_config
from broker_gateway import resolve_broker_symbol, to_internal_symbol
from trade_contracts import ExecutionResult, OrderIntent, RiskDecision, StrategySignal

# Constants for common slippage allowance
DEFAULT_DEVIATION = 20
LIVE_ORDER_MAGIC = 2026416
LIVE_ORDER_COMMENT = "AI-LIVE-ORDER"


def _mt5_lock_context():
    try:
        from mt5_gateway import get_mt5_call_lock

        return get_mt5_call_lock()
    except Exception:  # noqa: BLE001
        return nullcontext()


class MetalLiveTradingEngine:
    def __init__(self):
        self._max_risk_pct = 0.02
        self.last_execution_result = ExecutionResult(ok=False, message="尚未执行实盘信号", trade_mode="live")

    def _sync_meta_payload(self, original_meta: dict | None, payload: dict) -> None:
        if isinstance(original_meta, dict):
            original_meta.clear()
            original_meta.update(dict(payload or {}))

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
            return self._max_risk_pct, "未提供 ATR，沿用固定最高风险。"

        atr_ratio = atr / entry_price
        if atr_ratio <= 0:
            return self._max_risk_pct, "ATR 无效，沿用固定最高风险。"

        baseline_ratio = self._get_baseline_atr_ratio(symbol)
        multiplier = baseline_ratio / atr_ratio
        multiplier = max(0.60, min(multiplier, 1.35))
        risk_pct = self._max_risk_pct * multiplier
        risk_pct = max(0.012, min(risk_pct, 0.027))
        return (
            risk_pct,
            f"ATR 风险系数已启用（ATR/价格={atr_ratio:.4%}，风险预算={risk_pct:.2%}）。",
        )

    def get_account(self, user_id: str = "system") -> dict:
        """获取真实的 MT5 账户预付款和净值信息"""
        if not HAS_MT5:
            logging.error("❌ 未安装 MetaTrader5 Python 库，无法读取真实账户。")
            return {
                "balance": 0.0,
                "equity": 0.0,
                "used_margin": 0.0,
                "total_profit": 0.0
            }
        with _mt5_lock_context():
            account_info = mt5.account_info()
        if not account_info:
            logging.error(f"❌ 无法从 MT5 获取账户信息，错误代码：{mt5.last_error()}")
            return {
                "balance": 0.0,
                "equity": 0.0,
                "used_margin": 0.0,
                "total_profit": 0.0
            }
        
        return {
            "balance": account_info.balance,
            "equity": account_info.equity,
            "used_margin": account_info.margin,
            "total_profit": account_info.profit
        }

    def _get_contract_size(self, symbol: str) -> float:
        """获取合约乘数，通过 MT5 原生属性"""
        if not HAS_MT5:
            return 100000.0
        broker_symbol = resolve_broker_symbol(symbol).broker
        with _mt5_lock_context():
            symbol_info = mt5.symbol_info(broker_symbol)
        if symbol_info is None:
            return 100000.0
        return symbol_info.trade_contract_size

    def _normalize_volume_meta(self, symbol_info) -> tuple[float, float, float, int]:
        step_volume = float(getattr(symbol_info, "volume_step", 0.0) or 0.0)
        min_volume = float(getattr(symbol_info, "volume_min", 0.01) or 0.01)
        max_volume = float(getattr(symbol_info, "volume_max", 100.0) or 100.0)
        if step_volume <= 0:
            step_volume = 0.01
        if min_volume <= 0:
            min_volume = step_volume
        if max_volume < min_volume:
            max_volume = min_volume
        step_text = f"{float(step_volume or 0.01):.10f}".rstrip("0").rstrip(".")
        decimals = len(step_text.split(".")[1]) if "." in step_text else 0
        return step_volume, min_volume, max_volume, decimals

    def _estimate_loss_per_lot(self, symbol_info, symbol: str, entry_price: float, stop_loss: float) -> tuple[float, dict]:
        sym = str(symbol or "").upper()
        price_risk = abs(float(entry_price or 0.0) - float(stop_loss or 0.0))
        tick_size = float(getattr(symbol_info, "trade_tick_size", 0.0) or 0.0)
        tick_value = float(getattr(symbol_info, "trade_tick_value", 0.0) or 0.0)
        contract_size = float(getattr(symbol_info, "trade_contract_size", 100000.0) or 100000.0)
        if tick_size > 0 and tick_value > 0:
            loss_per_lot = price_risk / tick_size * tick_value
            method = "broker_tick_value"
        else:
            raw_loss_per_lot = price_risk * contract_size
            if sym.endswith("USD"):
                loss_per_lot = raw_loss_per_lot
                method = "contract_size_usd_quote"
            elif sym.endswith("JPY"):
                loss_per_lot = raw_loss_per_lot / entry_price if entry_price > 0 else raw_loss_per_lot
                method = "contract_size_jpy_to_usd"
            else:
                loss_per_lot = raw_loss_per_lot
                method = "contract_size_fallback"
        return float(loss_per_lot), {
            "sizing_method": method,
            "price_risk": float(price_risk),
            "tick_size": float(tick_size),
            "tick_value": float(tick_value),
            "contract_size": float(contract_size),
            "loss_per_lot_usd": float(loss_per_lot),
        }

    def build_order_plan(
        self,
        *,
        signal: StrategySignal,
        equity: float,
        entry_price: float,
        risk_pct: float,
        symbol_info,
        risk_note: str = "",
    ) -> dict:
        step_volume, min_volume, max_volume, decimals = self._normalize_volume_meta(symbol_info)
        loss_per_lot_usd, sizing_meta = self._estimate_loss_per_lot(
            symbol_info,
            signal.symbol,
            entry_price,
            signal.sl,
        )
        effective_risk_pct = max(0.002, min(float(risk_pct), 0.05))
        risk_amount = float(equity) * effective_risk_pct
        if loss_per_lot_usd <= 0:
            lots = min_volume
        else:
            exact_lots = risk_amount / loss_per_lot_usd
            lots = math.floor(exact_lots / step_volume) * step_volume
            lots = round(max(min_volume, min(lots, max_volume)), decimals)
        risk_decision = RiskDecision(
            allowed=lots > 0,
            reason=risk_note,
            risk_budget_pct=float(effective_risk_pct),
            sizing_reference_balance=float(equity),
            notes=(f"预计单手止损亏损 {loss_per_lot_usd:.2f} USD",),
        )
        intent = OrderIntent(signal=signal, trade_mode="live", volume=lots, risk_decision=risk_decision)
        return {
            "order_intent": intent.to_dict(),
            "risk_decision": risk_decision.to_dict(),
            "sizing": {
                **sizing_meta,
                "risk_amount_usd": float(risk_amount),
                "volume_step": float(step_volume),
                "volume_min": float(min_volume),
                "volume_max": float(max_volume),
                "planned_lots": float(lots),
            },
        }

    def calculate_optimal_lots(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
        symbol: str,
        risk_pct: float | None = None,
    ) -> float:
        """根据最新汇率与止损点计算 MT5 真实最优开仓手数"""
        if not HAS_MT5:
            return 0.0
        if stop_loss <= 0 or entry_price <= 0 or abs(entry_price - stop_loss) < 0.0001:
            return 0.01

        with _mt5_lock_context():
            symbol_info = mt5.symbol_info(resolve_broker_symbol(symbol).broker)
        if not symbol_info:
            return 0.01

        effective_risk_pct = float(risk_pct if risk_pct is not None else self._max_risk_pct)
        effective_risk_pct = max(0.002, min(effective_risk_pct, 0.05))
        signal = StrategySignal.from_payload(
            {"symbol": symbol, "action": "long", "price": entry_price, "sl": stop_loss, "tp": entry_price + abs(entry_price - stop_loss)}
        )
        plan = self.build_order_plan(
            signal=signal,
            equity=float(equity),
            entry_price=float(entry_price),
            risk_pct=effective_risk_pct,
            symbol_info=symbol_info,
        )
        return float(plan["sizing"]["planned_lots"])

    def _check_daily_drawdown_circuit_breaker(self) -> bool:
        """检查今日是否触发日内亏损熔断保护"""
        if not HAS_MT5:
            logging.critical("实盘熔断检查失败：未安装 MetaTrader5 Python 库，禁止实盘开仓。")
            return True
        try:
            config = get_runtime_config()
            max_drawdown = float(getattr(config, "live_max_drawdown_pct", 0.05) or 0.05)
            max_drawdown = max(0.005, min(max_drawdown, 0.50))

            with _mt5_lock_context():
                account_info = mt5.account_info()
            if not account_info:
                logging.critical("实盘熔断检查失败：无法读取 MT5 账户信息，禁止实盘开仓。")
                return True

            start_time, end_time = self._broker_day_range(config)
            with _mt5_lock_context():
                history_deals = mt5.history_deals_get(start_time, end_time)
            realized_loss = 0.0
            if history_deals:
                realized_loss = sum(float(getattr(deal, "profit", 0.0) or 0.0) for deal in history_deals if float(getattr(deal, "profit", 0.0) or 0.0) < 0)

            floating_loss = float(getattr(account_info, "profit", 0.0) or 0.0)
            if floating_loss > 0:
                floating_loss = 0.0
            daily_loss = realized_loss + floating_loss
            return abs(daily_loss) > float(getattr(account_info, "balance", 0.0) or 0.0) * max_drawdown
        except Exception as exc:  # noqa: BLE001
            logging.critical(f"实盘熔断检查异常，已按安全原则禁止开仓：{exc}")
            return True

    def _broker_day_range(self, config=None) -> tuple[datetime, datetime]:
        config = config or get_runtime_config()
        symbols = list(getattr(config, "symbols", []) or [])
        broker_now = datetime.now()
        if HAS_MT5 and symbols:
            broker_symbol = resolve_broker_symbol(str(symbols[0]).strip().upper()).broker
            with _mt5_lock_context():
                tick = mt5.symbol_info_tick(broker_symbol)
            tick_time = int(getattr(tick, "time", 0) or 0) if tick else 0
            if tick_time > 0:
                broker_now = datetime.fromtimestamp(tick_time)
        start_time = broker_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_time, broker_now + timedelta(days=1)

    def _count_today_ai_orders(self, config=None) -> int:
        if not HAS_MT5:
            return 0
        try:
            start_time, end_time = self._broker_day_range(config)
            with _mt5_lock_context():
                history_deals = mt5.history_deals_get(start_time, end_time)
        except Exception as exc:  # noqa: BLE001
            logging.critical(f"实盘日内订单计数失败，已按安全原则禁止开仓：{exc}")
            return 999999
        count = 0
        for deal in list(history_deals or []):
            magic = int(getattr(deal, "magic", 0) or 0)
            comment = str(getattr(deal, "comment", "") or "")
            if magic == LIVE_ORDER_MAGIC or LIVE_ORDER_COMMENT in comment:
                count += 1
        return count

    def _check_live_safety_limits(self, config=None) -> tuple[bool, str]:
        if not HAS_MT5:
            return False, "未安装 MetaTrader5 Python 库，实盘交易不可用。"
        config = config or get_runtime_config()
        max_open = max(1, int(getattr(config, "live_max_open_positions", 1) or 1))
        max_orders = max(1, int(getattr(config, "live_max_orders_per_day", 3) or 3))
        try:
            with _mt5_lock_context():
                positions = mt5.positions_get()
        except Exception as exc:  # noqa: BLE001
            return False, f"实盘持仓读取失败，禁止开仓：{exc}"
        open_count = len(list(positions or []))
        if open_count >= max_open:
            return False, f"实盘持仓上限拦截：当前持仓 {open_count} 个，配置上限 {max_open} 个。"
        today_count = self._count_today_ai_orders(config)
        if today_count >= max_orders:
            return False, f"实盘日内订单上限拦截：今日 AI 订单 {today_count} 笔，配置上限 {max_orders} 笔。"
        return True, ""

    def execute_signal(self, meta: dict, user_id: str = "system") -> Tuple[bool, str]:
        """将 AI 给出的结构化信号直接发送给 MT5 券商服务器"""
        original_meta = meta if isinstance(meta, dict) else None
        signal = StrategySignal.from_payload(meta)
        meta = signal.to_signal_meta()
        self._sync_meta_payload(original_meta, meta)
        if not HAS_MT5:
            self.last_execution_result = ExecutionResult(ok=False, message="未安装 MetaTrader5 Python 库，实盘交易不可用。", trade_mode="live")
            return False, "未安装 MetaTrader5 Python 库，实盘交易不可用。"

        config = get_runtime_config()
        safety_ok, safety_message = self._check_live_safety_limits(config)
        if not safety_ok:
            self.last_execution_result = ExecutionResult(ok=False, message=safety_message, trade_mode="live", audit_meta={"risk_decision": RiskDecision(allowed=False, reason=safety_message, block_code="safety_limits").to_dict()})
            return False, safety_message

        resolved_symbol = resolve_broker_symbol(meta.get("symbol", ""))
        symbol = resolved_symbol.internal
        broker_symbol = resolved_symbol.broker
        meta["symbol"] = symbol
        meta["broker_symbol"] = broker_symbol
        meta["broker_symbol_mapped"] = bool(resolved_symbol.is_mapped)
        self._sync_meta_payload(original_meta, meta)
        action = meta.get("action", "").lower()
        sl = float(meta.get("sl", 0.0))
        tp = float(meta.get("tp", 0.0))

        if action not in ("long", "short"):
            self.last_execution_result = ExecutionResult(ok=False, message="非明确执行信号", trade_mode="live")
            return False, "非明确执行信号"
        if sl <= 0 or tp <= 0:
            self.last_execution_result = ExecutionResult(ok=False, message="实盘禁飞区：缺失明确止损、止盈点位数据", trade_mode="live")
            return False, "实盘禁飞区：缺失明确止损、止盈点位数据"

        with _mt5_lock_context():
            symbol_info = mt5.symbol_info(broker_symbol)
        if symbol_info is None:
            self.last_execution_result = ExecutionResult(ok=False, message=f"MT5 终端找不到品种: {broker_symbol}", trade_mode="live")
            return False, f"MT5 终端找不到品种: {broker_symbol}"
        if not symbol_info.visible:
            with _mt5_lock_context():
                selected = mt5.symbol_select(broker_symbol, True)
            if not selected:
                self.last_execution_result = ExecutionResult(ok=False, message=f"MT5 依然无法监控品种: {broker_symbol}", trade_mode="live")
                return False, f"MT5 依然无法监控品种: {broker_symbol}"

        # 检查熔断！
        if self._check_daily_drawdown_circuit_breaker():
            message = "🚨 熔断拦截！今日累计亏损已达到硬性停止阈值，禁止实盘继续开仓。"
            risk_decision = RiskDecision(allowed=False, reason=message, block_code="daily_drawdown").to_dict()
            meta["risk_decision"] = risk_decision
            self._sync_meta_payload(original_meta, meta)
            self.last_execution_result = ExecutionResult(ok=False, message=message, trade_mode="live", audit_meta={"risk_decision": risk_decision})
            return False, "🚨 熔断拦截！今日累计亏损已达到硬性停止阈值，禁止实盘继续开仓。"

        account = self.get_account(user_id)
        equity = float(account["equity"])

        with _mt5_lock_context():
            tick = mt5.symbol_info_tick(broker_symbol)
        if not tick:
            self.last_execution_result = ExecutionResult(ok=False, message=f"无法获取 {broker_symbol} 实时 Tick", trade_mode="live")
            return False, f"无法获取 {broker_symbol} 实时 Tick"

        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        if action == "long" and ask <= 0:
            self.last_execution_result = ExecutionResult(ok=False, message=f"{symbol} 当前卖价 Ask 无效，禁止实盘做多。", trade_mode="live")
            return False, f"{symbol} 当前卖价 Ask 无效，禁止实盘做多。"
        if action == "short" and bid <= 0:
            self.last_execution_result = ExecutionResult(ok=False, message=f"{symbol} 当前买价 Bid 无效，禁止实盘做空。", trade_mode="live")
            return False, f"{symbol} 当前买价 Bid 无效，禁止实盘做空。"

        entry_price = ask if action == 'long' else bid
        risk_pct_used, risk_note = self._resolve_dynamic_risk_pct(meta, symbol, entry_price)
        signal = StrategySignal.from_payload({**meta, "price": entry_price})
        order_plan = self.build_order_plan(
            signal=signal,
            equity=equity,
            entry_price=entry_price,
            risk_pct=risk_pct_used,
            symbol_info=symbol_info,
            risk_note=risk_note,
        )
        meta["price"] = float(entry_price)
        meta["risk_decision"] = dict(order_plan["risk_decision"])
        meta["order_plan"] = dict(order_plan)
        self._sync_meta_payload(original_meta, meta)
        lots = float(order_plan["sizing"]["planned_lots"])
        if lots <= 0:
            self.last_execution_result = ExecutionResult(ok=False, message=f"{symbol} 计算得到的下单手数无效，禁止实盘开仓。", trade_mode="live", audit_meta=order_plan)
            return False, f"{symbol} 计算得到的下单手数无效，禁止实盘开仓。"

        # 构建原生的请求
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": lots,
            "type": mt5.ORDER_TYPE_BUY if action == 'long' else mt5.ORDER_TYPE_SELL,
            "price": ask if action == 'long' else bid,
            "sl": sl,
            "tp": tp,
            "deviation": DEFAULT_DEVIATION,
            "magic": LIVE_ORDER_MAGIC,
            "comment": LIVE_ORDER_COMMENT,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        with _mt5_lock_context():
            check_result = mt5.order_check(request)
        if check_result is None or check_result.retcode != mt5.TRADE_RETCODE_DONE:
            err = check_result.comment if check_result else mt5.last_error()
            self.last_execution_result = ExecutionResult(
                ok=False,
                message=f"MT5 发单预检失败: {err}",
                trade_mode="live",
                retcode=int(getattr(check_result, "retcode", 0) or 0) if check_result else 0,
                volume=float(lots),
                audit_meta={**order_plan, "request": dict(request), "check_comment": str(err)},
            )
            return False, f"MT5 发单预检失败: {err}"

        if bool(getattr(config, "live_order_precheck_only", True)):
            message = (
                f"实盘预检通过，但 LIVE_ORDER_PRECHECK_ONLY=1，未发送真实订单。"
                f"计划手数 {lots:.2f}，{risk_note}"
            )
            self.last_execution_result = ExecutionResult(
                ok=False,
                message=message,
                trade_mode="live",
                retcode=int(getattr(check_result, "retcode", 0) or 0),
                volume=float(lots),
                audit_meta={**order_plan, "request": dict(request), "precheck_only": True},
            )
            return (
                False,
                message,
            )

        # 真正发射订单
        with _mt5_lock_context():
            result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            self.last_execution_result = ExecutionResult(
                ok=False,
                message=f"MT5 真实开仓失败: retcode={result.retcode}, comment={result.comment}",
                trade_mode="live",
                retcode=int(getattr(result, "retcode", 0) or 0),
                filled_price=float(getattr(result, "price", 0.0) or 0.0),
                volume=float(lots),
                audit_meta={**order_plan, "request": dict(request), "send_comment": str(getattr(result, "comment", ""))},
            )
            return False, f"MT5 真实开仓失败: retcode={result.retcode}, comment={result.comment}"

        logging.info(
            f"🚀 实盘确认开仓: {action.upper()} {symbol}/{broker_symbol} "
            f"(手数: {lots:.2f}, 汇率: {result.price}, 止损: {sl}, 止盈: {tp}) - 订单号: {result.order}"
        )
        message = f"成功实盘发送 {lots:.2f} 手 {symbol}。单号：{result.order}（券商品种 {broker_symbol}，{risk_note}）"
        self.last_execution_result = ExecutionResult(
            ok=True,
            message=message,
            trade_mode="live",
            order_id=str(getattr(result, "order", "") or ""),
            retcode=int(getattr(result, "retcode", 0) or 0),
            filled_price=float(getattr(result, "price", 0.0) or 0.0),
            volume=float(lots),
            audit_meta={**order_plan, "request": dict(request)},
        )
        return True, message

    def get_open_positions(self, user_id: str = "system") -> List[dict]:
        """获取 MT5 客服端真实的在持仓位明细"""
        with _mt5_lock_context():
            positions = mt5.positions_get()
        if positions is None:
            return []

        sim_style_positions = []
        for pos in positions:
            broker_symbol = str(pos.symbol or "").strip().upper()
            internal_symbol = to_internal_symbol(broker_symbol)
            sim_style_positions.append({
                "id": pos.ticket,
                "symbol": internal_symbol,
                "broker_symbol": broker_symbol,
                "action": "long" if pos.type == mt5.ORDER_TYPE_BUY else "short",
                "entry_price": pos.price_open,
                "quantity": pos.volume,
                "floating_pnl": pos.profit,
                "stop_loss": pos.sl,
                "take_profit": pos.tp,
                "status": "open"
            })
        return sim_style_positions

# 单例全局实盘引擎实例
LIVE_ENGINE = MetalLiveTradingEngine()
