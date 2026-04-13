"""
提醒留痕：对外暴露统一的历史构造、存储和统计接口。
"""
from __future__ import annotations

from alert_history_stats import summarize_effectiveness, summarize_recent_history
from alert_history_store import (
    HISTORY_FILE,
    MAX_HISTORY_LINES,
    RUNTIME_DIR,
    append_history_entries,
    build_snapshot_history_entries,
    read_full_history,
    read_recent_history,
)

__all__ = [
    "RUNTIME_DIR",
    "HISTORY_FILE",
    "MAX_HISTORY_LINES",
    "build_snapshot_history_entries",
    "append_history_entries",
    "read_recent_history",
    "read_full_history",
    "summarize_recent_history",
    "summarize_effectiveness",
]
