from __future__ import annotations

import itertools
import json
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path

from knowledge_base import (
    KNOWLEDGE_DB_FILE,
    _normalize_text,
    _now_text,
    open_knowledge_connection,
    upsert_source,
)
from knowledge_ml import _extract_row_features
from prompt_cluster_miner import PROMPT_LLM_CLUSTER_LOSS, PROMPT_LLM_GOLDEN_SETUP

logger = logging.getLogger(__name__)

PATTERN_MINER_OUTCOME_LIMIT = 4000
_PATTERN_HORIZON_PRIORITY = {
    888: 2,
    30: 1,
}
_REFLECTION_HORIZON_ORDER = (888, 30)
_GOLDEN_SETUP_MIN_ROI = {
    888: 3.0,
    30: 2.0,
}
_FALLBACK_30M_MAX_RULES_PER_RUN = 2
_FALLBACK_30M_EXECUTION_KEYWORDS = (
    "做多",
    "做空",
    "回踩",
    "企稳",
    "突破",
    "止损",
    "止盈",
    "支撑",
    "压力",
    "顺势",
    "逆势",
    "多头",
    "空头",
    "入场",
    "建仓",
    "开仓",
    "观望",
    "暂停建仓",
    "回落",
    "反抽",
)


def _load_feature_payload(raw_json: str) -> dict:
    try:
        payload = json.loads(str(raw_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _fetch_recent_pattern_rows(conn: sqlite3.Connection, max_outcomes: int) -> list[sqlite3.Row]:
    safe_limit = max(100, int(max_outcomes or PATTERN_MINER_OUTCOME_LIMIT))
    return conn.execute(
        """
        WITH recent_outcomes AS (
            SELECT
                so.id AS outcome_id,
                so.snapshot_id,
                so.horizon_min,
                so.outcome_label
            FROM snapshot_outcomes so
            WHERE so.horizon_min IN (30, 888)
              AND so.outcome_label IN ('success', 'fail')
            ORDER BY so.id DESC
            LIMIT ?
        )
        SELECT
            ms.id AS snapshot_id,
            ro.outcome_id,
            ro.horizon_min,
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
            ro.outcome_label
        FROM recent_outcomes ro
        JOIN market_snapshots ms ON ms.id = ro.snapshot_id
        ORDER BY ro.outcome_id DESC
        """,
        (safe_limit,),
    ).fetchall()


def _choose_preferred_pattern_rows(rows: list[sqlite3.Row]) -> list[dict]:
    selected: dict[int, dict] = {}

    def _row_rank(payload: dict) -> tuple[int, int]:
        horizon = int(payload.get("horizon_min", 0) or 0)
        outcome_id = int(payload.get("outcome_id", 0) or 0)
        return (_PATTERN_HORIZON_PRIORITY.get(horizon, 0), outcome_id)

    for row in rows:
        payload = dict(row)
        snapshot_id = int(payload.get("snapshot_id", 0) or 0)
        if snapshot_id <= 0:
            continue
        current = selected.get(snapshot_id)
        if current is None or _row_rank(payload) > _row_rank(current):
            selected[snapshot_id] = payload

    return sorted(selected.values(), key=lambda item: int(item.get("outcome_id", 0) or 0))


def mine_frequent_patterns(
    db_path: Path | str | None = None,
    min_samples: int = 15,
    min_win_rate: float = 0.68,
    max_outcomes: int = PATTERN_MINER_OUTCOME_LIMIT,
) -> dict:
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE

    with open_knowledge_connection(target_db) as conn:
        rows = _fetch_recent_pattern_rows(conn, max_outcomes=max_outcomes)

    effective_rows = _choose_preferred_pattern_rows(rows)
    samples = []
    for row in effective_rows:
        outcome_label = str(row.get("outcome_label", "") or "").strip().lower()
        is_success = outcome_label == "success"
        features = _extract_row_features(row)
        valid_items = []
        for key, value in features.items():
            if key == "symbol":
                continue
            valid_items.append(f"{key}:{value}")
        if valid_items:
            samples.append((sorted(valid_items), is_success))

    if not samples:
        return {"mined_patterns": 0, "inserted_rules": 0}

    combination_stats = defaultdict(lambda: {"total": 0, "success": 0})
    for items, is_success in samples:
        for length in (2, 3):
            for combo in itertools.combinations(items, length):
                combination_stats[tuple(combo)]["total"] += 1
                if is_success:
                    combination_stats[tuple(combo)]["success"] += 1

    high_win_patterns = []
    for combo_key, stats in combination_stats.items():
        total = int(stats["total"] or 0)
        if total < max(1, int(min_samples)):
            continue
        win_rate = float(stats["success"] or 0.0) / total
        if win_rate >= float(min_win_rate):
            high_win_patterns.append(
                {
                    "combo": combo_key,
                    "total": total,
                    "success": int(stats["success"] or 0),
                    "win_rate": win_rate,
                }
            )

    high_win_patterns.sort(key=lambda item: (-item["win_rate"], -item["total"]))
    best_patterns = high_win_patterns[:10]

    inserted_count = 0
    source_id = upsert_source(
        title="数据挖掘引擎自提取结构",
        source_type="auto_miner",
        location="auto_miner_v1",
        trust_level="working",
        tags=["auto_miner", "frequent_pattern"],
        notes=f"基于 {len(samples)} 个真实记录与演练得出",
        db_path=target_db,
    )
    with open_knowledge_connection(target_db) as conn:
        for pattern in best_patterns:
            direction = "neutral"
            condition_texts = []
            for item in pattern["combo"]:
                key, value = item.split(":", 1)
                condition_texts.append(f"{key} 状态为 {value}")
                if key == "signal_side" and value in {"long", "short"}:
                    direction = value

            direction_text = (
                "做多 (long)"
                if direction == "long"
                else "做空 (short)"
                if direction == "short"
                else "在此结构下操作"
            )
            rule_text = (
                f"【特征挖掘】当 {' 并且 '.join(condition_texts)} 时，"
                f"{direction_text} 历史胜率可达 {pattern['win_rate']:.1%}。"
                f" (样本量/触发基数: {pattern['total']})"
            )
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO knowledge_rules (
                    source_id, document_id, section_title, category, asset_scope,
                    rule_text, confidence, evidence_type, tags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    None,
                    "挖掘特征组合",
                    "entry",
                    "ALL",
                    _normalize_text(rule_text),
                    "candidate",
                    "机器挖掘",
                    json.dumps(["auto", "derived"]),
                    _now_text(),
                ),
            )
            if cursor.rowcount > 0:
                inserted_count += 1
                logger.info("💡 自动挖掘发现一条新规则: %s", rule_text)
        conn.commit()

    return {"mined_patterns": len(best_patterns), "inserted_rules": inserted_count}


def _post_json_to_llm(
    api_base: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_content: str,
) -> list[dict]:
    import urllib.error
    import urllib.request

    url = str(api_base or "").strip().rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            text = response.read().decode("utf-8")
            response_json = json.loads(text)
            content = response_json.get("choices", [])[0].get("message", {}).get("content", "")
            content = content.replace("```json", "").replace("```", "").strip()
            try:
                from json_repair import loads as _repair

                parsed = _repair(content)
            except ImportError:
                parsed = json.loads(content)

            if isinstance(parsed, dict) and "rules" in parsed:
                return parsed["rules"]
            if isinstance(parsed, dict) and "category" in parsed:
                return [parsed]
            if isinstance(parsed, list):
                return parsed
            return []
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM Cluster Reflection 请求失败: %s", exc)
        return []


def _load_reflection_rows(conn: sqlite3.Connection) -> tuple[int, list[sqlite3.Row]]:
    for horizon_min in _REFLECTION_HORIZON_ORDER:
        if horizon_min == 888:
            rows = conn.execute(
                """
                SELECT
                    so.id as outcome_id,
                    so.horizon_min,
                    so.outcome_label,
                    so.mfe_pct,
                    so.mae_pct,
                    ms.symbol,
                    ms.regime_tag,
                    ms.trade_grade,
                    ms.trade_grade_source,
                    ms.signal_side,
                    ms.feature_json,
                    ms.created_at
                FROM snapshot_outcomes so
                JOIN market_snapshots ms ON ms.id = so.snapshot_id
                WHERE so.horizon_min = 888
                  AND so.is_clustered = 0
                ORDER BY so.id ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    so.id as outcome_id,
                    so.horizon_min,
                    so.outcome_label,
                    so.mfe_pct,
                    so.mae_pct,
                    ms.symbol,
                    ms.regime_tag,
                    ms.trade_grade,
                    ms.trade_grade_source,
                    ms.signal_side,
                    ms.feature_json,
                    ms.created_at
                FROM snapshot_outcomes so
                JOIN market_snapshots ms ON ms.id = so.snapshot_id
                WHERE so.horizon_min = 30
                  AND so.is_clustered = 0
                  AND so.outcome_label IN ('success', 'fail')
                  AND ms.trade_grade = '可轻仓试仓'
                  AND ms.trade_grade_source IN ('structure', 'setup')
                  AND ms.signal_side IN ('long', 'short')
                ORDER BY so.id ASC
                """
            ).fetchall()
        if rows:
            return horizon_min, rows
    return 0, []


def _reflection_source_suffix(horizon_min: int) -> str:
    if int(horizon_min or 0) == 888:
        return "sim"
    if int(horizon_min or 0) == 30:
        return "fallback_30m"
    return "generic"


def _has_fallback_execution_signal(rule_text: str, category: str) -> bool:
    text = _normalize_text(rule_text)
    if not text:
        return False
    if any(keyword in text for keyword in _FALLBACK_30M_EXECUTION_KEYWORDS):
        return True
    return category in {"entry", "trend", "directional", "risk"} and len(text) >= 16


def _prepare_reflection_results(results: list[dict], reflection_horizon: int) -> tuple[list[dict], dict]:
    prepared = []
    seen_rule_texts: set[str] = set()
    per_run_limit = _FALLBACK_30M_MAX_RULES_PER_RUN if int(reflection_horizon or 0) == 30 else 999999
    stats = {
        "raw_candidate_count": 0,
        "empty_candidate_count": 0,
        "quality_filtered_count": 0,
        "duplicate_in_batch_count": 0,
        "limit_truncated_count": 0,
    }
    for item in list(results or []):
        stats["raw_candidate_count"] += 1
        rule_dict = dict(item.get("rule", {}) or {})
        source_name = str(item.get("source", "") or "").strip()
        category = str(rule_dict.get("category", "entry") or "entry").strip().lower()
        rule_text = _normalize_text(rule_dict.get("rule_text", ""))
        if not source_name or not rule_text:
            stats["empty_candidate_count"] += 1
            continue
        if int(reflection_horizon or 0) == 30 and not _has_fallback_execution_signal(rule_text, category):
            stats["quality_filtered_count"] += 1
            continue
        if rule_text in seen_rule_texts:
            stats["duplicate_in_batch_count"] += 1
            continue
        seen_rule_texts.add(rule_text)
        if len(prepared) >= per_run_limit:
            stats["limit_truncated_count"] += 1
            continue
        prepared.append(
            {
                "source": source_name,
                "rule": {
                    **rule_dict,
                    "category": category or "entry",
                    "rule_text": rule_text,
                    "asset_scope": str(rule_dict.get("asset_scope", "ALL") or "ALL").strip() or "ALL",
                },
            }
        )
    stats["prepared_candidate_count"] = len(prepared)
    return prepared, stats


def run_llm_batch_reflection(db_path: Path | str | None, config) -> dict:
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    api_key = str(getattr(config, "ai_api_key", "") or "").strip()
    api_base = str(getattr(config, "ai_api_base", "https://api.siliconflow.cn/v1") or "").strip()
    model = str(getattr(config, "ai_model", "deepseek-ai/DeepSeek-R1") or "").strip()

    if not api_key:
        return {"mined_patterns": 0, "inserted_rules": 0, "quality_filtered_count": 0, "duplicate_skipped_count": 0}

    with open_knowledge_connection(target_db) as conn:
        conn.row_factory = sqlite3.Row
        try:
            reflection_horizon, rows = _load_reflection_rows(conn)
        except sqlite3.OperationalError:
            return {"mined_patterns": 0, "inserted_rules": 0, "quality_filtered_count": 0, "duplicate_skipped_count": 0}

    if not rows:
        return {"mined_patterns": 0, "inserted_rules": 0, "quality_filtered_count": 0, "duplicate_skipped_count": 0}

    groups = defaultdict(list)
    for row in rows:
        payload = dict(row)
        feature_payload = _load_feature_payload(str(payload.get("feature_json", "{}") or "{}"))
        payload["market_text"] = _normalize_text(feature_payload.get("summary_text", ""))
        payload["reflection_horizon"] = int(reflection_horizon)
        groups[(payload["symbol"], payload["regime_tag"])].append(payload)

    results = []
    processed_ids: set[int] = set()

    def _mark_processed(items_to_mark) -> None:
        for item in list(items_to_mark or []):
            try:
                processed_ids.add(int(item["outcome_id"]))
            except (KeyError, TypeError, ValueError):
                continue

    def _append_loss_cluster(symbol: str, regime_tag: str, losses: list[dict]) -> None:
        if len(losses) < 3:
            return
        horizon_min = int(losses[0].get("reflection_horizon", reflection_horizon) or reflection_horizon)
        horizon_text = "模拟盘真实成交复盘" if horizon_min == 888 else "30分钟执行结果轻量复盘"
        transactions_text = "\n---\n".join(
            [
                f"[{item['created_at']}] MFE:{item['mfe_pct']:.2%}, MAE:{item['mae_pct']:.2%}\n"
                f"Features: {item['feature_json']}\nMarket: {item['market_text']}"
                for item in losses
            ]
        )
        system_prompt = PROMPT_LLM_CLUSTER_LOSS.format(
            regime_tag=f"{regime_tag} / {horizon_text}",
            symbol=symbol,
            count=len(losses),
            transactions_text=transactions_text,
        )
        rules = _post_json_to_llm(
            api_base,
            api_key,
            model,
            system_prompt,
            "请直接输出 JSON 数组，无需其他分析。注意结合特征。",
        )
        for rule in rules:
            if isinstance(rule, dict) and "category" in rule:
                results.append({"source": "llm_cluster_loss", "rule": rule})

    for (symbol, regime_tag), items in groups.items():
        consecutive_losses = []
        for item in items:
            if item["outcome_label"] == "fail":
                consecutive_losses.append(item)
                continue

            if consecutive_losses:
                _append_loss_cluster(symbol, regime_tag, consecutive_losses)
                _mark_processed(consecutive_losses)
                consecutive_losses = []

            mfe = float(item["mfe_pct"] or 0.0)
            mae = max(float(item["mae_pct"] or 0.0001), 0.0001)
            roi = mfe / mae
            horizon_min = int(item.get("reflection_horizon", reflection_horizon) or reflection_horizon)
            min_roi = float(_GOLDEN_SETUP_MIN_ROI.get(horizon_min, 3.0))
            if item["outcome_label"] == "success" and roi >= min_roi:
                horizon_text = "模拟盘真实成交复盘" if horizon_min == 888 else "30分钟执行结果轻量复盘"
                transactions_text = (
                    f"[{item['created_at']}] 极致 ROI:{roi:.2f} "
                    f"(MFE:{mfe:.2%}, MAE:{item['mae_pct']:.2%})\n"
                    f"Features: {item['feature_json']}\nMarket: {item['market_text']}"
                )
                system_prompt = PROMPT_LLM_GOLDEN_SETUP.format(
                    regime_tag=f"{regime_tag} / {horizon_text}",
                    symbol=symbol,
                    count=1,
                    transactions_text=transactions_text,
                )
                rules = _post_json_to_llm(
                    api_base,
                    api_key,
                    model,
                    system_prompt,
                    "请直接输出 JSON 数组，无需其他分析。",
                )
                for rule in rules:
                    if isinstance(rule, dict) and "category" in rule:
                        results.append({"source": "llm_golden_setup", "rule": rule})
            _mark_processed([item])

        if consecutive_losses:
            if len(consecutive_losses) >= 3:
                _append_loss_cluster(symbol, regime_tag, consecutive_losses)
                _mark_processed(consecutive_losses)
            # 尾部不足 3 个连续 fail 故意保留，方便下一轮继续拼接。

    results, prepare_stats = _prepare_reflection_results(results, reflection_horizon)

    inserted_count = 0
    existing_duplicate_count = 0
    if results:
        source_id_cache: dict[str, int] = {}
        with open_knowledge_connection(target_db) as conn:
            now_text = _now_text()
            for index, item in enumerate(results, start=1):
                rule_dict = item["rule"]
                source_name = item["source"]
                source_id = source_id_cache.get(source_name)
                if not source_id:
                    source_location = f"auto_miner_v2_llm_{_reflection_source_suffix(reflection_horizon)}"
                    source_id = upsert_source(
                        title="大模型高级批量反思提取",
                        source_type=source_name,
                        location=source_location,
                        trust_level="working",
                        tags=["auto_miner", source_name, f"h{int(reflection_horizon)}"],
                        notes=(
                            "基于微观结构和历史连败/暴利聚类挖掘"
                            if int(reflection_horizon) == 888
                            else "基于30分钟执行结果的轻量反思挖掘"
                        ),
                        db_path=target_db,
                    )
                    source_id_cache[source_name] = source_id

                category = str(rule_dict.get("category", "entry") or "entry").strip()
                scope = str(rule_dict.get("asset_scope", "ALL") or "ALL").strip()
                rule_text = str(rule_dict.get("rule_text", "") or "").strip()
                if not rule_text:
                    continue
                existing_rule = conn.execute(
                    "SELECT id FROM knowledge_rules WHERE rule_text = ? LIMIT 1",
                    (rule_text,),
                ).fetchone()
                if existing_rule:
                    existing_duplicate_count += 1
                    continue

                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_rules (
                        source_id, document_id, section_title, category, asset_scope,
                        rule_text, confidence, evidence_type, tags_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        None,
                        "LLM深度环境反思",
                        category,
                        scope,
                        rule_text,
                        "pending",
                        "模型批量推演",
                        json.dumps(["llm_auto", source_name, f"h{int(reflection_horizon)}"]),
                        now_text,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted_count += 1
                if index % 50 == 0:
                    conn.commit()
            conn.commit()

    if processed_ids:
        with open_knowledge_connection(target_db) as conn:
            ordered_ids = sorted(processed_ids)
            chunk_size = 500
            for index in range(0, len(ordered_ids), chunk_size):
                chunk = ordered_ids[index : index + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                conn.execute(
                    f"UPDATE snapshot_outcomes SET is_clustered = 1 WHERE id IN ({placeholders})",
                    chunk,
                )
                conn.commit()

    return {
        "mined_patterns": len(results),
        "inserted_rules": inserted_count,
        "reflection_horizon": int(reflection_horizon),
        "raw_candidate_count": int(prepare_stats.get("raw_candidate_count", 0) or 0),
        "prepared_candidate_count": int(prepare_stats.get("prepared_candidate_count", 0) or 0),
        "quality_filtered_count": int(prepare_stats.get("quality_filtered_count", 0) or 0),
        "duplicate_skipped_count": int(prepare_stats.get("duplicate_in_batch_count", 0) or 0) + int(existing_duplicate_count or 0),
        "duplicate_in_batch_count": int(prepare_stats.get("duplicate_in_batch_count", 0) or 0),
        "duplicate_existing_count": int(existing_duplicate_count or 0),
        "empty_candidate_count": int(prepare_stats.get("empty_candidate_count", 0) or 0),
        "limit_truncated_count": int(prepare_stats.get("limit_truncated_count", 0) or 0),
    }
