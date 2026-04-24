"""
外部历史行情样本导入器。

目标：把公开 M1/OHLC 历史行情转成可验证的回放样本，补充本地学习系统的冷启动样本池。
注意：这里导入的是行情回放样本，不是他人真实账户成交单。
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection, upsert_source
from signal_enums import AlertTone, SignalSide, TradeGrade


@dataclass(frozen=True)
class OhlcBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class ReplaySample:
    symbol: str
    timestamp: datetime
    entry: float
    stop_loss: float
    take_profit: float
    side: str
    spread_points: float
    atr14: float
    lookback_change_pct: float
    lookback_range_pct: float
    location_ratio: float
    horizon_min: int
    future_timestamp: datetime
    future_price: float
    max_price: float
    min_price: float
    price_change_pct: float
    mfe_pct: float
    mae_pct: float
    outcome_label: str
    signal_quality: str


def _parse_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value or "").strip().replace(",", ""))
    except (TypeError, ValueError):
        return default


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        try:
            raw_value = int(text)
            if raw_value > 10_000_000_000:
                return datetime.fromtimestamp(raw_value / 1000.0)
            return datetime.fromtimestamp(raw_value)
        except (OSError, OverflowError, ValueError):
            return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y%m%d %H%M%S",
        "%Y%m%d%H%M%S",
        "%Y%m%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _sniff_dialect(text: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _has_header(first_row: list[str]) -> bool:
    joined = " ".join(str(item or "").lower() for item in first_row)
    return any(token in joined for token in ("time", "date", "open", "high", "low", "close"))


def _field(row: dict[str, str], names: Iterable[str]) -> str:
    lowered = {str(key or "").strip().lower(): value for key, value in row.items()}
    for name in names:
        key = str(name or "").strip().lower()
        if key in lowered:
            return lowered[key]
    return ""


def load_ohlc_csv(path: Path | str) -> list[OhlcBar]:
    target = Path(path)
    text = target.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        return []

    dialect = _sniff_dialect(text)
    raw_rows = list(csv.reader(text.splitlines(), dialect))
    raw_rows = [row for row in raw_rows if row and any(str(cell or "").strip() for cell in row)]
    if not raw_rows:
        return []

    bars: list[OhlcBar] = []
    if _has_header(raw_rows[0]):
        reader = csv.DictReader(text.splitlines(), dialect=dialect)
        for row in reader:
            timestamp = _parse_timestamp(
                _field(row, ("timestamp", "time", "date", "datetime", "local time", "gmt time"))
            )
            if timestamp is None:
                continue
            bar = OhlcBar(
                timestamp=timestamp,
                open=_parse_float(_field(row, ("open", "bidopen", "askopen"))),
                high=_parse_float(_field(row, ("high", "bidhigh", "askhigh"))),
                low=_parse_float(_field(row, ("low", "bidlow", "asklow"))),
                close=_parse_float(_field(row, ("close", "bidclose", "askclose"))),
                volume=_parse_float(_field(row, ("volume", "vol", "tick_volume"))),
            )
            if min(bar.open, bar.high, bar.low, bar.close) > 0:
                bars.append(bar)
    else:
        for row in raw_rows:
            if len(row) < 5:
                continue
            timestamp = _parse_timestamp(row[0] if len(row) == 5 else f"{row[0]} {row[1]}")
            offset = 1 if len(row) == 5 else 2
            if timestamp is None:
                continue
            bar = OhlcBar(
                timestamp=timestamp,
                open=_parse_float(row[offset]),
                high=_parse_float(row[offset + 1]),
                low=_parse_float(row[offset + 2]),
                close=_parse_float(row[offset + 3]),
                volume=_parse_float(row[offset + 4] if len(row) > offset + 4 else 0.0),
            )
            if min(bar.open, bar.high, bar.low, bar.close) > 0:
                bars.append(bar)

    deduped = {bar.timestamp: bar for bar in bars}
    return sorted(deduped.values(), key=lambda item: item.timestamp)


def _true_range(current: OhlcBar, previous: OhlcBar | None) -> float:
    if previous is None:
        return max(current.high - current.low, 0.0)
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
        0.0,
    )


def _atr14(bars: list[OhlcBar], end_index: int) -> float:
    start = max(1, end_index - 13)
    ranges = [_true_range(bars[index], bars[index - 1]) for index in range(start, end_index + 1)]
    return sum(ranges) / len(ranges) if ranges else 0.0


def _directional_metrics(entry: float, future_price: float, max_price: float, min_price: float, side: str) -> dict:
    if entry <= 0:
        return {"price_change_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0}
    if side == SignalSide.SHORT.value:
        return {
            "price_change_pct": (entry - future_price) / entry * 100.0,
            "mfe_pct": max((entry - min_price) / entry * 100.0, 0.0),
            "mae_pct": max((max_price - entry) / entry * 100.0, 0.0),
        }
    return {
        "price_change_pct": (future_price - entry) / entry * 100.0,
        "mfe_pct": max((max_price - entry) / entry * 100.0, 0.0),
        "mae_pct": max((entry - min_price) / entry * 100.0, 0.0),
    }


def _label_path(entry: float, stop_loss: float, take_profit: float, side: str, future_bars: list[OhlcBar]) -> tuple[str, str]:
    for bar in future_bars:
        if side == SignalSide.SHORT.value:
            sl_hit = bar.high >= stop_loss
            tp_hit = bar.low <= take_profit
        else:
            sl_hit = bar.low <= stop_loss
            tp_hit = bar.high >= take_profit
        if sl_hit and tp_hit:
            return "fail", "low"
        if sl_hit:
            return "fail", "low"
        if tp_hit:
            return "success", "high"
    max_price = max(bar.high for bar in future_bars)
    min_price = min(bar.low for bar in future_bars)
    metrics = _directional_metrics(entry, future_bars[-1].close, max_price, min_price, side)
    risk_pct = abs(entry - stop_loss) / entry * 100.0 if entry > 0 else 0.0
    if metrics["mfe_pct"] >= risk_pct * 0.9 and metrics["mae_pct"] <= risk_pct * 0.8:
        return "mixed", "medium"
    return "fail", "low"


def build_replay_samples(
    bars: list[OhlcBar],
    symbol: str = "XAUUSD",
    horizon_min: int = 30,
    lookback_bars: int = 60,
    stride_bars: int = 5,
    min_move_pct: float = 0.12,
    rr_ratio: float = 2.0,
    stop_atr_multiplier: float = 1.2,
    spread_points: float = 17.0,
) -> list[ReplaySample]:
    ordered = sorted(bars, key=lambda item: item.timestamp)
    if len(ordered) <= lookback_bars + horizon_min:
        return []

    samples: list[ReplaySample] = []
    safe_stride = max(1, int(stride_bars))
    safe_horizon = max(1, int(horizon_min))
    for index in range(max(lookback_bars, 14), len(ordered) - safe_horizon, safe_stride):
        lookback = ordered[index - lookback_bars + 1 : index + 1]
        current = ordered[index]
        future = ordered[index + 1 : index + safe_horizon + 1]
        if len(future) < safe_horizon:
            continue

        first_open = lookback[0].open
        highest = max(bar.high for bar in lookback)
        lowest = min(bar.low for bar in lookback)
        if first_open <= 0 or highest <= lowest:
            continue

        change_pct = (current.close - first_open) / first_open * 100.0
        range_pct = (highest - lowest) / current.close * 100.0 if current.close > 0 else 0.0
        location_ratio = (current.close - lowest) / (highest - lowest)
        side = ""
        if change_pct >= min_move_pct and location_ratio >= 0.58:
            side = SignalSide.LONG.value
        elif change_pct <= -min_move_pct and location_ratio <= 0.42:
            side = SignalSide.SHORT.value
        if side not in {SignalSide.LONG.value, SignalSide.SHORT.value}:
            continue

        atr = _atr14(ordered, index)
        min_stop = current.close * (max(min_move_pct, 0.02) / 100.0) * 0.6
        stop_distance = max(atr * max(0.2, stop_atr_multiplier), min_stop)
        if stop_distance <= 0:
            continue

        if side == SignalSide.SHORT.value:
            stop_loss = current.close + stop_distance
            take_profit = current.close - stop_distance * rr_ratio
        else:
            stop_loss = current.close - stop_distance
            take_profit = current.close + stop_distance * rr_ratio
        if min(stop_loss, take_profit) <= 0:
            continue

        future_price = future[-1].close
        max_price = max(bar.high for bar in future)
        min_price = min(bar.low for bar in future)
        metrics = _directional_metrics(current.close, future_price, max_price, min_price, side)
        outcome_label, signal_quality = _label_path(current.close, stop_loss, take_profit, side, future)
        samples.append(
            ReplaySample(
                symbol=str(symbol or "XAUUSD").strip().upper(),
                timestamp=current.timestamp,
                entry=current.close,
                stop_loss=stop_loss,
                take_profit=take_profit,
                side=side,
                spread_points=float(spread_points),
                atr14=atr,
                lookback_change_pct=change_pct,
                lookback_range_pct=range_pct,
                location_ratio=location_ratio,
                horizon_min=safe_horizon,
                future_timestamp=future[-1].timestamp,
                future_price=future_price,
                max_price=max_price,
                min_price=min_price,
                price_change_pct=float(metrics["price_change_pct"]),
                mfe_pct=float(metrics["mfe_pct"]),
                mae_pct=float(metrics["mae_pct"]),
                outcome_label=outcome_label,
                signal_quality=signal_quality,
            )
        )
    return samples


def _sample_feature_json(sample: ReplaySample, source_title: str) -> str:
    side_text = "偏多" if sample.side == SignalSide.LONG.value else "偏空"
    breakout_text = "动能上破" if sample.side == SignalSide.LONG.value else "动能下破"
    return json.dumps(
        {
            "sample_source": "external_market_replay",
            "sample_source_title": source_title,
            "atr14": sample.atr14,
            "atr14_h4": 0.0,
            "risk_reward_state_text": "盈亏比优秀",
            "risk_reward_ratio": abs(sample.take_profit - sample.entry) / abs(sample.entry - sample.stop_loss),
            "risk_reward_ready": True,
            "risk_reward_direction": "bullish" if sample.side == SignalSide.LONG.value else "bearish",
            "risk_reward_stop_price": sample.stop_loss,
            "risk_reward_target_price": sample.take_profit,
            "intraday_bias_text": side_text,
            "multi_timeframe_bias_text": side_text,
            "multi_timeframe_alignment_text": "外部回放动能同向",
            "key_level_state_text": "动能区间突破",
            "breakout_state_text": breakout_text,
            "retest_state_text": "回放样本未要求回踩",
            "lookback_change_pct": sample.lookback_change_pct,
            "lookback_range_pct": sample.lookback_range_pct,
            "location_ratio": sample.location_ratio,
        },
        ensure_ascii=False,
    )


def import_external_market_csv(
    csv_path: Path | str,
    db_path: Path | str | None = None,
    symbol: str = "XAUUSD",
    horizon_min: int = 30,
    lookback_bars: int = 60,
    stride_bars: int = 5,
    min_move_pct: float = 0.12,
    source_title: str | None = None,
) -> dict:
    bars = load_ohlc_csv(csv_path)
    samples = build_replay_samples(
        bars,
        symbol=symbol,
        horizon_min=horizon_min,
        lookback_bars=lookback_bars,
        stride_bars=stride_bars,
        min_move_pct=min_move_pct,
    )
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    title = source_title or f"外部历史行情回放 {str(symbol).upper()} {Path(csv_path).name}"
    upsert_source(
        title=title,
        source_type="external_market_replay",
        location=str(Path(csv_path).resolve()),
        author="public-market-data",
        trust_level="external_replay",
        tags=["external", "market_replay", str(symbol).upper()],
        notes="公开历史行情回放样本；用于扩充冷启动学习集，不等同于他人真实账户成交。",
        db_path=target_db,
    )

    inserted_snapshots = 0
    inserted_outcomes = 0
    skipped_existing = 0
    with open_knowledge_connection(target_db, ensure_schema=True) as conn:
        for sample in samples:
            snapshot_time = sample.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            feature_json = _sample_feature_json(sample, title)
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO market_snapshots (
                        snapshot_time, symbol, latest_price, spread_points, has_live_quote, tone,
                        trade_grade, trade_grade_source, alert_state_text, event_risk_mode_text,
                        event_active_name, event_importance_text, event_note, signal_side,
                        regime_tag, regime_text, feature_json, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_time,
                        sample.symbol,
                        sample.entry,
                        sample.spread_points,
                        AlertTone.SUCCESS.value,
                        TradeGrade.LIGHT_POSITION.value,
                        "structure",
                        "外部回放候选",
                        "外部历史行情回放",
                        "公开历史行情回放样本，不等同于真实账户成交。",
                        sample.side,
                        "external_replay_momentum",
                        "外部动能回放",
                        feature_json,
                        snapshot_time,
                    ),
                )
            except sqlite3.IntegrityError:
                skipped_existing += 1
                continue
            if int(cursor.rowcount or 0) <= 0:
                skipped_existing += 1
                continue
            inserted_snapshots += 1
            snapshot_id = int(cursor.lastrowid)
            outcome_cursor = conn.execute(
                """
                INSERT OR IGNORE INTO snapshot_outcomes (
                    snapshot_id, symbol, snapshot_time, horizon_min, future_snapshot_time,
                    future_price, future_spread_points, price_change_pct, max_price, min_price,
                    mfe_pct, mae_pct, outcome_label, signal_quality, labeled_at, is_clustered
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    snapshot_id,
                    sample.symbol,
                    snapshot_time,
                    sample.horizon_min,
                    sample.future_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    sample.future_price,
                    sample.spread_points,
                    sample.price_change_pct,
                    sample.max_price,
                    sample.min_price,
                    sample.mfe_pct,
                    sample.mae_pct,
                    sample.outcome_label,
                    sample.signal_quality,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            inserted_outcomes += int(outcome_cursor.rowcount or 0)
    return {
        "ok": True,
        "csv_path": str(Path(csv_path).resolve()),
        "symbol": str(symbol).upper(),
        "bar_count": len(bars),
        "candidate_count": len(samples),
        "inserted_snapshots": inserted_snapshots,
        "inserted_outcomes": inserted_outcomes,
        "skipped_existing": skipped_existing,
        "db_path": str(target_db),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入外部 M1/OHLC 历史行情回放样本。")
    parser.add_argument("csv_path", help="历史行情 CSV 文件路径")
    parser.add_argument("--symbol", default="XAUUSD", help="品种代码，默认 XAUUSD")
    parser.add_argument("--db-path", default=str(KNOWLEDGE_DB_FILE), help="知识库 DB 路径")
    parser.add_argument("--horizon-min", type=int, default=30, help="结果评估窗口，默认 30 分钟")
    parser.add_argument("--lookback-bars", type=int, default=60, help="候选识别回看 K 线数，默认 60")
    parser.add_argument("--stride-bars", type=int, default=5, help="采样步长，默认每 5 根")
    parser.add_argument("--min-move-pct", type=float, default=0.12, help="动能候选最小涨跌幅百分比")
    args = parser.parse_args(argv)

    result = import_external_market_csv(
        args.csv_path,
        db_path=args.db_path,
        symbol=args.symbol,
        horizon_min=args.horizon_min,
        lookback_bars=args.lookback_bars,
        stride_bars=args.stride_bars,
        min_move_pct=args.min_move_pct,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
