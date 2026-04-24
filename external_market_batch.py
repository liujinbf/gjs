"""
外部历史行情批量导入器。

职责：
1. 按天调用 dukascopy-node 下载 M1/OHLC CSV；
2. 调用 external_market_samples 导入回放样本；
3. 训练本地概率模型并输出批次报告。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from external_market_samples import import_external_market_csv
from knowledge_base import KNOWLEDGE_DB_FILE
from knowledge_ml import train_probability_model


def _parse_date(value: str) -> date:
    return datetime.strptime(str(value or "").strip(), "%Y-%m-%d").date()


def iter_daily_windows(date_from: str | date, date_to: str | date) -> list[tuple[date, date]]:
    start = _parse_date(date_from) if isinstance(date_from, str) else date_from
    end = _parse_date(date_to) if isinstance(date_to, str) else date_to
    if end <= start:
        raise ValueError("date_to 必须晚于 date_from；例如 2026-04-20 到 2026-04-23 表示导入 20/21/22 三天。")
    windows: list[tuple[date, date]] = []
    current = start
    while current < end:
        next_day = current + timedelta(days=1)
        windows.append((current, next_day))
        current = next_day
    return windows


def _daily_file_stem(symbol: str, day: date, timeframe: str) -> str:
    return f"{str(symbol).strip().lower()}_{day:%Y-%m-%d}_{str(timeframe).strip().lower()}"


def _resolve_npx_executable() -> str:
    return shutil.which("npx.cmd") or shutil.which("npx") or "npx"


def expected_daily_csv_path(directory: Path | str, symbol: str, day: date, timeframe: str = "m1") -> Path:
    return Path(directory) / f"{_daily_file_stem(symbol, day, timeframe)}.csv"


def build_dukascopy_command(
    symbol: str,
    date_from: date,
    date_to: date,
    directory: Path | str,
    timeframe: str = "m1",
) -> list[str]:
    return [
        _resolve_npx_executable(),
        "--yes",
        "dukascopy-node",
        "-i",
        str(symbol).strip().lower(),
        "-from",
        f"{date_from:%Y-%m-%d}",
        "-to",
        f"{date_to:%Y-%m-%d}",
        "-t",
        str(timeframe).strip().lower(),
        "-f",
        "csv",
        "-dir",
        str(Path(directory)),
        "-fn",
        _daily_file_stem(symbol, date_from, timeframe),
        "-s",
    ]


def download_daily_csv(
    symbol: str,
    date_from: date,
    date_to: date,
    directory: Path | str,
    timeframe: str = "m1",
    force: bool = False,
    timeout_sec: int = 180,
) -> dict:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    expected_path = expected_daily_csv_path(target_dir, symbol, date_from, timeframe)
    if expected_path.exists() and not force:
        return {
            "ok": True,
            "downloaded": False,
            "csv_path": str(expected_path),
            "message": "文件已存在，跳过下载。",
        }

    command = build_dukascopy_command(symbol, date_from, date_to, target_dir, timeframe=timeframe)
    completed = subprocess.run(
        command,
        cwd=str(Path.cwd()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(30, int(timeout_sec)),
        check=False,
    )
    if completed.returncode != 0:
        return {
            "ok": False,
            "downloaded": False,
            "csv_path": str(expected_path),
            "message": (completed.stderr or completed.stdout or "dukascopy-node 下载失败").strip(),
            "returncode": int(completed.returncode),
        }

    if not expected_path.exists():
        candidates = sorted(target_dir.glob(f"{_daily_file_stem(symbol, date_from, timeframe)}*.csv"))
        if candidates:
            expected_path = candidates[-1]
    return {
        "ok": expected_path.exists(),
        "downloaded": True,
        "csv_path": str(expected_path),
        "message": (completed.stdout or "").strip(),
        "returncode": int(completed.returncode),
    }


def batch_import_external_market_data(
    date_from: str | date,
    date_to: str | date,
    symbol: str = "XAUUSD",
    db_path: Path | str | None = None,
    download_dir: Path | str = ".runtime/external_data",
    timeframe: str = "m1",
    skip_download: bool = False,
    force_download: bool = False,
    horizon_min: int = 30,
    lookback_bars: int = 60,
    stride_bars: int = 5,
    min_move_pct: float = 0.12,
    train_after_import: bool = True,
    timeout_sec: int = 180,
) -> dict:
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    target_dir = Path(download_dir)
    batches = []
    total_inserted_snapshots = 0
    total_inserted_outcomes = 0
    total_candidates = 0
    total_bars = 0
    failed_batches = 0

    for day_start, day_end in iter_daily_windows(date_from, date_to):
        if skip_download:
            csv_path = expected_daily_csv_path(target_dir, symbol, day_start, timeframe)
            download_result = {
                "ok": csv_path.exists(),
                "downloaded": False,
                "csv_path": str(csv_path),
                "message": "跳过下载，使用本地 CSV。",
            }
        else:
            download_result = download_daily_csv(
                symbol,
                day_start,
                day_end,
                target_dir,
                timeframe=timeframe,
                force=force_download,
                timeout_sec=timeout_sec,
            )

        import_result = {"ok": False, "error": ""}
        if bool(download_result.get("ok", False)) and Path(str(download_result.get("csv_path", ""))).exists():
            try:
                import_result = import_external_market_csv(
                    str(download_result["csv_path"]),
                    db_path=target_db,
                    symbol=symbol,
                    horizon_min=horizon_min,
                    lookback_bars=lookback_bars,
                    stride_bars=stride_bars,
                    min_move_pct=min_move_pct,
                )
            except Exception as exc:  # noqa: BLE001
                import_result = {"ok": False, "error": str(exc)}
        else:
            failed_batches += 1

        if bool(import_result.get("ok", False)):
            total_inserted_snapshots += int(import_result.get("inserted_snapshots", 0) or 0)
            total_inserted_outcomes += int(import_result.get("inserted_outcomes", 0) or 0)
            total_candidates += int(import_result.get("candidate_count", 0) or 0)
            total_bars += int(import_result.get("bar_count", 0) or 0)
        elif not bool(download_result.get("ok", False)):
            failed_batches += 0
        else:
            failed_batches += 1

        batches.append(
            {
                "date_from": f"{day_start:%Y-%m-%d}",
                "date_to": f"{day_end:%Y-%m-%d}",
                "download": download_result,
                "import": import_result,
            }
        )

    train_result = None
    if train_after_import:
        train_result = train_probability_model(db_path=target_db, horizon_min=horizon_min, min_train_samples=20)

    return {
        "ok": failed_batches == 0,
        "symbol": str(symbol).strip().upper(),
        "date_from": f"{(_parse_date(date_from) if isinstance(date_from, str) else date_from):%Y-%m-%d}",
        "date_to": f"{(_parse_date(date_to) if isinstance(date_to, str) else date_to):%Y-%m-%d}",
        "db_path": str(target_db),
        "download_dir": str(target_dir),
        "timeframe": str(timeframe).strip().lower(),
        "batch_count": len(batches),
        "failed_batches": failed_batches,
        "bar_count": total_bars,
        "candidate_count": total_candidates,
        "inserted_snapshots": total_inserted_snapshots,
        "inserted_outcomes": total_inserted_outcomes,
        "train_result": train_result,
        "batches": batches,
    }


def write_batch_report(report: dict, output_path: Path | str) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量下载并导入外部 M1 历史行情回放样本。")
    parser.add_argument("--date-from", required=True, help="开始日期，包含，例如 2026-04-20")
    parser.add_argument("--date-to", required=True, help="结束日期，不包含，例如 2026-04-23 表示导入 20/21/22")
    parser.add_argument("--symbol", default="XAUUSD", help="品种代码，默认 XAUUSD")
    parser.add_argument("--db-path", default=str(KNOWLEDGE_DB_FILE), help="知识库 DB 路径")
    parser.add_argument("--download-dir", default=".runtime/external_data", help="CSV 下载目录")
    parser.add_argument("--timeframe", default="m1", help="下载周期，默认 m1")
    parser.add_argument("--skip-download", action="store_true", help="跳过下载，仅导入本地已有 CSV")
    parser.add_argument("--force-download", action="store_true", help="即使文件已存在也重新下载")
    parser.add_argument("--horizon-min", type=int, default=30, help="结果评估窗口，默认 30 分钟")
    parser.add_argument("--lookback-bars", type=int, default=60, help="候选识别回看 K 线数，默认 60")
    parser.add_argument("--stride-bars", type=int, default=5, help="采样步长，默认每 5 根")
    parser.add_argument("--min-move-pct", type=float, default=0.12, help="动能候选最小涨跌幅百分比")
    parser.add_argument("--no-train", action="store_true", help="导入后不训练本地概率模型")
    parser.add_argument("--timeout-sec", type=int, default=180, help="单日下载超时秒数")
    parser.add_argument("--output", default="", help="可选 JSON 报告输出路径")
    args = parser.parse_args(argv)

    report = batch_import_external_market_data(
        date_from=args.date_from,
        date_to=args.date_to,
        symbol=args.symbol,
        db_path=args.db_path,
        download_dir=args.download_dir,
        timeframe=args.timeframe,
        skip_download=bool(args.skip_download),
        force_download=bool(args.force_download),
        horizon_min=args.horizon_min,
        lookback_bars=args.lookback_bars,
        stride_bars=args.stride_bars,
        min_move_pct=args.min_move_pct,
        train_after_import=not bool(args.no_train),
        timeout_sec=args.timeout_sec,
    )
    if args.output:
        write_batch_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if bool(report.get("ok", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
