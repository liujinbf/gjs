import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mt5_live_engine
from mt5_live_engine import MetalLiveTradingEngine


def _live_config(**overrides):
    payload = {
        "symbols": ["XAUUSD"],
        "live_max_drawdown_pct": 0.05,
        "live_order_precheck_only": False,
        "live_max_open_positions": 3,
        "live_max_orders_per_day": 5,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


class _FakeMT5:
    def __init__(self, account_profit=-20.0, deal_profits=None):
        self._account_profit = account_profit
        self._deal_profits = list(deal_profits if deal_profits is not None else [-40.0, 12.0])
        self.captured_history_range = None

    def account_info(self):
        return SimpleNamespace(balance=1000.0, equity=980.0, margin=0.0, profit=self._account_profit)

    def symbol_info_tick(self, _symbol):
        return SimpleNamespace(time=1_777_684_800)

    def history_deals_get(self, start_time, end_time):
        self.captured_history_range = (start_time, end_time)
        return [SimpleNamespace(profit=value) for value in self._deal_profits]


def test_live_drawdown_circuit_breaker_uses_datetime_range(monkeypatch):
    fake_mt5 = _FakeMT5(account_profit=-20.0, deal_profits=[-40.0, 15.0])
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)
    monkeypatch.setattr(
        mt5_live_engine,
        "get_runtime_config",
        lambda: _live_config(live_max_drawdown_pct=0.05),
    )

    assert MetalLiveTradingEngine()._check_daily_drawdown_circuit_breaker() is True

    start_time, end_time = fake_mt5.captured_history_range
    assert start_time.hour == 0
    assert start_time.minute == 0
    assert end_time > start_time


def test_live_drawdown_circuit_breaker_allows_below_limit(monkeypatch):
    fake_mt5 = _FakeMT5(account_profit=-5.0, deal_profits=[-10.0, 20.0])
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)
    monkeypatch.setattr(
        mt5_live_engine,
        "get_runtime_config",
        lambda: _live_config(live_max_drawdown_pct=0.05),
    )

    assert MetalLiveTradingEngine()._check_daily_drawdown_circuit_breaker() is False


def test_live_engine_fails_closed_when_mt5_module_missing(monkeypatch):
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", False)
    monkeypatch.setattr(mt5_live_engine, "mt5", None)

    ok, message = MetalLiveTradingEngine().execute_signal(
        {"symbol": "XAUUSD", "action": "long", "sl": 4800.0, "tp": 4880.0}
    )

    assert ok is False
    assert "MetaTrader5" in message


class _FakeTradeMT5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(
        self,
        *,
        bid=4850.2,
        ask=4850.8,
        visible=True,
        volume_step=0.01,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        positions=None,
        history_deals=None,
    ):
        self._bid = bid
        self._ask = ask
        self._visible = visible
        self._volume_step = volume_step
        self._trade_tick_size = trade_tick_size
        self._trade_tick_value = trade_tick_value
        self._positions = list(positions or [])
        self._history_deals = list(history_deals or [])
        self.order_check_request = None
        self.order_send_request = None
        self.symbol_info_calls = []
        self.tick_calls = []
        self.select_calls = []

    def symbol_info(self, _symbol):
        self.symbol_info_calls.append(_symbol)
        return SimpleNamespace(
            visible=self._visible,
            trade_contract_size=100.0,
            trade_tick_size=self._trade_tick_size,
            trade_tick_value=self._trade_tick_value,
            volume_step=self._volume_step,
            volume_min=0.01,
            volume_max=5.0,
        )

    def symbol_select(self, _symbol, _enabled):
        self.select_calls.append(_symbol)
        return True

    def account_info(self):
        return SimpleNamespace(balance=1000.0, equity=1000.0, margin=0.0, profit=0.0)

    def symbol_info_tick(self, _symbol):
        self.tick_calls.append(_symbol)
        return SimpleNamespace(time=1_777_684_800, bid=self._bid, ask=self._ask)

    def positions_get(self):
        return list(self._positions)

    def history_deals_get(self, _start_time, _end_time):
        return list(self._history_deals)

    def order_check(self, request):
        self.order_check_request = dict(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="ok")

    def order_send(self, request):
        self.order_send_request = dict(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="ok", price=request["price"], order=123456)


def test_live_engine_uses_ask_and_buy_type_for_long(monkeypatch):
    fake_mt5 = _FakeTradeMT5(bid=4850.2, ask=4850.8)
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_live_engine, "get_runtime_config", lambda: _live_config())
    monkeypatch.setattr(MetalLiveTradingEngine, "_check_daily_drawdown_circuit_breaker", lambda self: False)
    monkeypatch.setattr(MetalLiveTradingEngine, "_resolve_dynamic_risk_pct", lambda self, meta, symbol, entry: (0.02, "test-risk"))

    meta = {"symbol": "XAUUSD", "action": "long", "sl": 4840.0, "tp": 4870.0}
    engine = MetalLiveTradingEngine()
    ok, message = engine.execute_signal(meta)

    assert ok is True
    assert "成功实盘发送" in message
    assert fake_mt5.order_check_request["type"] == fake_mt5.ORDER_TYPE_BUY
    assert fake_mt5.order_check_request["price"] == 4850.8
    assert meta["price"] == 4850.8
    assert meta["risk_decision"]["allowed"] is True
    assert meta["order_plan"]["sizing"]["sizing_method"] == "broker_tick_value"
    assert engine.last_execution_result.ok is True
    assert engine.last_execution_result.order_id == "123456"


def test_live_engine_uses_bid_and_sell_type_for_short(monkeypatch):
    fake_mt5 = _FakeTradeMT5(bid=4849.6, ask=4850.1)
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_live_engine, "get_runtime_config", lambda: _live_config())
    monkeypatch.setattr(MetalLiveTradingEngine, "_check_daily_drawdown_circuit_breaker", lambda self: False)
    monkeypatch.setattr(MetalLiveTradingEngine, "_resolve_dynamic_risk_pct", lambda self, meta, symbol, entry: (0.02, "test-risk"))

    ok, message = MetalLiveTradingEngine().execute_signal(
        {"symbol": "XAUUSD", "action": "short", "sl": 4860.0, "tp": 4830.0}
    )

    assert ok is True
    assert "成功实盘发送" in message
    assert fake_mt5.order_check_request["type"] == fake_mt5.ORDER_TYPE_SELL
    assert fake_mt5.order_check_request["price"] == 4849.6


def test_live_engine_blocks_invalid_short_bid(monkeypatch):
    fake_mt5 = _FakeTradeMT5(bid=0.0, ask=4850.1)
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_live_engine, "get_runtime_config", lambda: _live_config())
    monkeypatch.setattr(MetalLiveTradingEngine, "_check_daily_drawdown_circuit_breaker", lambda self: False)

    ok, message = MetalLiveTradingEngine().execute_signal(
        {"symbol": "XAUUSD", "action": "short", "sl": 4860.0, "tp": 4830.0}
    )

    assert ok is False
    assert "Bid 无效" in message


def test_live_engine_precheck_mode_does_not_send_order(monkeypatch):
    fake_mt5 = _FakeTradeMT5(bid=4850.2, ask=4850.8)
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_live_engine, "get_runtime_config", lambda: _live_config(live_order_precheck_only=True))
    monkeypatch.setattr(MetalLiveTradingEngine, "_check_daily_drawdown_circuit_breaker", lambda self: False)
    monkeypatch.setattr(MetalLiveTradingEngine, "_resolve_dynamic_risk_pct", lambda self, meta, symbol, entry: (0.02, "test-risk"))

    meta = {"symbol": "XAUUSD", "action": "long", "sl": 4840.0, "tp": 4870.0}
    engine = MetalLiveTradingEngine()
    ok, message = engine.execute_signal(meta)

    assert ok is False
    assert "实盘预检通过" in message
    assert fake_mt5.order_check_request is not None
    assert fake_mt5.order_send_request is None
    assert meta["order_plan"]["sizing"]["planned_lots"] > 0
    assert engine.last_execution_result.audit_meta["precheck_only"] is True


def test_live_engine_blocks_when_open_position_limit_reached(monkeypatch):
    fake_mt5 = _FakeTradeMT5(
        positions=[SimpleNamespace(ticket=1, symbol="XAUUSD", type=0, price_open=4850.0, volume=0.01, profit=0.0, sl=4840.0, tp=4870.0)]
    )
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_live_engine, "get_runtime_config", lambda: _live_config(live_max_open_positions=1))

    ok, message = MetalLiveTradingEngine().execute_signal(
        {"symbol": "XAUUSD", "action": "long", "sl": 4840.0, "tp": 4870.0}
    )

    assert ok is False
    assert "实盘持仓上限拦截" in message
    assert fake_mt5.order_check_request is None
    assert fake_mt5.order_send_request is None


def test_live_engine_blocks_when_daily_order_limit_reached(monkeypatch):
    fake_mt5 = _FakeTradeMT5(
        history_deals=[
            SimpleNamespace(magic=mt5_live_engine.LIVE_ORDER_MAGIC, comment="AI-LIVE-ORDER", profit=1.0),
            SimpleNamespace(magic=mt5_live_engine.LIVE_ORDER_MAGIC, comment="AI-LIVE-ORDER", profit=-1.0),
        ]
    )
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_live_engine, "get_runtime_config", lambda: _live_config(live_max_orders_per_day=2))

    ok, message = MetalLiveTradingEngine().execute_signal(
        {"symbol": "XAUUSD", "action": "long", "sl": 4840.0, "tp": 4870.0}
    )

    assert ok is False
    assert "实盘日内订单上限拦截" in message
    assert fake_mt5.order_check_request is None
    assert fake_mt5.order_send_request is None


def test_live_lot_calculation_falls_back_when_volume_step_invalid(monkeypatch):
    fake_mt5 = _FakeTradeMT5(volume_step=0.0)
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)

    lots = MetalLiveTradingEngine().calculate_optimal_lots(
        equity=1000.0,
        entry_price=4850.0,
        stop_loss=4840.0,
        symbol="XAUUSD",
        risk_pct=0.02,
    )

    assert lots >= 0.01


def test_live_lot_calculation_prefers_broker_tick_value(monkeypatch):
    fake_mt5 = _FakeTradeMT5(trade_tick_size=0.1, trade_tick_value=5.0, volume_step=0.01)
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)

    lots = MetalLiveTradingEngine().calculate_optimal_lots(
        equity=1000.0,
        entry_price=4850.0,
        stop_loss=4840.0,
        symbol="XAUUSD",
        risk_pct=0.02,
    )

    assert lots == 0.04


def test_live_order_plan_explains_fallback_sizing_method():
    engine = MetalLiveTradingEngine()
    signal = mt5_live_engine.StrategySignal.from_payload(
        {"symbol": "USDJPY", "action": "short", "price": 155.0, "sl": 156.0, "tp": 153.0}
    )
    symbol_info = SimpleNamespace(
        trade_contract_size=100000.0,
        trade_tick_size=0.0,
        trade_tick_value=0.0,
        volume_step=0.01,
        volume_min=0.01,
        volume_max=2.0,
    )

    plan = engine.build_order_plan(
        signal=signal,
        equity=1000.0,
        entry_price=155.0,
        risk_pct=0.02,
        symbol_info=symbol_info,
        risk_note="test-risk",
    )

    assert plan["sizing"]["sizing_method"] == "contract_size_jpy_to_usd"
    assert plan["risk_decision"]["risk_budget_pct"] == 0.02
    assert plan["order_intent"]["trade_mode"] == "live"


def test_live_engine_uses_broker_symbol_mapping(monkeypatch):
    fake_mt5 = _FakeTradeMT5(bid=4850.2, ask=4850.8)
    monkeypatch.setenv("BROKER_SYMBOL_MAP_JSON", '{"XAUUSD":"GOLD"}')
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_live_engine, "get_runtime_config", lambda: _live_config())
    monkeypatch.setattr(MetalLiveTradingEngine, "_check_daily_drawdown_circuit_breaker", lambda self: False)
    monkeypatch.setattr(MetalLiveTradingEngine, "_resolve_dynamic_risk_pct", lambda self, meta, symbol, entry: (0.02, "test-risk"))

    meta = {"symbol": "XAUUSD", "action": "long", "sl": 4840.0, "tp": 4870.0}
    ok, message = MetalLiveTradingEngine().execute_signal(meta)

    assert ok is True
    assert "券商品种 GOLD" in message
    assert fake_mt5.order_check_request["symbol"] == "GOLD"
    assert fake_mt5.symbol_info_calls[-1] == "GOLD"
    assert fake_mt5.tick_calls[-1] == "GOLD"
    assert meta["symbol"] == "XAUUSD"
    assert meta["broker_symbol"] == "GOLD"
    assert meta["broker_symbol_mapped"] is True


def test_live_open_positions_map_broker_symbol_to_internal(monkeypatch):
    fake_mt5 = _FakeTradeMT5(
        positions=[
            SimpleNamespace(ticket=1, symbol="GOLD", type=0, price_open=4850.0, volume=0.01, profit=1.2, sl=4840.0, tp=4870.0)
        ]
    )
    monkeypatch.setenv("BROKER_SYMBOL_MAP_JSON", '{"XAUUSD":"GOLD"}')
    monkeypatch.setattr(mt5_live_engine, "HAS_MT5", True)
    monkeypatch.setattr(mt5_live_engine, "mt5", fake_mt5)

    positions = MetalLiveTradingEngine().get_open_positions()

    assert positions[0]["symbol"] == "XAUUSD"
    assert positions[0]["broker_symbol"] == "GOLD"
