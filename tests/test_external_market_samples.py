import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from external_market_samples import build_replay_samples, import_external_market_csv, load_ohlc_csv
from knowledge_ml import train_probability_model


def _write_trending_csv(path: Path) -> None:
    start = datetime(2026, 1, 5, 9, 0, 0)
    price = 2000.0
    rows = ["timestamp,open,high,low,close,volume"]
    for index in range(90):
        ts = start + timedelta(minutes=index)
        open_price = price
        if index < 35:
            close_price = open_price + 0.9
        elif index < 50:
            close_price = open_price + 1.6
        else:
            close_price = open_price + 2.4
        high = max(open_price, close_price) + 0.3
        low = min(open_price, close_price) - 0.2
        rows.append(f"{ts:%Y-%m-%d %H:%M:%S},{open_price:.2f},{high:.2f},{low:.2f},{close_price:.2f},10")
        price = close_price
    path.write_text("\n".join(rows), encoding="utf-8")


def test_load_ohlc_csv_accepts_header_format(tmp_path):
    csv_path = tmp_path / "xauusd.csv"
    _write_trending_csv(csv_path)

    bars = load_ohlc_csv(csv_path)

    assert len(bars) == 90
    assert bars[0].open == 2000.0
    assert bars[-1].close > bars[0].close


def test_load_ohlc_csv_accepts_epoch_milliseconds(tmp_path):
    csv_path = tmp_path / "epoch.csv"
    csv_path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close",
                "1776643200000,4754.995,4754.995,4744.295,4748.345",
            ]
        ),
        encoding="utf-8",
    )

    bars = load_ohlc_csv(csv_path)

    assert len(bars) == 1
    assert bars[0].timestamp.year == 2026
    assert bars[0].close == 4748.345


def test_build_replay_samples_labels_directional_candidates(tmp_path):
    csv_path = tmp_path / "xauusd.csv"
    _write_trending_csv(csv_path)
    bars = load_ohlc_csv(csv_path)

    samples = build_replay_samples(
        bars,
        lookback_bars=12,
        horizon_min=8,
        stride_bars=2,
        min_move_pct=0.03,
    )

    assert samples
    assert {sample.side for sample in samples} == {"long"}
    assert any(sample.outcome_label == "success" for sample in samples)


def test_import_external_market_csv_feeds_learning_tables(tmp_path):
    csv_path = tmp_path / "xauusd.csv"
    db_path = tmp_path / "knowledge.db"
    _write_trending_csv(csv_path)

    result = import_external_market_csv(
        csv_path,
        db_path=db_path,
        lookback_bars=12,
        horizon_min=8,
        stride_bars=2,
        min_move_pct=0.03,
    )

    assert result["inserted_snapshots"] > 0
    assert result["inserted_outcomes"] == result["inserted_snapshots"]

    with sqlite3.connect(str(db_path)) as conn:
        snapshot_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
        outcome_count = conn.execute("SELECT COUNT(*) FROM snapshot_outcomes").fetchone()[0]
        replay_count = conn.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE regime_tag='external_replay_momentum'"
        ).fetchone()[0]
    assert snapshot_count == result["inserted_snapshots"]
    assert outcome_count == result["inserted_outcomes"]
    assert replay_count == result["inserted_snapshots"]

    ml_result = train_probability_model(db_path=db_path, horizon_min=8, min_train_samples=4)
    assert ml_result["status"] == "trained"
    assert ml_result["sample_count"] >= result["inserted_outcomes"]
