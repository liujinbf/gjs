import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest_engine import extract_signal_meta


def test_extract_signal_meta_supports_new_json_payload():
    content = (
        '{"summary_text":"当前结论：轻仓试多。",'
        '"signal_meta":{"symbol":"XAUUSD","action":"long","price":2350.5,"sl":2342.0,"tp":2366.0}}'
    )
    meta = extract_signal_meta(content)
    assert meta == {
        "symbol": "XAUUSD",
        "action": "long",
        "price": 2350.5,
        "sl": 2342.0,
        "tp": 2366.0,
    }


def test_extract_signal_meta_keeps_backward_compatibility():
    content = (
        "当前结论：继续观察。\n"
        "<!-- TRACKER_META: {\"symbol\": \"EURUSD\", \"action\": \"short\", "
        "\"price\": 1.1, \"sl\": 1.11, \"tp\": 1.08} -->"
    )
    meta = extract_signal_meta(content)
    assert meta == {
        "symbol": "EURUSD",
        "action": "short",
        "price": 1.1,
        "sl": 1.11,
        "tp": 1.08,
    }
