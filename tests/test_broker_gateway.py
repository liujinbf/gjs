import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from broker_gateway import (
    attach_broker_symbol_meta,
    load_broker_symbol_map,
    resolve_broker_symbol,
    to_broker_symbol,
    to_internal_symbol,
)


def test_broker_symbol_map_normalizes_direction():
    mapping = load_broker_symbol_map('{"xauusd":"gold","EURUSD":"eurusdm"}')

    assert mapping == {"XAUUSD": "GOLD", "EURUSD": "EURUSDM"}
    assert to_broker_symbol("xauusd", mapping) == "GOLD"
    assert to_internal_symbol("gold", mapping) == "XAUUSD"


def test_resolve_broker_symbol_falls_back_to_internal_symbol():
    resolved = resolve_broker_symbol("usdjpY", symbol_map={})

    assert resolved.internal == "USDJPY"
    assert resolved.broker == "USDJPY"
    assert resolved.is_mapped is False


def test_attach_broker_symbol_meta_marks_mapping():
    payload = attach_broker_symbol_meta({"price": 1.0}, "XAUUSD", {"XAUUSD": "GOLD"})

    assert payload["symbol"] == "XAUUSD"
    assert payload["broker_symbol"] == "GOLD"
    assert payload["is_mapped"] is True
