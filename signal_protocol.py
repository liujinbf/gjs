"""AI 结构化信号协议：对外保留旧接口，内部复用统一交易契约。"""
from __future__ import annotations

from trade_contracts import (
    SIGNAL_SCHEMA_VERSION,
    VALID_ACTIONS,
    StrategySignal,
    build_empty_signal_meta,
    normalize_signal_meta,
    validate_signal_meta,
)

__all__ = [
    "SIGNAL_SCHEMA_VERSION",
    "VALID_ACTIONS",
    "StrategySignal",
    "build_empty_signal_meta",
    "normalize_signal_meta",
    "validate_signal_meta",
]
