import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from signal_protocol import normalize_signal_meta as legacy_normalize_signal_meta
from signal_protocol import validate_signal_meta as legacy_validate_signal_meta
from trade_contracts import OrderIntent, RiskDecision, StrategySignal


def test_strategy_signal_normalizes_and_preserves_execution_context():
    signal = StrategySignal.from_payload(
        {
            "symbol": " xauusd ",
            "action": "LONG",
            "price": "2400.5",
            "sl": "2390.0",
            "tp": "2420.0",
            "tp2": "2440.0",
            "setup_kind": "early_momentum",
            "execution_profile": "exploratory",
            "snapshot_id": "42",
            "atr14": 18.5,
        }
    )

    assert signal.symbol == "XAUUSD"
    assert signal.action == "long"
    assert signal.strategy_family == "early_momentum"
    assert signal.execution_profile == "exploratory"
    assert signal.snapshot_id == 42
    assert signal.extra["atr14"] == 18.5
    assert signal.validate() == (True, "做多信号结构有效")

    payload = signal.to_signal_meta()
    assert payload["tp2"] == 2440.0
    assert payload["strategy_family"] == "early_momentum"
    assert payload["source_kind"] == "early_momentum"
    assert payload["setup_kind"] == "early_momentum"
    assert payload["execution_profile"] == "exploratory"
    assert payload["atr14"] == 18.5


def test_strategy_signal_keeps_legacy_signal_protocol_shape_for_neutral():
    assert legacy_normalize_signal_meta({"symbol": "xagusd", "action": "bad"}) == {
        "symbol": "XAGUSD",
        "action": "neutral",
        "price": 0.0,
        "sl": 0.0,
        "tp": 0.0,
    }
    assert legacy_validate_signal_meta({"symbol": "xagusd", "action": "neutral"}) == (True, "观望信号")


def test_order_intent_serializes_risk_decision_for_audit_chain():
    signal = StrategySignal.from_payload(
        {"symbol": "USDJPY", "action": "short", "price": 155.0, "sl": 156.0, "tp": 153.5}
    )
    decision = RiskDecision(
        allowed=True,
        reason="通过基础风控",
        risk_budget_pct=0.01,
        sizing_reference_balance=1000.0,
    )
    intent = OrderIntent(signal=signal, trade_mode="live", volume=0.02, risk_decision=decision)

    payload = intent.to_dict()
    assert payload["trade_mode"] == "live"
    assert payload["signal"]["action"] == "short"
    assert payload["risk_decision"]["allowed"] is True
    assert payload["risk_decision"]["risk_budget_pct"] == 0.01
