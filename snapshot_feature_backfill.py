"""
历史快照特征修复器：
1. 为旧版 feature_json 反推缺失的结构字段；
2. 将提醒留痕中的风控/模型字段回填到 market_snapshots；
3. 输出修复覆盖率，便于判断知识库样本是否仍在“吃残样本”。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from alert_history_store import HISTORY_FILE
from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from risk_reward import analyze_risk_reward
from signal_side_utils import derive_signal_side_meta
from signal_enums import TradeGrade


TEXT_TO_ENUM = {
    "intraday_bias": {
        "偏多": "bullish",
        "偏空": "bearish",
        "震荡": "sideways",
        "节奏不足": "unknown",
    },
    "intraday_volatility": {
        "波动放大": "high",
        "波动偏静": "low",
        "波动正常": "normal",
        "波动未知": "unknown",
    },
    "intraday_location": {
        "贴近区间高位": "upper",
        "贴近区间低位": "lower",
        "处于区间中段": "middle",
        "位置未知": "unknown",
    },
    "multi_timeframe_alignment": {
        "多周期同向": "aligned",
        "多周期分歧": "mixed",
        "多周期待确认": "partial",
        "多周期震荡": "range",
        "多周期不足": "unknown",
    },
    "multi_timeframe_bias": {
        "偏多": "bullish",
        "偏空": "bearish",
        "震荡": "sideways",
        "方向分歧": "mixed",
        "待确认": "unknown",
    },
    "key_level_state": {
        "上破关键位": "breakout_above",
        "下破关键位": "breakout_below",
        "贴近高位": "near_high",
        "贴近低位": "near_low",
        "位于区间中段": "mid_range",
        "关键位未知": "unknown",
    },
    "breakout_state": {
        "上破已确认": "confirmed_above",
        "上破失败": "failed_above",
        "上破待确认": "pending_above",
        "下破已确认": "confirmed_below",
        "下破失败": "failed_below",
        "下破待确认": "pending_below",
        "暂无突破": "none",
        "突破未知": "unknown",
    },
    "retest_state": {
        "回踩已确认": "confirmed_support",
        "回踩待确认": "waiting_support",
        "回踩失守": "failed_support",
        "反抽已确认": "confirmed_resistance",
        "反抽待确认": "waiting_resistance",
        "反抽失守": "failed_resistance",
        "暂无回踩": "none",
        "回踩未知": "unknown",
    },
}

HISTORY_FIELDS = (
    "risk_reward_ready",
    "risk_reward_ratio",
    "stop_loss_price",
    "take_profit_1",
    "take_profit_2",
    "entry_zone_low",
    "entry_zone_high",
    "model_ready",
    "model_win_probability",
    "signal_side",
    "signal_side_text",
    "regime_text",
    "regime_reason",
)


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _load_json_dict(raw_text: str) -> dict:
    try:
        payload = json.loads(str(raw_text or "{}"))
    except json.JSONDecodeError:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _merge_history_entry(target: dict, source: dict) -> None:
    for key in HISTORY_FIELDS:
        value = source.get(key)
        if key not in target or _is_missing(target.get(key)):
            if value is not None and not (isinstance(value, str) and not value.strip()):
                target[key] = value


def _build_history_index(
    history_file: Path,
    start_time: str = "",
    end_time: str = "",
    symbol: str = "",
) -> dict[tuple[str, str], dict]:
    if not history_file.exists():
        return {}

    symbol_filter = _normalize_text(symbol).upper()
    index: dict[tuple[str, str], dict] = {}
    for raw_line in history_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = _load_json_dict(line)
        occurred_at = _normalize_text(payload.get("occurred_at", ""))
        entry_symbol = _normalize_text(payload.get("symbol", "")).upper()
        if not occurred_at or not entry_symbol:
            continue
        if start_time and occurred_at < start_time:
            continue
        if end_time and occurred_at > end_time:
            continue
        if symbol_filter and entry_symbol != symbol_filter:
            continue
        merged = index.setdefault((occurred_at, entry_symbol), {})
        _merge_history_entry(merged, payload)
    return index


def _apply_text_mapping(payload: dict, target_key: str, text_key: str) -> bool:
    if not _is_missing(payload.get(target_key)):
        return False
    text_value = _normalize_text(payload.get(text_key, ""))
    if not text_value:
        return False
    mapped = TEXT_TO_ENUM.get(target_key, {}).get(text_value)
    if not mapped:
        return False
    payload[target_key] = mapped
    return True


def _infer_breakout_direction(payload: dict) -> bool:
    if not _is_missing(payload.get("breakout_direction")):
        return False
    breakout_state = _normalize_text(payload.get("breakout_state", "")).lower()
    if breakout_state in {"confirmed_above", "failed_above", "pending_above"}:
        payload["breakout_direction"] = "bullish"
        return True
    if breakout_state in {"confirmed_below", "failed_below", "pending_below"}:
        payload["breakout_direction"] = "bearish"
        return True
    if breakout_state == "none":
        payload["breakout_direction"] = "neutral"
        return True
    return False


def _infer_risk_reward_direction(payload: dict) -> bool:
    if not _is_missing(payload.get("risk_reward_direction")):
        return False
    for key in ("breakout_direction", "multi_timeframe_bias", "intraday_bias"):
        value = _normalize_text(payload.get(key, "")).lower()
        if value in {"bullish", "bearish"}:
            payload["risk_reward_direction"] = value
            return True
    signal_side = _normalize_text(payload.get("signal_side", "")).lower()
    if signal_side == "long":
        payload["risk_reward_direction"] = "bullish"
        return True
    if signal_side == "short":
        payload["risk_reward_direction"] = "bearish"
        return True
    return False


def _infer_signal_side_fields(payload: dict) -> bool:
    current_side = _normalize_text(payload.get("signal_side", "")).lower()
    current_basis = _normalize_text(payload.get("signal_side_basis", ""))
    current_reason = _normalize_text(payload.get("signal_side_reason", ""))
    current_long_votes = int(payload.get("signal_side_long_votes", 0) or 0)
    current_short_votes = int(payload.get("signal_side_short_votes", 0) or 0)

    meta = derive_signal_side_meta(payload)
    inferred_side = _normalize_text(meta.get("signal_side", "")).lower()
    if not inferred_side:
        return False

    changed = False
    if current_side in {"", "neutral"} and inferred_side in {"long", "short"}:
        payload["signal_side"] = inferred_side
        changed = True
    if _is_missing(payload.get("signal_side_text")) and meta.get("signal_side_text"):
        payload["signal_side_text"] = meta["signal_side_text"]
        changed = True
    if not current_basis and meta.get("signal_side_basis"):
        payload["signal_side_basis"] = meta["signal_side_basis"]
        changed = True
    if not current_reason and meta.get("signal_side_reason"):
        payload["signal_side_reason"] = meta["signal_side_reason"]
        changed = True
    if current_long_votes == 0 and int(meta.get("signal_side_long_votes", 0) or 0) > 0:
        payload["signal_side_long_votes"] = int(meta["signal_side_long_votes"] or 0)
        changed = True
    if current_short_votes == 0 and int(meta.get("signal_side_short_votes", 0) or 0) > 0:
        payload["signal_side_short_votes"] = int(meta["signal_side_short_votes"] or 0)
        changed = True
    return changed


def _apply_history_patch(payload: dict, history_payload: dict) -> Counter:
    stats = Counter()
    for source_key, target_key in (
        ("risk_reward_ready", "risk_reward_ready"),
        ("risk_reward_ratio", "risk_reward_ratio"),
        ("stop_loss_price", "risk_reward_stop_price"),
        ("take_profit_1", "risk_reward_target_price"),
        ("take_profit_2", "risk_reward_target_price_2"),
        ("entry_zone_low", "risk_reward_entry_zone_low"),
        ("entry_zone_high", "risk_reward_entry_zone_high"),
        ("model_ready", "model_ready"),
        ("model_win_probability", "model_win_probability"),
        ("signal_side", "signal_side"),
        ("signal_side_text", "signal_side_text"),
        ("regime_text", "regime_text"),
        ("regime_reason", "regime_reason"),
    ):
        if target_key in payload and not _is_missing(payload.get(target_key)):
            continue
        if source_key not in history_payload:
            continue
        payload[target_key] = history_payload[source_key]
        stats["history_field_updates"] += 1
    return stats


def _repair_feature_payload(payload: dict, history_payload: dict | None = None) -> tuple[dict, Counter]:
    repaired = dict(payload or {})
    stats = Counter()

    for target_key, text_key in (
        ("intraday_bias", "intraday_bias_text"),
        ("intraday_volatility", "intraday_volatility_text"),
        ("intraday_location", "intraday_location_text"),
        ("multi_timeframe_alignment", "multi_timeframe_alignment_text"),
        ("multi_timeframe_bias", "multi_timeframe_bias_text"),
        ("key_level_state", "key_level_state_text"),
        ("breakout_state", "breakout_state_text"),
        ("retest_state", "retest_state_text"),
    ):
        if _apply_text_mapping(repaired, target_key, text_key):
            stats["text_field_updates"] += 1

    if _infer_breakout_direction(repaired):
        stats["text_field_updates"] += 1
    if history_payload:
        stats.update(_apply_history_patch(repaired, history_payload))
    if _infer_risk_reward_direction(repaired):
        stats["text_field_updates"] += 1
    if _infer_signal_side_fields(repaired):
        stats["text_field_updates"] += 1

    return repaired, stats


def _fill_missing_quote_activity(payload: dict, row: dict) -> Counter:
    stats = Counter()
    if bool(row["has_live_quote"]):
        return stats
    if not _is_missing(payload.get("quote_live_reason")):
        return stats

    status_text = _normalize_text(payload.get("status_text", ""))
    quote_text = _normalize_text(payload.get("quote_text", ""))
    latest_price = float(row["latest_price"] or 0.0)
    if "未识别" in status_text or "未加入" in status_text or "异常" in status_text:
        payload["quote_live_reason"] = "no_price"
        payload["quote_live_reason_text"] = "MT5 空报价/链路异常"
        payload["quote_live_diagnostic_text"] = status_text or "历史样本显示当时未拿到有效报价。"
        stats["quote_activity_updates"] += 1
        return stats
    if quote_text or latest_price > 0:
        payload["quote_live_reason"] = "stale_tick"
        payload["quote_live_reason_text"] = "报价延迟/旧 tick"
        payload["quote_live_diagnostic_text"] = status_text or "历史样本存在价格但被标记为非活跃，更像旧 tick 或活跃性误判。"
        stats["quote_activity_updates"] += 1
        return stats
    payload["quote_live_reason"] = "no_price"
    payload["quote_live_reason_text"] = "MT5 空报价/时间异常"
    payload["quote_live_diagnostic_text"] = status_text or "历史样本未保留有效价格。"
    stats["quote_activity_updates"] += 1
    return stats


def _fill_missing_risk_reward(payload: dict, row: dict) -> Counter:
    stats = Counter()
    has_rr = not _is_missing(payload.get("risk_reward_ready")) or not _is_missing(payload.get("risk_reward_ratio"))
    if has_rr:
        rr_state = _normalize_text(payload.get("risk_reward_state", "")).lower()
        rr_state_text = _normalize_text(payload.get("risk_reward_state_text", ""))
        if rr_state != "unknown" and "未知" not in rr_state_text:
            return stats

    risk_row = dict(payload)
    risk_row["latest_price"] = float(row["latest_price"] or 0.0)
    risk_row["spread_points"] = float(row["spread_points"] or 0.0)
    estimated = analyze_risk_reward(risk_row)
    if not bool(estimated.get("risk_reward_ready", False)):
        return stats

    allow_replace_unknown = (
        _normalize_text(payload.get("risk_reward_state", "")).lower() == "unknown"
        or "未知" in _normalize_text(payload.get("risk_reward_state_text", ""))
    )
    for key in (
        "risk_reward_ready",
        "risk_reward_state",
        "risk_reward_state_text",
        "risk_reward_context_text",
        "risk_reward_ratio",
        "risk_reward_direction",
        "risk_reward_basis",
        "risk_reward_atr",
        "risk_reward_stop_price",
        "risk_reward_target_price",
        "risk_reward_target_price_2",
        "risk_reward_position_text",
        "risk_reward_invalidation_text",
        "risk_reward_entry_zone_low",
        "risk_reward_entry_zone_high",
        "risk_reward_entry_zone_text",
    ):
        if key in payload and not _is_missing(payload.get(key)):
            if allow_replace_unknown and key in {
                "risk_reward_state",
                "risk_reward_state_text",
                "risk_reward_context_text",
                "risk_reward_basis",
                "risk_reward_position_text",
                "risk_reward_invalidation_text",
                "risk_reward_entry_zone_text",
            }:
                payload[key] = estimated.get(key)
                stats["recomputed_risk_fields"] += 1
            continue
        payload[key] = estimated.get(key)
        stats["recomputed_risk_fields"] += 1
    return stats


def _repair_execution_note(payload: dict, row: dict) -> Counter:
    stats = Counter()
    trade_grade = _normalize_text(payload.get("trade_grade", "")) or _normalize_text(row["trade_grade"])
    trade_grade_detail = _normalize_text(payload.get("trade_grade_detail", ""))
    execution_note = _normalize_text(payload.get("execution_note", ""))
    if not trade_grade or not trade_grade_detail:
        return stats

    current_note = f"{trade_grade}：{trade_grade_detail}"
    known_prefixes = tuple(f"{candidate.value}：" for candidate in TradeGrade)
    current_prefix = f"{trade_grade}："
    if not execution_note:
        payload["execution_note"] = current_note
        stats["execution_note_repairs"] += 1
        return stats
    if execution_note.startswith(current_prefix):
        if trade_grade_detail not in execution_note:
            payload["execution_note"] = current_note
            stats["execution_note_repairs"] += 1
        return stats
    if execution_note.startswith(known_prefixes):
        payload["execution_note"] = current_note
        stats["execution_note_repairs"] += 1
    return stats


def _coverage_counter(payload: dict) -> Counter:
    keys = (
        "intraday_bias",
        "intraday_volatility",
        "multi_timeframe_alignment",
        "multi_timeframe_bias",
        "breakout_state",
        "retest_state",
        "risk_reward_ready",
        "risk_reward_ratio",
        "model_ready",
        "model_win_probability",
    )
    counter = Counter()
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            counter[key] += 1
        elif isinstance(value, (int, float)):
            if float(value) != 0.0:
                counter[key] += 1
        elif not _is_missing(value):
            counter[key] += 1
    return counter


def backfill_snapshot_features(
    db_path: Path | str | None = None,
    history_file: Path | str | None = None,
    start_time: str = "",
    end_time: str = "",
    symbol: str = "",
    dry_run: bool = False,
    limit: int = 0,
) -> dict:
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    target_history = Path(history_file) if history_file else HISTORY_FILE
    history_index = _build_history_index(target_history, start_time=start_time, end_time=end_time, symbol=symbol)
    symbol_filter = _normalize_text(symbol).upper()

    where_parts = []
    params: list[object] = []
    if start_time:
        where_parts.append("snapshot_time >= ?")
        params.append(start_time)
    if end_time:
        where_parts.append("snapshot_time <= ?")
        params.append(end_time)
    if symbol_filter:
        where_parts.append("symbol = ?")
        params.append(symbol_filter)
    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    limit_sql = f"LIMIT {int(limit)}" if int(limit or 0) > 0 else ""

    report = {
        "db_path": str(target_db),
        "history_file": str(target_history),
        "start_time": start_time,
        "end_time": end_time,
        "symbol": symbol_filter,
        "dry_run": bool(dry_run),
        "scanned_rows": 0,
        "updated_rows": 0,
        "text_field_updates": 0,
        "history_field_updates": 0,
        "recomputed_risk_fields": 0,
        "quote_activity_updates": 0,
        "signal_side_repairs": 0,
        "execution_note_repairs": 0,
        "coverage_before": {},
        "coverage_after": {},
    }

    with open_knowledge_connection(target_db, ensure_schema=True) as conn:
        rows = conn.execute(
            f"""
            SELECT id, snapshot_time, symbol, feature_json
                , latest_price, spread_points, has_live_quote, signal_side, trade_grade
            FROM market_snapshots
            {where_sql}
            ORDER BY snapshot_time ASC, id ASC
            {limit_sql}
            """,
            tuple(params),
        ).fetchall()

        coverage_before = Counter()
        coverage_after = Counter()
        pending_updates: list[tuple[str, str, int]] = []

        for row in rows:
            payload = _load_json_dict(str(row["feature_json"] or "{}"))
            coverage_before.update(_coverage_counter(payload))

            history_payload = history_index.get(
                (_normalize_text(row["snapshot_time"]), _normalize_text(row["symbol"]).upper()),
                {},
            )
            repaired, stats = _repair_feature_payload(payload, history_payload=history_payload)
            stats.update(_fill_missing_quote_activity(repaired, row))
            stats.update(_fill_missing_risk_reward(repaired, row))
            stats.update(_repair_execution_note(repaired, row))
            coverage_after.update(_coverage_counter(repaired))
            report["scanned_rows"] += 1
            report["text_field_updates"] += int(stats.get("text_field_updates", 0) or 0)
            report["history_field_updates"] += int(stats.get("history_field_updates", 0) or 0)
            report["recomputed_risk_fields"] += int(stats.get("recomputed_risk_fields", 0) or 0)
            report["quote_activity_updates"] += int(stats.get("quote_activity_updates", 0) or 0)
            if _normalize_text(payload.get("signal_side", "")).lower() != _normalize_text(repaired.get("signal_side", "")).lower():
                report["signal_side_repairs"] += 1
            report["execution_note_repairs"] += int(stats.get("execution_note_repairs", 0) or 0)

            row_signal_side = _normalize_text(row["signal_side"]).lower() or "neutral"
            repaired_signal_side = _normalize_text(repaired.get("signal_side", "")).lower() or row_signal_side
            if repaired != payload or row_signal_side != repaired_signal_side:
                report["updated_rows"] += 1
                pending_updates.append((json.dumps(repaired, ensure_ascii=False), repaired_signal_side, int(row["id"])))

        if pending_updates and not dry_run:
            with conn:
                conn.executemany(
                    "UPDATE market_snapshots SET feature_json = ?, signal_side = ? WHERE id = ?",
                    pending_updates,
                )

    report["coverage_before"] = dict(coverage_before)
    report["coverage_after"] = dict(coverage_after)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回填 market_snapshots.feature_json 中缺失的结构与模型字段")
    parser.add_argument("--db-path", default=str(KNOWLEDGE_DB_FILE), help="知识库 SQLite 路径")
    parser.add_argument("--history-file", default=str(HISTORY_FILE), help="提醒留痕 JSONL 路径")
    parser.add_argument("--symbol", default="", help="仅修复单一品种，例如 XAUUSD")
    parser.add_argument("--start-time", default="", help="起始时间，格式 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--end-time", default="", help="结束时间，格式 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--limit", type=int, default=0, help="最多扫描多少条 market_snapshots，0 表示不限制")
    parser.add_argument("--dry-run", action="store_true", help="只输出修复统计，不真正写回数据库")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    report = backfill_snapshot_features(
        db_path=args.db_path,
        history_file=args.history_file,
        symbol=args.symbol,
        start_time=args.start_time,
        end_time=args.end_time,
        dry_run=bool(args.dry_run),
        limit=int(args.limit or 0),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
