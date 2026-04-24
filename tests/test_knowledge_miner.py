import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_base import init_knowledge_base, open_knowledge_connection, upsert_source
from knowledge_miner import mine_frequent_patterns, run_llm_batch_reflection


def _insert_snapshot_pair(conn, snapshot_id: int, outcome_id: int, snapshot_time: str, outcome_label: str, mfe_pct: float, mae_pct: float):
    conn.execute(
        """
        INSERT INTO market_snapshots (
            id, snapshot_time, symbol, latest_price, spread_points, has_live_quote, tone,
            trade_grade, trade_grade_source, alert_state_text, event_risk_mode_text,
            event_active_name, event_importance_text, event_note, signal_side,
            regime_tag, regime_text, feature_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            snapshot_time,
            "XAUUSD",
            3300.0,
            18.0,
            1,
            "warning",
            "可轻仓试仓",
            "structure",
            "结构候选",
            "正常观察",
            "",
            "",
            "",
            "long",
            "trend_expansion",
            "趋势扩张",
            '{"summary_text":"高波趋势扩张测试样本"}',
            snapshot_time,
        ),
    )
    conn.execute(
        """
        INSERT INTO snapshot_outcomes (
            id, snapshot_id, symbol, snapshot_time, horizon_min, future_snapshot_time,
            future_price, future_spread_points, price_change_pct, max_price, min_price,
            mfe_pct, mae_pct, outcome_label, signal_quality, labeled_at, is_clustered
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            outcome_id,
            snapshot_id,
            "XAUUSD",
            snapshot_time,
            888,
            snapshot_time,
            3300.0,
            18.0,
            0.0,
            3310.0,
            3290.0,
            mfe_pct,
            mae_pct,
            outcome_label,
            "neutral",
            snapshot_time,
            0,
        ),
    )


def _insert_snapshot(conn, snapshot_id: int, snapshot_time: str, signal_side: str = "long"):
    conn.execute(
        """
        INSERT INTO market_snapshots (
            id, snapshot_time, symbol, latest_price, spread_points, has_live_quote, tone,
            trade_grade, trade_grade_source, alert_state_text, event_risk_mode_text,
            event_active_name, event_importance_text, event_note, signal_side,
            regime_tag, regime_text, feature_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            snapshot_time,
            "XAUUSD",
            3300.0,
            18.0,
            1,
            "warning",
            "可轻仓试仓",
            "structure",
            "结构候选",
            "正常观察",
            "",
            "",
            "",
            signal_side,
            "trend_expansion",
            "趋势扩张",
            json_text := '{"summary_text":"高波趋势扩张测试样本","risk_reward_state_text":"盈亏比优秀","intraday_bias_text":"偏多","multi_timeframe_bias_text":"偏多","breakout_state_text":"上破已确认","retest_state_text":"回踩已确认","atr14":18.0,"atr14_h4":42.0}',
            snapshot_time,
        ),
    )
    return json_text


def _insert_outcome(conn, outcome_id: int, snapshot_id: int, snapshot_time: str, horizon_min: int, outcome_label: str):
    conn.execute(
        """
        INSERT INTO snapshot_outcomes (
            id, snapshot_id, symbol, snapshot_time, horizon_min, future_snapshot_time,
            future_price, future_spread_points, price_change_pct, max_price, min_price,
            mfe_pct, mae_pct, outcome_label, signal_quality, labeled_at, is_clustered
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            outcome_id,
            snapshot_id,
            "XAUUSD",
            snapshot_time,
            horizon_min,
            snapshot_time,
            3300.0,
            18.0,
            0.0,
            3310.0,
            3290.0,
            0.03 if outcome_label == "success" else 0.01,
            0.01 if outcome_label == "success" else 0.03,
            outcome_label,
            "neutral",
            snapshot_time,
            0,
        ),
    )


def test_run_llm_batch_reflection_marks_non_tail_records_clustered(monkeypatch, tmp_path):
    db_path = tmp_path / "knowledge.db"
    init_knowledge_base(db_path=db_path)

    with open_knowledge_connection(db_path) as conn:
        _insert_snapshot_pair(conn, 1, 1, "2026-04-16 10:00:00", "fail", 0.01, 0.03)
        _insert_snapshot_pair(conn, 2, 2, "2026-04-16 10:10:00", "fail", 0.01, 0.04)
        _insert_snapshot_pair(conn, 3, 3, "2026-04-16 10:20:00", "mixed", 0.02, 0.02)
        _insert_snapshot_pair(conn, 4, 4, "2026-04-16 10:30:00", "success", 0.04, 0.02)
        _insert_snapshot_pair(conn, 5, 5, "2026-04-16 10:40:00", "fail", 0.01, 0.03)
        _insert_snapshot_pair(conn, 6, 6, "2026-04-16 10:50:00", "fail", 0.01, 0.03)

    monkeypatch.setattr("knowledge_miner._post_json_to_llm", lambda *args, **kwargs: [])

    result = run_llm_batch_reflection(
        db_path,
        SimpleNamespace(
            ai_api_key="test-key",
            ai_api_base="https://example.com/v1",
            ai_model="test-model",
        ),
    )

    assert result["mined_patterns"] == 0
    assert result["inserted_rules"] == 0

    with open_knowledge_connection(db_path) as conn:
        clustered_rows = conn.execute(
            "SELECT id, is_clustered FROM snapshot_outcomes ORDER BY id ASC"
        ).fetchall()

    status = {int(row["id"]): int(row["is_clustered"]) for row in clustered_rows}
    assert status[1] == 1
    assert status[2] == 1
    assert status[3] == 1
    assert status[4] == 1
    assert status[5] == 0
    assert status[6] == 0


def test_run_llm_batch_reflection_uses_real_newlines_in_cluster_prompt(monkeypatch, tmp_path):
    db_path = tmp_path / "knowledge.db"
    init_knowledge_base(db_path=db_path)

    with open_knowledge_connection(db_path) as conn:
        _insert_snapshot_pair(conn, 1, 1, "2026-04-16 10:00:00", "fail", 0.01, 0.03)
        _insert_snapshot_pair(conn, 2, 2, "2026-04-16 10:10:00", "fail", 0.01, 0.04)
        _insert_snapshot_pair(conn, 3, 3, "2026-04-16 10:20:00", "fail", 0.01, 0.05)

    captured = {}

    def fake_post(api_base, api_key, model, system_prompt, user_content):
        captured["system_prompt"] = system_prompt
        return []

    monkeypatch.setattr("knowledge_miner._post_json_to_llm", fake_post)
    monkeypatch.setattr(
        "knowledge_miner.PROMPT_LLM_CLUSTER_LOSS",
        "regime={regime_tag}\nsymbol={symbol}\ncount={count}\n{transactions_text}",
    )

    run_llm_batch_reflection(
        db_path,
        SimpleNamespace(
            ai_api_key="test-key",
            ai_api_base="https://example.com/v1",
            ai_model="test-model",
        ),
    )

    system_prompt = captured["system_prompt"]
    assert "\n---\n" in system_prompt
    assert "\\n---\\n" not in system_prompt
    assert "\nFeatures: " in system_prompt
    assert "\nMarket: " in system_prompt


def test_run_llm_batch_reflection_reuses_same_source_id_within_batch(monkeypatch, tmp_path):
    db_path = tmp_path / "knowledge.db"
    init_knowledge_base(db_path=db_path)

    with open_knowledge_connection(db_path) as conn:
        _insert_snapshot_pair(conn, 1, 1, "2026-04-16 10:00:00", "fail", 0.01, 0.03)
        _insert_snapshot_pair(conn, 2, 2, "2026-04-16 10:10:00", "fail", 0.01, 0.04)
        _insert_snapshot_pair(conn, 3, 3, "2026-04-16 10:20:00", "fail", 0.01, 0.05)

    monkeypatch.setattr(
        "knowledge_miner._post_json_to_llm",
        lambda *args, **kwargs: [
            {"category": "entry", "asset_scope": "XAUUSD", "rule_text": "规则一"},
            {"category": "entry", "asset_scope": "XAUUSD", "rule_text": "规则二"},
        ],
    )
    upsert_calls = {"count": 0}
    real_upsert = __import__("knowledge_miner").upsert_source

    def fake_upsert(*args, **kwargs):
        upsert_calls["count"] += 1
        return real_upsert(*args, **kwargs)

    monkeypatch.setattr("knowledge_miner.upsert_source", fake_upsert)

    result = run_llm_batch_reflection(
        db_path,
        SimpleNamespace(
            ai_api_key="test-key",
            ai_api_base="https://example.com/v1",
            ai_model="test-model",
        ),
    )

    assert result["inserted_rules"] == 2
    assert upsert_calls["count"] == 1


def test_run_llm_batch_reflection_falls_back_to_30m_when_no_sim_trade(monkeypatch, tmp_path):
    db_path = tmp_path / "knowledge.db"
    init_knowledge_base(db_path=db_path)

    with open_knowledge_connection(db_path) as conn:
        _insert_snapshot(conn, 1, "2026-04-16 10:00:00", signal_side="long")
        _insert_snapshot(conn, 2, "2026-04-16 10:10:00", signal_side="long")
        _insert_outcome(conn, 1, 1, "2026-04-16 10:00:00", 30, "success")
        _insert_outcome(conn, 2, 2, "2026-04-16 10:10:00", 30, "success")

    monkeypatch.setattr(
        "knowledge_miner._post_json_to_llm",
        lambda *args, **kwargs: [
            {"category": "entry", "asset_scope": "XAUUSD", "rule_text": "30分钟轻量反思：回踩企稳后再做多"}
        ],
    )

    result = run_llm_batch_reflection(
        db_path,
        SimpleNamespace(
            ai_api_key="test-key",
            ai_api_base="https://example.com/v1",
            ai_model="test-model",
        ),
    )

    assert result["reflection_horizon"] == 30
    assert result["inserted_rules"] >= 1

    with open_knowledge_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT ks.source_type, ks.location, kr.rule_text
            FROM knowledge_rules kr
            JOIN knowledge_sources ks ON ks.id = kr.source_id
            WHERE kr.rule_text = ?
            """,
            ("30分钟轻量反思：回踩企稳后再做多",),
        ).fetchone()

    assert row is not None
    assert row["source_type"] == "llm_golden_setup"
    assert row["location"] == "auto_miner_v2_llm_fallback_30m"


def test_run_llm_batch_reflection_prefers_888_over_30m(monkeypatch, tmp_path):
    db_path = tmp_path / "knowledge.db"
    init_knowledge_base(db_path=db_path)

    with open_knowledge_connection(db_path) as conn:
        _insert_snapshot(conn, 1, "2026-04-16 10:00:00", signal_side="long")
        _insert_snapshot(conn, 2, "2026-04-16 10:10:00", signal_side="long")
        _insert_outcome(conn, 1, 1, "2026-04-16 10:00:00", 30, "success")
        _insert_outcome(conn, 2, 2, "2026-04-16 10:10:00", 888, "success")

    monkeypatch.setattr(
        "knowledge_miner._post_json_to_llm",
        lambda *args, **kwargs: [
            {"category": "entry", "asset_scope": "XAUUSD", "rule_text": "优先使用888反思"}
        ],
    )

    result = run_llm_batch_reflection(
        db_path,
        SimpleNamespace(
            ai_api_key="test-key",
            ai_api_base="https://example.com/v1",
            ai_model="test-model",
        ),
    )

    assert result["reflection_horizon"] == 888

    with open_knowledge_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT ks.location
            FROM knowledge_rules kr
            JOIN knowledge_sources ks ON ks.id = kr.source_id
            WHERE kr.rule_text = ?
            """,
            ("优先使用888反思",),
        ).fetchone()

    assert row is not None
    assert row["location"] == "auto_miner_v2_llm_sim"


def test_run_llm_batch_reflection_limits_and_filters_30m_rules(monkeypatch, tmp_path):
    db_path = tmp_path / "knowledge.db"
    init_knowledge_base(db_path=db_path)

    with open_knowledge_connection(db_path) as conn:
        _insert_snapshot(conn, 1, "2026-04-16 10:00:00", signal_side="long")
        _insert_snapshot(conn, 2, "2026-04-16 10:10:00", signal_side="long")
        _insert_outcome(conn, 1, 1, "2026-04-16 10:00:00", 30, "success")
        _insert_outcome(conn, 2, 2, "2026-04-16 10:10:00", 30, "success")

    monkeypatch.setattr(
        "knowledge_miner._post_json_to_llm",
        lambda *args, **kwargs: [
            {"category": "entry", "asset_scope": "XAUUSD", "rule_text": "回踩企稳后顺势做多"},
            {"category": "entry", "asset_scope": "XAUUSD", "rule_text": "回踩企稳后顺势做多"},
            {"category": "trend", "asset_scope": "XAUUSD", "rule_text": "突破后等待回踩确认，不追第一次突破"},
            {"category": "general", "asset_scope": "XAUUSD", "rule_text": "保持耐心，尊重市场"},
        ],
    )

    result = run_llm_batch_reflection(
        db_path,
        SimpleNamespace(
            ai_api_key="test-key",
            ai_api_base="https://example.com/v1",
            ai_model="test-model",
        ),
    )

    assert result["reflection_horizon"] == 30
    assert result["inserted_rules"] == 2
    assert result["quality_filtered_count"] == 2
    assert result["duplicate_skipped_count"] == 4
    assert result["duplicate_in_batch_count"] == 4
    assert result["duplicate_existing_count"] == 0

    with open_knowledge_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT rule_text
            FROM knowledge_rules
            WHERE rule_text IN ('回踩企稳后顺势做多', '突破后等待回踩确认，不追第一次突破', '保持耐心，尊重市场')
            ORDER BY id ASC
            """
        ).fetchall()

    texts = [row["rule_text"] for row in rows]
    assert "回踩企稳后顺势做多" in texts
    assert "突破后等待回踩确认，不追第一次突破" in texts
    assert "保持耐心，尊重市场" not in texts


def test_run_llm_batch_reflection_skips_existing_rule_text(monkeypatch, tmp_path):
    db_path = tmp_path / "knowledge.db"
    init_knowledge_base(db_path=db_path)

    with open_knowledge_connection(db_path) as conn:
        _insert_snapshot(conn, 1, "2026-04-16 10:00:00", signal_side="long")
        _insert_snapshot(conn, 2, "2026-04-16 10:10:00", signal_side="long")
        _insert_outcome(conn, 1, 1, "2026-04-16 10:00:00", 30, "success")
        _insert_outcome(conn, 2, 2, "2026-04-16 10:10:00", 30, "success")

    source_id = upsert_source(
        title="已有自动规则",
        source_type="auto_miner",
        location="auto_miner_existing",
        db_path=db_path,
    )
    with open_knowledge_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO knowledge_rules (
                source_id, document_id, section_title, category, asset_scope,
                rule_text, confidence, evidence_type, tags_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                None,
                "测试",
                "entry",
                "XAUUSD",
                "回踩企稳后顺势做多",
                "pending",
                "测试",
                "[]",
                "2026-04-16 10:20:00",
            ),
        )

    monkeypatch.setattr(
        "knowledge_miner._post_json_to_llm",
        lambda *args, **kwargs: [
            {"category": "entry", "asset_scope": "XAUUSD", "rule_text": "回踩企稳后顺势做多"}
        ],
    )

    result = run_llm_batch_reflection(
        db_path,
        SimpleNamespace(
            ai_api_key="test-key",
            ai_api_base="https://example.com/v1",
            ai_model="test-model",
        ),
    )

    assert result["reflection_horizon"] == 30
    assert result["inserted_rules"] == 0
    assert result["duplicate_skipped_count"] == 2
    assert result["duplicate_in_batch_count"] == 1
    assert result["duplicate_existing_count"] == 1
    assert result["quality_filtered_count"] == 0


def test_mine_frequent_patterns_prefers_real_trade_horizon_over_30m_label(tmp_path):
    db_path = tmp_path / "knowledge.db"
    init_knowledge_base(db_path=db_path)

    with open_knowledge_connection(db_path) as conn:
        _insert_snapshot(conn, 1, "2026-04-16 10:00:00")
        _insert_snapshot(conn, 2, "2026-04-16 10:10:00")
        _insert_snapshot(conn, 3, "2026-04-16 10:20:00")
        _insert_outcome(conn, 1, 1, "2026-04-16 10:00:00", 30, "success")
        _insert_outcome(conn, 2, 1, "2026-04-16 10:00:00", 888, "fail")
        _insert_outcome(conn, 3, 2, "2026-04-16 10:10:00", 30, "success")
        _insert_outcome(conn, 4, 2, "2026-04-16 10:10:00", 888, "fail")
        _insert_outcome(conn, 5, 3, "2026-04-16 10:20:00", 30, "success")

    result = mine_frequent_patterns(
        db_path=db_path,
        min_samples=3,
        min_win_rate=0.6,
        max_outcomes=20,
    )

    assert result["inserted_rules"] == 0


def test_mine_frequent_patterns_only_uses_recent_window(tmp_path):
    db_path = tmp_path / "knowledge.db"
    init_knowledge_base(db_path=db_path)

    with open_knowledge_connection(db_path) as conn:
        for idx in range(1, 5):
            snapshot_time = f"2026-04-16 10:{idx:02d}:00"
            _insert_snapshot(conn, idx, snapshot_time)
            _insert_outcome(conn, idx, idx, snapshot_time, 888, "success")
        for idx in range(5, 8):
            snapshot_time = f"2026-04-16 11:{idx:02d}:00"
            _insert_snapshot(conn, idx, snapshot_time)
            _insert_outcome(conn, idx, idx, snapshot_time, 888, "fail")

    result = mine_frequent_patterns(
        db_path=db_path,
        min_samples=3,
        min_win_rate=0.6,
        max_outcomes=3,
    )

    assert result["inserted_rules"] == 0
