import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_history import (
    append_ai_history_entry,
    build_ai_history_entry,
    read_recent_ai_history,
    summarize_recent_ai_history,
)
import ai_history


def test_build_ai_history_entry_keeps_summary_and_push_state():
    entry = build_ai_history_entry(
        {
            "model": "deepseek-chat",
            "content": "当前结论：只适合观察。\n方向判断：黄金偏强。\n风险点：点差可能放大。\n行动建议：等待回踩。",
            "signal_meta": {"symbol": "XAUUSD", "action": "neutral", "price": 0, "sl": 0, "tp": 0},
            "rulebook_summary_text": "当前优先遵守 1 条已验证规则。",
        },
        {
            "last_refresh_text": "2026-04-12 18:20:00",
            "status_hint": "MT5 连接正常。",
            "alert_text": "贵金属提醒：非农前先盯点差。",
            "items": [{"symbol": "XAUUSD"}],
        },
        push_result={"messages": ["AI 研判已推送到钉钉"], "errors": []},
    )
    assert entry["summary_line"] == "当前结论：只适合观察。"
    assert entry["push_sent"] is True
    assert entry["symbols"] == ["XAUUSD"]
    assert entry["signal_meta"]["symbol"] == "XAUUSD"
    assert entry["rulebook_summary_text"] == "当前优先遵守 1 条已验证规则。"


def test_append_and_summarize_ai_history():
    history_dir = ROOT / ".runtime_test_ai_history"
    if history_dir.exists():
        shutil.rmtree(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "ai_brief_history.jsonl"

    assert append_ai_history_entry(
        {
            "occurred_at": "2026-04-12 18:00:00",
            "model": "deepseek-chat",
            "summary_line": "方向判断：黄金偏强。",
            "content": "方向判断：黄金偏强。",
            "push_sent": True,
            "signature": "ai-1",
        },
        history_file=history_file,
    ) == 1
    assert append_ai_history_entry(
        {
            "occurred_at": "2026-04-12 18:05:00",
            "model": "deepseek-chat",
            "summary_line": "方向判断：黄金偏强。",
            "content": "方向判断：黄金偏强。",
            "push_sent": True,
            "signature": "ai-1",
        },
        history_file=history_file,
    ) == 0

    recent = read_recent_ai_history(limit=2, history_file=history_file)
    assert len(recent) == 1
    assert recent[0]["summary_line"] == "方向判断：黄金偏强。"

    stats = summarize_recent_ai_history(
        days=7,
        history_file=history_file,
        now=datetime(2026, 4, 12, 19, 0, 0),
    )
    assert stats["total_count"] == 1
    assert stats["push_count"] == 1
    assert stats["latest_model"] == "deepseek-chat"
    assert "最近 7 天共记录 1 次 AI 研判" in stats["summary_text"]

    shutil.rmtree(history_dir)


def test_ai_history_trim_uses_atomic_replace(monkeypatch, tmp_path):
    history_file = tmp_path / "ai_brief_history.jsonl"
    replaced = {"called": False}
    original_limit = ai_history.MAX_AI_HISTORY_LINES
    original_replace = Path.replace

    def spy_replace(self, target):
        if str(self).endswith(".tmp"):
            replaced["called"] = True
        return original_replace(self, target)

    monkeypatch.setattr(ai_history, "MAX_AI_HISTORY_LINES", 2)
    monkeypatch.setattr(Path, "replace", spy_replace)
    try:
        for index in range(3):
            assert append_ai_history_entry(
                {
                    "occurred_at": f"2026-04-12 18:0{index}:00",
                    "model": "deepseek-chat",
                    "summary_line": f"方向判断：{index}",
                    "content": f"方向判断：{index}",
                    "push_sent": False,
                    "signature": f"ai-{index}",
                },
                history_file=history_file,
            ) == 1
    finally:
        monkeypatch.setattr(ai_history, "MAX_AI_HISTORY_LINES", original_limit)

    assert replaced["called"] is True
    lines = [line for line in history_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
