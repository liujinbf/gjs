"""
本地轻量胜率模型：
1. 基于 market_snapshots + snapshot_outcomes 训练一个无外部依赖的概率模型；
2. 先作为规则引擎的辅助证据，不直接接管决策；
3. 未来可平滑替换为 XGBoost/LightGBM，而不改调用方。
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from quote_models import SnapshotItem
from signal_enums import AlertTone, TradeGrade

MODEL_NAME = "naive-edge-v1"
MIN_TRAIN_SAMPLES = 20
MIN_FEATURE_SAMPLES = 4
LOW_PROBABILITY_BLOCK = 0.48
HIGH_PROBABILITY_CONFIRM = 0.68


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    return open_knowledge_connection(target, ensure_schema=True)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    """统一本地概率模型链消费的快照项字段契约。"""
    return SnapshotItem.from_payload(item).to_dict()


def _bucket_numeric(value: float, step: float) -> str:
    if step <= 0:
        return str(round(value, 4))
    lower = math.floor(value / step) * step
    upper = lower + step
    if step >= 1:
        return f"{lower:.0f}-{upper:.0f}"
    if step >= 0.1:
        return f"{lower:.1f}-{upper:.1f}"
    return f"{lower:.2f}-{upper:.2f}"


def _load_feature_payload(raw_json: str) -> dict:
    try:
        payload = json.loads(str(raw_json or "{}"))
    except json.JSONDecodeError:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _extract_row_features(row: sqlite3.Row) -> dict[str, str]:
    payload = _load_feature_payload(str(row["feature_json"] or "{}"))
    atr14 = float(payload.get("atr14", 0.0) or 0.0)
    atr14_h4 = float(payload.get("atr14_h4", 0.0) or 0.0)
    latest_price = float(row["latest_price"] or 0.0)
    spread_points = float(row["spread_points"] or 0.0)
    atr_pct = (atr14 / latest_price * 100.0) if latest_price > 0 and atr14 > 0 else 0.0
    features = {
        "symbol": _normalize_text(row["symbol"]).upper(),
        "regime_tag": _normalize_text(row["regime_tag"]).lower() or "unknown",
        "trade_grade": _normalize_text(row["trade_grade"]) or "unknown",
        "trade_grade_source": _normalize_text(row["trade_grade_source"]) or "unknown",
        "signal_side": _normalize_text(row["signal_side"]).lower() or "neutral",
        "tone": _normalize_text(row["tone"]).lower() or "neutral",
        "event_importance": _normalize_text(row["event_importance_text"]) or "none",
        "alert_state": _normalize_text(row["alert_state_text"]) or "none",
        "risk_reward_state": _normalize_text(payload.get("risk_reward_state_text", "")) or "unknown",
        "intraday_bias": _normalize_text(payload.get("intraday_bias_text", "")) or "unknown",
        "multi_timeframe_bias": _normalize_text(payload.get("multi_timeframe_bias_text", "")) or "unknown",
        "breakout_state": _normalize_text(payload.get("breakout_state_text", "")) or "unknown",
        "retest_state": _normalize_text(payload.get("retest_state_text", "")) or "unknown",
        "spread_bucket": _bucket_numeric(spread_points, 10.0 if spread_points >= 1 else 1.0),
        "atr_pct_bucket": _bucket_numeric(atr_pct, 0.2),
        "atr_h4_bucket": _bucket_numeric(atr14_h4, 5.0 if atr14_h4 >= 1 else 1.0),
    }
    return {key: value for key, value in features.items() if value and value != "unknown"}


def _extract_item_features(snapshot: dict, item: dict) -> dict[str, str]:
    latest_price = float(item.get("latest_price", 0.0) or 0.0)
    atr14 = float(item.get("atr14", 0.0) or 0.0)
    atr14_h4 = float(item.get("atr14_h4", 0.0) or 0.0)
    spread_points = float(item.get("spread_points", 0.0) or 0.0)
    atr_pct = (atr14 / latest_price * 100.0) if latest_price > 0 and atr14 > 0 else 0.0
    return {
        "symbol": _normalize_text(item.get("symbol", "")).upper(),
        "regime_tag": _normalize_text(item.get("regime_tag", "") or snapshot.get("regime_tag", "")).lower() or "unknown",
        "trade_grade": _normalize_text(item.get("trade_grade", "")) or "unknown",
        "trade_grade_source": _normalize_text(item.get("trade_grade_source", "")) or "unknown",
        "signal_side": _normalize_text(item.get("signal_side", "")).lower() or "neutral",
        "tone": _normalize_text(item.get("tone", "")).lower() or "neutral",
        "event_importance": _normalize_text(item.get("event_importance_text", "")) or "none",
        "alert_state": _normalize_text(item.get("alert_state_text", "")) or "none",
        "risk_reward_state": _normalize_text(item.get("risk_reward_state_text", "")) or "unknown",
        "intraday_bias": _normalize_text(item.get("intraday_bias_text", "")) or "unknown",
        "multi_timeframe_bias": _normalize_text(item.get("multi_timeframe_bias_text", "")) or "unknown",
        "breakout_state": _normalize_text(item.get("breakout_state_text", "")) or "unknown",
        "retest_state": _normalize_text(item.get("retest_state_text", "")) or "unknown",
        "spread_bucket": _bucket_numeric(spread_points, 10.0 if spread_points >= 1 else 1.0),
        "atr_pct_bucket": _bucket_numeric(atr_pct, 0.2),
        "atr_h4_bucket": _bucket_numeric(atr14_h4, 5.0 if atr14_h4 >= 1 else 1.0),
    }


def train_probability_model(
    db_path: Path | str | None = None,
    horizon_min: int = 30,
    min_train_samples: int = MIN_TRAIN_SAMPLES,
) -> dict:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                ms.symbol,
                ms.latest_price,
                ms.spread_points,
                ms.tone,
                ms.trade_grade,
                ms.trade_grade_source,
                ms.alert_state_text,
                ms.event_importance_text,
                ms.signal_side,
                ms.regime_tag,
                ms.feature_json,
                so.outcome_label
            FROM snapshot_outcomes so
            JOIN market_snapshots ms ON ms.id = so.snapshot_id
            WHERE so.horizon_min = ?
              AND so.outcome_label IN ('success', 'mixed', 'fail')
            ORDER BY so.id ASC
            """,
            (int(horizon_min),),
        ).fetchall()

        sample_count = len(rows)
        if sample_count < max(8, int(min_train_samples)):
            conn.execute(
                """
                INSERT INTO ml_model_runs (
                    model_name, horizon_min, sample_count, base_win_probability,
                    feature_count, status, notes, created_at
                ) VALUES (?, ?, ?, 0, 0, 'insufficient', ?, ?)
                """,
                (
                    MODEL_NAME,
                    int(horizon_min),
                    sample_count,
                    f"样本不足，至少需要 {max(8, int(min_train_samples))} 条有效结果样本。",
                    _now_text(),
                ),
            )
            return {
                "model_name": MODEL_NAME,
                "horizon_min": int(horizon_min),
                "sample_count": sample_count,
                "status": "insufficient",
                "base_win_probability": 0.0,
                "feature_count": 0,
            }

        label_weight = {"success": 1.0, "mixed": 0.5, "fail": 0.0}
        base_weight = sum(label_weight.get(str(row["outcome_label"]), 0.0) for row in rows)
        base_probability = base_weight / sample_count if sample_count > 0 else 0.0

        feature_stats: dict[tuple[str, str], dict[str, float]] = {}
        for row in rows:
            outcome_label = str(row["outcome_label"] or "").strip().lower()
            success_weight = float(label_weight.get(outcome_label, 0.0))
            features = _extract_row_features(row)
            for feature_name, feature_value in features.items():
                key = (feature_name, feature_value)
                stat = feature_stats.setdefault(
                    key,
                    {"sample_count": 0.0, "success_weight": 0.0, "mixed_count": 0.0, "fail_count": 0.0},
                )
                stat["sample_count"] += 1.0
                stat["success_weight"] += success_weight
                if outcome_label == "mixed":
                    stat["mixed_count"] += 1.0
                elif outcome_label == "fail":
                    stat["fail_count"] += 1.0

        with conn:
            conn.execute(
                "DELETE FROM ml_feature_stats WHERE model_name = ? AND horizon_min = ?",
                (MODEL_NAME, int(horizon_min)),
            )
            for (feature_name, feature_value), stat in feature_stats.items():
                feature_sample_count = int(stat["sample_count"])
                win_probability = float(stat["success_weight"]) / float(stat["sample_count"])
                conn.execute(
                    """
                    INSERT INTO ml_feature_stats (
                        model_name, horizon_min, feature_name, feature_value, sample_count,
                        success_weight, mixed_count, fail_count, win_probability, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        MODEL_NAME,
                        int(horizon_min),
                        feature_name,
                        feature_value,
                        feature_sample_count,
                        float(stat["success_weight"]),
                        int(stat["mixed_count"]),
                        int(stat["fail_count"]),
                        float(win_probability),
                        _now_text(),
                    ),
                )
            conn.execute(
                """
                INSERT INTO ml_model_runs (
                    model_name, horizon_min, sample_count, base_win_probability,
                    feature_count, status, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, 'trained', ?, ?)
                """,
                (
                    MODEL_NAME,
                    int(horizon_min),
                    sample_count,
                    float(base_probability),
                    len(feature_stats),
                    "已完成轻量胜率模型训练。",
                    _now_text(),
                ),
            )

    return {
        "model_name": MODEL_NAME,
        "horizon_min": int(horizon_min),
        "sample_count": sample_count,
        "status": "trained",
        "base_win_probability": float(base_probability),
        "feature_count": len(feature_stats),
    }


def _load_latest_model_meta(conn: sqlite3.Connection, horizon_min: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT model_name, sample_count, base_win_probability, feature_count, status, created_at
        FROM ml_model_runs
        WHERE horizon_min = ? AND model_name = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(horizon_min), MODEL_NAME),
    ).fetchone()


def predict_item_probability(
    snapshot: dict,
    item: dict,
    db_path: Path | str | None = None,
    horizon_min: int = 30,
) -> dict:
    features = _extract_item_features(snapshot, item)
    with _connect(db_path) as conn:
        model_meta = _load_latest_model_meta(conn, horizon_min=int(horizon_min))
        if model_meta is None or str(model_meta["status"] or "") != "trained":
            return {
                "model_ready": False,
                "model_name": MODEL_NAME,
                "win_probability": 0.0,
                "confidence_text": "未训练",
                "model_note": "本地胜率模型尚未形成有效样本。",
                "supporting_features": [],
            }

        base_probability = float(model_meta["base_win_probability"] or 0.0)
        sample_count = int(model_meta["sample_count"] or 0)
        numerator = base_probability
        denominator = 1.0
        supporting_features = []

        for feature_name, feature_value in features.items():
            row = conn.execute(
                """
                SELECT sample_count, win_probability
                FROM ml_feature_stats
                WHERE model_name = ? AND horizon_min = ? AND feature_name = ? AND feature_value = ?
                LIMIT 1
                """,
                (MODEL_NAME, int(horizon_min), feature_name, feature_value),
            ).fetchone()
            if row is None:
                continue
            feature_samples = int(row["sample_count"] or 0)
            if feature_samples < MIN_FEATURE_SAMPLES:
                continue
            feature_probability = float(row["win_probability"] or 0.0)
            weight = min(2.0, feature_samples / 10.0)
            numerator += feature_probability * weight
            denominator += weight
            supporting_features.append(
                {
                    "feature_name": feature_name,
                    "feature_value": feature_value,
                    "sample_count": feature_samples,
                    "win_probability": feature_probability,
                    "delta": feature_probability - base_probability,
                }
            )

    win_probability = numerator / denominator if denominator > 0 else base_probability
    win_probability = max(0.05, min(0.95, float(win_probability)))
    supporting_features.sort(key=lambda item_: abs(float(item_["delta"])), reverse=True)
    confidence_text = "高信心" if sample_count >= 120 else ("中等信心" if sample_count >= 50 else "基础信心")
    note_parts = [f"本地模型参考胜率约 {win_probability * 100:.0f}%"]
    if supporting_features:
        explain = "；".join(
            f"{item_['feature_name']}={item_['feature_value']}（样本 {item_['sample_count']}，胜率 {item_['win_probability'] * 100:.0f}%）"
            for item_ in supporting_features[:2]
        )
        note_parts.append(f"主要依据：{explain}")
    return {
        "model_ready": True,
        "model_name": MODEL_NAME,
        "win_probability": win_probability,
        "confidence_text": confidence_text,
        "model_note": "。".join(note_parts) + "。",
        "supporting_features": supporting_features[:3],
    }


def annotate_snapshot_with_model(snapshot: dict, db_path: Path | str | None = None, horizon_min: int = 30) -> dict:
    result = dict(snapshot or {})
    items = []
    probabilities = []
    for item in [_normalize_snapshot_item(item) for item in list(result.get("items", []) or [])]:
        enriched = dict(item or {})
        prediction = predict_item_probability(result, enriched, db_path=db_path, horizon_min=horizon_min)
        enriched.update(
            {
                "model_ready": bool(prediction.get("model_ready", False)),
                "model_name": str(prediction.get("model_name", "") or "").strip(),
                "model_win_probability": float(prediction.get("win_probability", 0.0) or 0.0),
                "model_confidence_text": str(prediction.get("confidence_text", "") or "").strip(),
                "model_note": str(prediction.get("model_note", "") or "").strip(),
                "model_supporting_features": list(prediction.get("supporting_features", []) or []),
            }
        )
        if enriched["model_ready"]:
            probabilities.append(enriched["model_win_probability"])
        items.append(enriched)

    result["items"] = items
    if probabilities:
        avg_probability = sum(probabilities) / len(probabilities)
        result["model_probability_summary_text"] = f"本地模型平均参考胜率约 {avg_probability * 100:.0f}%。"
    else:
        result["model_probability_summary_text"] = "本地模型样本仍不足，暂不提供胜率概率。"
    return result


def _replace_summary_line(summary_text: str, prefix: str, line: str) -> str:
    lines = [str(current or "") for current in str(summary_text or "").splitlines()]
    replaced = False
    for index, current in enumerate(lines):
        if current.startswith(prefix):
            lines[index] = line
            replaced = True
            break
    if not replaced:
        lines.append(line)
    return "\n".join(current for current in lines if _normalize_text(current))


def apply_model_probability_context(snapshot: dict) -> dict:
    payload = dict(snapshot or {})
    items = []
    model_notes = []

    for raw_item in [_normalize_snapshot_item(item) for item in list(payload.get("items", []) or [])]:
        item = dict(raw_item or {})
        probability = float(item.get("model_win_probability", 0.0) or 0.0)
        model_ready = bool(item.get("model_ready", False))
        grade = _normalize_text(item.get("trade_grade", ""))
        symbol = _normalize_text(item.get("symbol", "")).upper()

        if model_ready and grade == TradeGrade.LIGHT_POSITION and probability < LOW_PROBABILITY_BLOCK:
            item["trade_grade"] = TradeGrade.OBSERVE_ONLY.value
            item["trade_grade_source"] = "model"
            detail = _normalize_text(item.get("trade_grade_detail", ""))
            item["trade_grade_detail"] = (
                f"{detail} 本地模型参考胜率仅约 {probability * 100:.0f}%，"
                "说明当前结构虽然好看，但历史延续率还不够，先别急着动手。"
            ).strip()
            item["trade_next_review"] = "建议等下一轮结构确认或模型胜率回到更健康区间后再复核。"
            item["alert_state_text"] = "模型概率偏低"
            item["alert_state_detail"] = item["trade_grade_detail"]
            item["alert_state_tone"] = AlertTone.ACCENT.value
            item["alert_state_rank"] = max(int(item.get("alert_state_rank", 0) or 0), 3)
            model_notes.append(f"{symbol} 模型参考胜率约 {probability * 100:.0f}%，已从候选机会降级为观察。")
        elif model_ready and grade == TradeGrade.LIGHT_POSITION and probability >= HIGH_PROBABILITY_CONFIRM:
            detail = _normalize_text(item.get("trade_grade_detail", ""))
            item["trade_grade_detail"] = (
                f"{detail} 本地模型参考胜率约 {probability * 100:.0f}%，"
                "历史样本对当前结构有一定背书，但仍只按轻仓候选处理。"
            ).strip()
            model_notes.append(f"{symbol} 模型参考胜率约 {probability * 100:.0f}%，与当前候选结构基本一致。")

        items.append(item)

    payload["items"] = items
    if model_notes:
        payload["summary_text"] = _replace_summary_line(
            str(payload.get("summary_text", "") or ""),
            "模型参考：",
            f"模型参考：{'；'.join(model_notes[:3])}",
        )

        from monitor_rules import build_portfolio_trade_grade

        connected = str(payload.get("status_tone", "") or "").strip().lower() == "success"
        portfolio_grade = build_portfolio_trade_grade(
            items,
            connected,
            event_risk_mode=str(payload.get("event_risk_mode", "normal") or "normal"),
            event_context=None,
        )
        payload["trade_grade"] = portfolio_grade["grade"]
        payload["trade_grade_detail"] = portfolio_grade["detail"]
        payload["trade_next_review"] = portfolio_grade["next_review"]
        payload["trade_grade_tone"] = portfolio_grade["tone"]
        payload["summary_text"] = _replace_summary_line(
            str(payload.get("summary_text", "") or ""),
            "出手分级：",
            f"出手分级：{portfolio_grade['grade']}。{portfolio_grade['detail']}",
        )
    return payload
