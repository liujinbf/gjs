"""
交易执行契约：统一信号、风控决策、下单意图和执行结果的字段边界。

这一层先保持纯 Python、无外部依赖，方便被 AI 信号、规则桥接、模拟盘和实盘共用。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SIGNAL_SCHEMA_VERSION = "signal-meta-v1"
VALID_ACTIONS = {"long", "short", "neutral"}
VALID_TRADE_MODES = {"simulation", "live"}
VALID_EXECUTION_PROFILES = {"standard", "exploratory"}


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return int(default)


@dataclass(frozen=True)
class StrategySignal:
    """策略层输出的标准信号，只表达方向与点位，不表达是否允许执行。"""

    symbol: str = "--"
    action: str = "neutral"
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    tp2: float = 0.0
    schema_version: str = SIGNAL_SCHEMA_VERSION
    source_kind: str = ""
    strategy_family: str = ""
    execution_profile: str = "standard"
    confidence: float = 0.0
    snapshot_id: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict | None, *, default_symbol: str = "--") -> "StrategySignal":
        data = dict(payload or {})
        symbol = _normalize_text(data.get("symbol", default_symbol)).upper() or "--"
        action = _normalize_text(data.get("action", "neutral")).lower() or "neutral"
        if action not in VALID_ACTIONS:
            action = "neutral"

        execution_profile = _normalize_text(data.get("execution_profile", "standard")).lower() or "standard"
        if execution_profile not in VALID_EXECUTION_PROFILES:
            execution_profile = "standard"

        strategy_family = _normalize_text(
            data.get("strategy_family", "")
            or data.get("setup_kind", "")
            or data.get("trade_grade_source", "")
        )
        source_kind = _normalize_text(data.get("source_kind", "") or data.get("trade_grade_source", "") or strategy_family)
        tp2 = data.get("tp2", data.get("take_profit_2", data.get("target_2", 0.0)))
        known_keys = {
            "symbol",
            "action",
            "price",
            "sl",
            "tp",
            "tp2",
            "take_profit_2",
            "target_2",
            "schema_version",
            "source_kind",
            "strategy_family",
            "execution_profile",
            "confidence",
            "snapshot_id",
        }
        extra = {str(key): value for key, value in data.items() if key not in known_keys}
        if action == "neutral":
            return cls(symbol=symbol)
        return cls(
            symbol=symbol,
            action=action,
            price=_to_float(data.get("price", 0.0)),
            sl=_to_float(data.get("sl", 0.0)),
            tp=_to_float(data.get("tp", 0.0)),
            tp2=_to_float(tp2),
            schema_version=_normalize_text(data.get("schema_version", SIGNAL_SCHEMA_VERSION)) or SIGNAL_SCHEMA_VERSION,
            source_kind=source_kind,
            strategy_family=strategy_family,
            execution_profile=execution_profile,
            confidence=max(0.0, min(1.0, _to_float(data.get("confidence", 0.0)))),
            snapshot_id=max(0, _to_int(data.get("snapshot_id", 0))),
            extra=extra,
        )

    def to_signal_meta(self, *, include_extra: bool = True) -> dict:
        payload = {
            "symbol": self.symbol,
            "action": self.action,
            "price": float(self.price),
            "sl": float(self.sl),
            "tp": float(self.tp),
        }
        if self.action != "neutral":
            if self.tp2 > 0:
                payload["tp2"] = float(self.tp2)
            if self.source_kind:
                payload["source_kind"] = self.source_kind
            if self.strategy_family:
                payload["strategy_family"] = self.strategy_family
            if self.execution_profile != "standard":
                payload["execution_profile"] = self.execution_profile
            if self.confidence > 0:
                payload["confidence"] = float(self.confidence)
            if self.snapshot_id > 0:
                payload["snapshot_id"] = int(self.snapshot_id)
            if include_extra:
                payload.update(dict(self.extra or {}))
        return payload

    def validate(self) -> tuple[bool, str]:
        if self.action == "neutral":
            return True, "观望信号"
        if not self.symbol or self.symbol == "--":
            return False, "缺少有效品种代码"
        if min(float(self.price), float(self.sl), float(self.tp)) <= 0:
            return False, "缺少有效的入场价/止损价/目标价"
        if self.action == "long":
            if not (self.sl < self.price < self.tp):
                return False, "做多信号要求 止损 < 入场 < 目标"
            return True, "做多信号结构有效"
        if self.action == "short":
            if not (self.tp < self.price < self.sl):
                return False, "做空信号要求 目标 < 入场 < 止损"
            return True, "做空信号结构有效"
        return False, "未知动作类型"


@dataclass(frozen=True)
class RiskDecision:
    """风控层对策略信号的执行裁决。"""

    allowed: bool
    reason: str = ""
    risk_budget_pct: float = 0.0
    sizing_reference_balance: float = 0.0
    block_code: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OrderIntent:
    """执行层可消费的下单意图。"""

    signal: StrategySignal
    trade_mode: str = "simulation"
    volume: float = 0.0
    risk_decision: RiskDecision = field(default_factory=lambda: RiskDecision(allowed=False))
    request_meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        mode = _normalize_text(self.trade_mode).lower() or "simulation"
        if mode not in VALID_TRADE_MODES:
            mode = "simulation"
        object.__setattr__(self, "trade_mode", mode)

    def to_dict(self) -> dict:
        return {
            "signal": self.signal.to_signal_meta(),
            "trade_mode": self.trade_mode,
            "volume": float(self.volume),
            "risk_decision": self.risk_decision.to_dict(),
            "request_meta": dict(self.request_meta or {}),
        }


@dataclass(frozen=True)
class ExecutionResult:
    """执行层返回的标准结果，可用于审计、通知和学习反哺。"""

    ok: bool
    message: str
    trade_mode: str = "simulation"
    order_id: str = ""
    retcode: int = 0
    filled_price: float = 0.0
    volume: float = 0.0
    audit_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def build_empty_signal_meta(symbol: str = "--") -> dict:
    return StrategySignal(symbol=_normalize_text(symbol).upper() or "--").to_signal_meta()


def normalize_signal_meta(meta: dict | None) -> dict:
    return StrategySignal.from_payload(meta).to_signal_meta()


def validate_signal_meta(meta: dict | None) -> tuple[bool, str]:
    return StrategySignal.from_payload(meta).validate()
