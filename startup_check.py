"""个人工作台启动自检。

自检只做本地、只读、低风险判断：不发通知、不请求 AI、不发送订单。
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from app_config import MetalMonitorConfig, get_runtime_config


CHECK_OK = "ok"
CHECK_WARN = "warn"
CHECK_FAIL = "fail"
CHECK_SKIP = "skip"


@dataclass(frozen=True)
class StartupCheckItem:
    key: str
    title: str
    status: str
    detail: str
    action: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _status_rank(status: str) -> int:
    return {
        CHECK_FAIL: 3,
        CHECK_WARN: 2,
        CHECK_SKIP: 1,
        CHECK_OK: 0,
    }.get(_normalize_text(status).lower(), 0)


def _overall_status(items: list[StartupCheckItem]) -> str:
    if any(item.status == CHECK_FAIL for item in items):
        return CHECK_FAIL
    if any(item.status == CHECK_WARN for item in items):
        return CHECK_WARN
    if items and all(item.status == CHECK_SKIP for item in items):
        return CHECK_SKIP
    return CHECK_OK


def _check_config(config: MetalMonitorConfig) -> list[StartupCheckItem]:
    items: list[StartupCheckItem] = []
    symbols = [str(item or "").strip().upper() for item in list(config.symbols or []) if str(item or "").strip()]
    if symbols:
        items.append(
            StartupCheckItem(
                key="config.symbols",
                title="监控品种",
                status=CHECK_OK,
                detail=f"已配置 {len(symbols)} 个品种：{', '.join(symbols[:6])}",
            )
        )
    else:
        items.append(
            StartupCheckItem(
                key="config.symbols",
                title="监控品种",
                status=CHECK_FAIL,
                detail="当前没有可监控品种。",
                action="在设置里至少配置一个 MT5 品种，例如 XAUUSD。",
            )
        )

    if int(config.refresh_interval_sec or 0) < 10:
        items.append(
            StartupCheckItem(
                key="config.refresh_interval",
                title="刷新频率",
                status=CHECK_WARN,
                detail=f"当前刷新间隔 {int(config.refresh_interval_sec or 0)} 秒，个人工作台可能过于频繁。",
                action="建议保持 10 秒以上，减少 MT5 和 UI 压力。",
            )
        )
    else:
        items.append(
            StartupCheckItem(
                key="config.refresh_interval",
                title="刷新频率",
                status=CHECK_OK,
                detail=f"刷新间隔 {int(config.refresh_interval_sec)} 秒。",
            )
        )
    return items


def _check_ai(config: MetalMonitorConfig) -> StartupCheckItem:
    api_key = _normalize_text(config.ai_api_key)
    model = _normalize_text(config.ai_model)
    api_base = _normalize_text(config.ai_api_base)
    if api_key and model and api_base:
        return StartupCheckItem(
            key="ai.config",
            title="AI 研判",
            status=CHECK_OK,
            detail=f"AI 已配置：{model}",
        )
    if bool(getattr(config, "ai_push_enabled", False)):
        return StartupCheckItem(
            key="ai.config",
            title="AI 研判",
            status=CHECK_WARN,
            detail="AI 推送已开启，但 AI Key、模型或 API 地址不完整。",
            action="补齐 AI 配置，或关闭 AI 推送。",
        )
    return StartupCheckItem(
        key="ai.config",
        title="AI 研判",
        status=CHECK_SKIP,
        detail="AI 未配置；本地规则和监控仍可使用。",
        action="需要手动/自动 AI 研判时再补充 AI Key。",
    )


def _check_notification(config: MetalMonitorConfig) -> StartupCheckItem:
    channels = []
    if _normalize_text(config.dingtalk_webhook):
        channels.append("钉钉")
    if _normalize_text(config.pushplus_token):
        channels.append("PushPlus")
    if channels:
        return StartupCheckItem(
            key="notification.channels",
            title="推送渠道",
            status=CHECK_OK,
            detail=f"已配置：{', '.join(channels)}；冷却 {int(config.notify_cooldown_min)} 分钟。",
        )
    return StartupCheckItem(
        key="notification.channels",
        title="推送渠道",
        status=CHECK_WARN,
        detail="未配置钉钉或 PushPlus，关键提醒只会显示在本地。",
        action="如果需要离屏提醒，至少配置一个推送渠道。",
    )


def _check_live_safety(config: MetalMonitorConfig) -> StartupCheckItem:
    trade_mode = _normalize_text(config.trade_mode).lower() or "simulation"
    if trade_mode != "live":
        return StartupCheckItem(
            key="trade.live_safety",
            title="实盘模式",
            status=CHECK_OK,
            detail="当前为模拟/监控模式，不会发送真实订单。",
        )

    if bool(getattr(config, "live_order_precheck_only", True)):
        return StartupCheckItem(
            key="trade.live_safety",
            title="实盘模式",
            status=CHECK_WARN,
            detail=(
                "当前是 live，但仍处于预检模式；系统只会 order_check，不会 order_send。"
                f"持仓上限 {int(config.live_max_open_positions)}，日订单上限 {int(config.live_max_orders_per_day)}。"
            ),
            action="确认账户、品种合约和止损金额后，再考虑关闭预检模式。",
        )
    return StartupCheckItem(
        key="trade.live_safety",
        title="实盘模式",
        status=CHECK_FAIL,
        detail=(
            "当前 live 且 LIVE_ORDER_PRECHECK_ONLY=0，系统具备发送真实订单能力。"
            f"最大日内回撤 {float(config.live_max_drawdown_pct):.2%}。"
        ),
        action="启动前请确认 MT5 账户、手数、止损、最大亏损和网络状态。",
    )


def _check_broker_symbol_map() -> StartupCheckItem:
    raw_text = _normalize_text(os.getenv("BROKER_SYMBOL_MAP_JSON", ""))
    if not raw_text:
        return StartupCheckItem(
            key="broker.symbol_map",
            title="券商品种映射",
            status=CHECK_SKIP,
            detail="未配置券商品种映射，内部品种名会直接发送给 MT5。",
            action="如果券商使用 GOLD、XAUUSDm 等后缀品种，可配置 BROKER_SYMBOL_MAP_JSON。",
        )
    from broker_gateway import load_broker_symbol_map

    mapping = load_broker_symbol_map(raw_text)
    if not mapping:
        return StartupCheckItem(
            key="broker.symbol_map",
            title="券商品种映射",
            status=CHECK_WARN,
            detail="BROKER_SYMBOL_MAP_JSON 已填写，但无法解析出有效映射。",
            action='示例：{"XAUUSD":"GOLD","EURUSD":"EURUSDm"}',
        )
    preview = ", ".join(f"{key}->{value}" for key, value in list(mapping.items())[:5])
    return StartupCheckItem(
        key="broker.symbol_map",
        title="券商品种映射",
        status=CHECK_OK,
        detail=f"已配置 {len(mapping)} 个映射：{preview}",
    )


def _check_runtime_paths(runtime_dir: Path | None = None) -> StartupCheckItem:
    target = Path(runtime_dir) if runtime_dir else Path(__file__).resolve().parent / ".runtime"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".startup_check_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        return StartupCheckItem(
            key="runtime.path",
            title="运行目录",
            status=CHECK_FAIL,
            detail=f"运行目录不可写：{target}；{exc}",
            action="检查目录权限，或把项目放到可写路径。",
        )
    return StartupCheckItem(
        key="runtime.path",
        title="运行目录",
        status=CHECK_OK,
        detail=f"运行目录可写：{target}",
    )


def _check_knowledge_db(summary_loader: Callable[[], dict] | None = None) -> StartupCheckItem:
    try:
        if summary_loader is None:
            from knowledge_base import summarize_knowledge_base

            summary = summarize_knowledge_base()
        else:
            summary = summary_loader()
    except Exception as exc:  # noqa: BLE001
        return StartupCheckItem(
            key="knowledge.db",
            title="知识库",
            status=CHECK_FAIL,
            detail=f"知识库初始化或读取失败：{exc}",
            action="检查 .runtime/knowledge_base.db 是否被占用或损坏。",
        )
    rule_count = int((summary or {}).get("rule_count", 0) or 0)
    if rule_count <= 0:
        return StartupCheckItem(
            key="knowledge.db",
            title="知识库",
            status=CHECK_WARN,
            detail="知识库可访问，但暂未加载候选规则。",
            action="运行知识库导入/种子流程，或检查 knowledge_docs。",
        )
    return StartupCheckItem(
        key="knowledge.db",
        title="知识库",
        status=CHECK_OK,
        detail=str((summary or {}).get("summary_text", "") or f"知识库可访问，候选规则 {rule_count} 条。"),
    )


def _check_mt5(
    config: MetalMonitorConfig,
    mt5_probe: Callable[[list[str]], tuple[bool, str]] | None = None,
) -> StartupCheckItem:
    symbols = [str(item or "").strip().upper() for item in list(config.symbols or []) if str(item or "").strip()]
    if mt5_probe is None:
        try:
            from mt5_gateway import HAS_MT5, initialize_connection
        except Exception as exc:  # noqa: BLE001
            return StartupCheckItem(
                key="mt5.connection",
                title="MT5 连接",
                status=CHECK_FAIL,
                detail=f"MT5 模块导入失败：{exc}",
                action="确认已安装 MetaTrader5 Python 库。",
            )
        if not HAS_MT5:
            return StartupCheckItem(
                key="mt5.connection",
                title="MT5 连接",
                status=CHECK_FAIL,
                detail="未安装 MetaTrader5 Python 库。",
                action="运行 pip install -r requirements.txt，并确认 MetaTrader5 可用。",
            )
        mt5_probe = lambda _symbols: initialize_connection()

    try:
        ok, message = mt5_probe(symbols)
    except Exception as exc:  # noqa: BLE001
        return StartupCheckItem(
            key="mt5.connection",
            title="MT5 连接",
            status=CHECK_FAIL,
            detail=f"MT5 探测异常：{exc}",
            action="确认 MT5 客户端已启动并登录。",
        )
    if ok:
        return StartupCheckItem(
            key="mt5.connection",
            title="MT5 连接",
            status=CHECK_OK,
            detail=_normalize_text(message) or "MT5 连接正常。",
        )
    return StartupCheckItem(
        key="mt5.connection",
        title="MT5 连接",
        status=CHECK_FAIL,
        detail=_normalize_text(message) or "MT5 连接失败。",
        action="启动并登录 MT5，检查 MT5_PATH / 账号 / 服务器配置。",
    )


def run_startup_check(
    *,
    config: MetalMonitorConfig | None = None,
    mt5_probe: Callable[[list[str]], tuple[bool, str]] | None = None,
    knowledge_summary_loader: Callable[[], dict] | None = None,
    runtime_dir: Path | str | None = None,
) -> dict:
    cfg = config or get_runtime_config()
    items: list[StartupCheckItem] = []
    items.extend(_check_config(cfg))
    items.append(_check_broker_symbol_map())
    items.append(_check_runtime_paths(Path(runtime_dir) if runtime_dir else None))
    items.append(_check_knowledge_db(knowledge_summary_loader))
    items.append(_check_mt5(cfg, mt5_probe=mt5_probe))
    items.append(_check_notification(cfg))
    items.append(_check_ai(cfg))
    items.append(_check_live_safety(cfg))
    items.sort(key=lambda item: (-_status_rank(item.status), item.key))
    overall = _overall_status(items)
    counts = {
        status: sum(1 for item in items if item.status == status)
        for status in (CHECK_FAIL, CHECK_WARN, CHECK_SKIP, CHECK_OK)
    }
    return {
        "overall_status": overall,
        "counts": counts,
        "items": [item.to_dict() for item in items],
        "summary_text": _build_summary_text(overall, counts),
    }


def _build_summary_text(overall: str, counts: dict[str, int]) -> str:
    label = {
        CHECK_OK: "启动自检通过",
        CHECK_WARN: "启动自检有提醒",
        CHECK_FAIL: "启动自检发现阻断项",
        CHECK_SKIP: "启动自检已跳过",
    }.get(overall, "启动自检完成")
    return (
        f"{label}：失败 {int(counts.get(CHECK_FAIL, 0))} 项，"
        f"提醒 {int(counts.get(CHECK_WARN, 0))} 项，"
        f"跳过 {int(counts.get(CHECK_SKIP, 0))} 项，"
        f"正常 {int(counts.get(CHECK_OK, 0))} 项。"
    )
