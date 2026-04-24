"""轻量券商适配工具。

项目内部统一使用标准品种名（如 XAUUSD），券商侧品种名通过映射转换
（如 GOLD、XAUUSDm）。这样以后换 MT5 券商或接入其它券商时，策略层
和提醒层不用跟着改名字。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


SYMBOL_MAP_ENV = "BROKER_SYMBOL_MAP_JSON"


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


@dataclass(frozen=True)
class BrokerSymbol:
    internal: str
    broker: str

    @property
    def is_mapped(self) -> bool:
        return bool(self.internal and self.broker and self.internal != self.broker)

    def to_dict(self) -> dict:
        return {
            "symbol": self.internal,
            "broker_symbol": self.broker,
            "is_mapped": self.is_mapped,
        }


def load_broker_symbol_map(raw_json: str | None = None) -> dict[str, str]:
    """读取内部品种到券商品种的映射。

    环境变量示例：
    BROKER_SYMBOL_MAP_JSON={"XAUUSD":"GOLD","EURUSD":"EURUSDm"}
    """
    payload = raw_json
    if payload is None:
        payload = os.getenv(SYMBOL_MAP_ENV, "")
    text = str(payload or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, str] = {}
    for internal, broker in parsed.items():
        internal_symbol = _normalize_symbol(internal)
        broker_symbol = _normalize_symbol(broker)
        if internal_symbol and broker_symbol:
            result[internal_symbol] = broker_symbol
    return result


def resolve_broker_symbol(symbol: str, symbol_map: dict[str, str] | None = None) -> BrokerSymbol:
    internal = _normalize_symbol(symbol)
    mapping = dict(symbol_map if symbol_map is not None else load_broker_symbol_map())
    broker = _normalize_symbol(mapping.get(internal, internal))
    return BrokerSymbol(internal=internal, broker=broker)


def to_broker_symbol(symbol: str, symbol_map: dict[str, str] | None = None) -> str:
    return resolve_broker_symbol(symbol, symbol_map=symbol_map).broker


def to_internal_symbol(broker_symbol: str, symbol_map: dict[str, str] | None = None) -> str:
    broker = _normalize_symbol(broker_symbol)
    mapping = dict(symbol_map if symbol_map is not None else load_broker_symbol_map())
    reverse = {_normalize_symbol(value): _normalize_symbol(key) for key, value in mapping.items()}
    return reverse.get(broker, broker)


def attach_broker_symbol_meta(payload: dict, symbol: str, symbol_map: dict[str, str] | None = None) -> dict:
    result = dict(payload or {})
    resolved = resolve_broker_symbol(symbol, symbol_map=symbol_map)
    result.update(resolved.to_dict())
    return result
