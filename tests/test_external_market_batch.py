from datetime import date, datetime, timedelta
from pathlib import Path

from external_market_batch import (
    batch_import_external_market_data,
    build_dukascopy_command,
    expected_daily_csv_path,
    iter_daily_windows,
)


def _write_trending_csv(path: Path, start: datetime) -> None:
    price = 2000.0
    rows = ["timestamp,open,high,low,close"]
    for index in range(90):
        ts = start + timedelta(minutes=index)
        open_price = price
        close_price = open_price + (0.9 if index < 45 else 1.6)
        high = max(open_price, close_price) + 0.2
        low = min(open_price, close_price) - 0.2
        rows.append(f"{ts:%Y-%m-%d %H:%M:%S},{open_price:.2f},{high:.2f},{low:.2f},{close_price:.2f}")
        price = close_price
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows), encoding="utf-8")


def test_iter_daily_windows_uses_exclusive_end_date():
    windows = iter_daily_windows("2026-04-20", "2026-04-23")

    assert windows == [
        (date(2026, 4, 20), date(2026, 4, 21)),
        (date(2026, 4, 21), date(2026, 4, 22)),
        (date(2026, 4, 22), date(2026, 4, 23)),
    ]


def test_build_dukascopy_command_uses_extensionless_file_name(tmp_path):
    command = build_dukascopy_command("XAUUSD", date(2026, 4, 20), date(2026, 4, 21), tmp_path)

    assert "-fn" in command
    file_name = command[command.index("-fn") + 1]
    assert file_name == "xauusd_2026-04-20_m1"
    assert not file_name.endswith(".csv")


def test_batch_import_external_market_data_uses_existing_csvs(tmp_path):
    download_dir = tmp_path / "external_data"
    db_path = tmp_path / "knowledge.db"
    first = expected_daily_csv_path(download_dir, "XAUUSD", date(2026, 4, 20))
    second = expected_daily_csv_path(download_dir, "XAUUSD", date(2026, 4, 21))
    _write_trending_csv(first, datetime(2026, 4, 20, 8, 0, 0))
    _write_trending_csv(second, datetime(2026, 4, 21, 8, 0, 0))

    report = batch_import_external_market_data(
        "2026-04-20",
        "2026-04-22",
        db_path=db_path,
        download_dir=download_dir,
        skip_download=True,
        horizon_min=8,
        lookback_bars=12,
        stride_bars=2,
        min_move_pct=0.03,
    )

    assert report["ok"] is True
    assert report["batch_count"] == 2
    assert report["inserted_snapshots"] > 0
    assert report["inserted_outcomes"] == report["inserted_snapshots"]
    assert report["train_result"]["status"] == "trained"
