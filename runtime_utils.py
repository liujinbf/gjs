"""
runtime_utils.py - 运行时公共工具函数。

P-004 修复：将多个模块中重复定义的 _parse_time 提取到此处，
统一维护，避免三份相同代码导致修改不一致的风险。
"""
from __future__ import annotations

from datetime import datetime

_SUPPORTED_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_time(value: str) -> datetime | None:
    """将字符串解析为 datetime，支持 'YYYY-MM-DD HH:MM:SS' / 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD'。
    解析失败时返回 None（而非抛出异常）。
    """
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _SUPPORTED_FMTS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
