import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import multi_symbol_correlation as corr


def _write_ratio_history(path: Path, values: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps({"ts": f"2026-06-02 10:{index:02d}:00", "ratio": value}) for index, value in enumerate(values)),
        encoding="utf-8",
    )


def test_analyze_au_ag_ratio_uses_zscore_high_pair_signal(tmp_path, monkeypatch):
    history_file = tmp_path / "au_ag_ratio_history.jsonl"
    _write_ratio_history(history_file, [80.0, 80.2, 79.8, 80.1, 79.9] * 10)
    monkeypatch.setattr(corr, "_AU_AG_HISTORY_FILE", history_file)

    payload = corr.analyze_au_ag_ratio(
        [
            {"symbol": "XAUUSD", "latest_price": 4476.0},
            {"symbol": "XAGUSD", "latest_price": 52.0},
        ]
    )

    assert payload["au_ag_zscore_ready"] is True
    assert payload["au_ag_zscore"] >= 2.0
    assert payload["au_ag_signal"] == "long_xag_short_xau"
    assert payload["au_ag_ratio_momentum_state"] == "expanding_up"
    assert payload["au_ag_pair_legs"] == [
        {"symbol": "XAUUSD", "action": "short", "role": "rich_leg"},
        {"symbol": "XAGUSD", "action": "long", "role": "cheap_leg"},
    ]


def test_analyze_au_ag_ratio_uses_zscore_low_pair_signal(tmp_path, monkeypatch):
    history_file = tmp_path / "au_ag_ratio_history.jsonl"
    _write_ratio_history(history_file, [80.0, 80.2, 79.8, 80.1, 79.9] * 10)
    monkeypatch.setattr(corr, "_AU_AG_HISTORY_FILE", history_file)

    payload = corr.analyze_au_ag_ratio(
        [
            {"symbol": "XAUUSD", "latest_price": 3600.0},
            {"symbol": "XAGUSD", "latest_price": 50.0},
        ]
    )

    assert payload["au_ag_zscore_ready"] is True
    assert payload["au_ag_zscore"] <= -2.0
    assert payload["au_ag_signal"] == "long_xau_short_xag"
    assert payload["au_ag_ratio_momentum_state"] == "expanding_down"
    assert payload["au_ag_pair_legs"] == [
        {"symbol": "XAUUSD", "action": "long", "role": "cheap_leg"},
        {"symbol": "XAGUSD", "action": "short", "role": "rich_leg"},
    ]


def test_analyze_au_ag_ratio_marks_zscore_not_ready_when_history_is_short(tmp_path, monkeypatch):
    history_file = tmp_path / "au_ag_ratio_history.jsonl"
    _write_ratio_history(history_file, [80.0, 80.2, 79.8])
    monkeypatch.setattr(corr, "_AU_AG_HISTORY_FILE", history_file)

    payload = corr.analyze_au_ag_ratio(
        [
            {"symbol": "XAUUSD", "latest_price": 4476.0},
            {"symbol": "XAGUSD", "latest_price": 52.0},
        ]
    )

    assert payload["au_ag_zscore_ready"] is False
    assert payload["au_ag_ratio_sample_count"] == 3
