"""
规则引擎降级简报模块。

当 AI API 不可用时（无网络、密钥失效、接口超时等），
基于本地技术指标、知识库规则和快照数据自动生成结构化研判报告。

输出格式与 request_ai_brief() 完全一致，确保下游的
机器信号解析、推送通知、AI留痕 等逻辑无需改动。

降级简报特征：
  - model 字段返回 "rule-engine-fallback"（用于 UI 区分）
  - is_fallback=True 供 UI 显示特殊警告标记
  - signal_meta.action 始终为 neutral（防止降级模式误触发模拟跟单）
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 基础工具函数
# ──────────────────────────────────────────────

def _sf(v, default: float = 0.0) -> float:
    """Safe float conversion."""
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return default


def _ss(v, default: str = "--") -> str:
    """Safe string conversion."""
    return str(v or "").strip() or default


def _first_item(snapshot: dict) -> dict:
    items = list(snapshot.get("items", []) or [])
    return dict(items[0]) if items else {}


# ──────────────────────────────────────────────
# 各指标描述函数（纯规则，无 AI）
# ──────────────────────────────────────────────

def _fmt_price(p: float) -> str:
    """格式化价格：大于100用2位小数，否则4位。"""
    if p <= 0:
        return "--"
    return f"{p:.2f}" if p >= 100 else f"{p:.4f}"


def _describe_instruction(item: dict) -> tuple[str, str]:
    """
    根据盈亏比 + 多周期方向判定当前指令。
    返回 (emoji, instruction_text)。
    降级模式下不输出 long/short action，一律为 neutral（防套利误操作）。
    """
    rr_ready = bool(item.get("risk_reward_ready", False))
    rr = _sf(item.get("risk_reward_ratio"))
    alignment = _ss(item.get("multi_timeframe_alignment", "unknown")).lower()

    if not rr_ready or rr < 1.0:
        return "🟡", "静默观望"
    if alignment == "bullish" and rr >= 1.5:
        return "🟢", "关注做多机会（降级模式，请人工确认）"
    if alignment == "bearish" and rr >= 1.5:
        return "🔴", "关注做空机会（降级模式，请人工确认）"
    return "🟡", "静默观望"


def _describe_rsi(rsi) -> str:
    if rsi is None:
        return "RSI 数据不足"
    rsi = _sf(rsi)
    if rsi > 70:
        return f"RSI={rsi}（超买区 >70，注意回调风险）"
    if rsi < 30:
        return f"RSI={rsi}（超卖区 <30，关注反弹机会）"
    if 40 <= rsi <= 60:
        return f"RSI={rsi}（中性区 40-60，方向不明）"
    tag = "偏强" if rsi > 50 else "偏弱"
    return f"RSI={rsi}（{tag}中间区）"


def _describe_ma(ma20, ma50) -> str:
    if not ma20 or not ma50:
        return "MA 均线数据不足"
    m20, m50 = _sf(ma20), _sf(ma50)
    if m20 > m50:
        return f"MA20={m20:.2f} > MA50={m50:.2f}（多头排列）"
    return f"MA20={m20:.2f} < MA50={m50:.2f}（空头排列，MA20<MA50）"


def _describe_macd(macd, macd_signal, macd_histogram) -> str:
    if macd is None or macd_signal is None or macd_histogram is None:
        return "MACD 数据不足，无法判断金叉/死叉"
    hist = _sf(macd_histogram)
    state = "金叉偏多" if hist > 0 else "死叉偏空"
    return (
        f"MACD={_sf(macd):.4f} Signal={_sf(macd_signal):.4f} "
        f"Hist={hist:.4f}（{state}）"
    )


def _describe_bollinger(item: dict, price: float) -> str:
    upper = _sf(item.get("bollinger_upper"))
    mid = _sf(item.get("bollinger_mid"))
    lower = _sf(item.get("bollinger_lower"))
    if not all([upper, mid, lower, price]):
        return "布林带数据不足"
    if price >= upper * 0.998:
        pos = "上轨附近（超买区，警惕回调）"
    elif price <= lower * 1.002:
        pos = "下轨附近（超卖区，关注反弹）"
    else:
        pos = "中轨震荡区"
    return (
        f"H1布林带：上={upper:.2f} 中={mid:.2f} 下={lower:.2f}，"
        f"价格位于{pos}"
    )


def _describe_multitf(item: dict) -> str:
    alignment = _ss(item.get("multi_timeframe_alignment", "unknown")).lower()
    bias_text = _ss(item.get("multi_timeframe_bias_text", ""), "")
    ctx_text = _ss(item.get("multi_timeframe_context_text", ""), "")
    m15 = _ss(item.get("m15_context_text", ""), "")
    h1 = _ss(item.get("h1_context_text", ""), "")
    h4 = _ss(item.get("h4_context_text", ""), "")

    detail_parts = [p for p in [
        f"M15:{m15}" if m15 else "",
        f"H1:{h1}" if h1 else "",
        f"H4:{h4}" if h4 else "",
    ] if p]
    detail = " / ".join(detail_parts) if detail_parts else ctx_text

    if alignment == "bullish":
        return f"多周期共振方向一致偏多（{bias_text or detail}），趋势结构支持多方"
    if alignment == "bearish":
        return f"多周期共振方向一致偏空（{bias_text or detail}），趋势结构支持空方"
    return f"多周期共振失效（{detail or '各级别方向不一致'}），胜率低于 50%"


def _describe_rr(item: dict) -> tuple[str, float, float]:
    """
    返回 (rr_text, stop_price, target_price)。
    """
    rr_ready = bool(item.get("risk_reward_ready", False))
    rr = _sf(item.get("risk_reward_ratio"))
    stop = _sf(item.get("risk_reward_stop_price"))
    target = _sf(item.get("risk_reward_target_price"))

    if rr_ready and rr > 0:
        eval_tag = (
            "优质" if rr >= 2.0
            else ("及格" if rr >= 1.3 else "偏低，不建议入场")
        )
        return f"1:{rr:.1f}（{eval_tag}）", stop, target
    return "暂无法计算（方向不明或数据不足）", stop, target


def _get_vix_text(snapshot: dict) -> str:
    for item in list(snapshot.get("macro_data_items", []) or []):
        name = _ss(item.get("name", ""))
        if "VIX" in name:
            val_text = _ss(item.get("value_text", ""), "")
            return f"VIX {val_text}" if val_text else ""
    return ""


def _get_event_text(snapshot: dict) -> str:
    name = _ss(snapshot.get("event_next_name", ""), "")
    time_text = _ss(snapshot.get("event_next_time_text", ""), "")
    if name and time_text:
        return f"下一个关注事件：『{name}』（预计 {time_text} 公布）"
    return "暂无重大宏观窗口，关注盘中价格结构变化。"


def _get_rulebook_text() -> str:
    """从知识库拉取当前有效规则摘要（最多取前 3 条）。"""
    try:
        from knowledge_rulebook import build_rulebook
        rulebook = build_rulebook()
        active = _ss(rulebook.get("active_rules_text", ""), "")
        if active and "暂无" not in active:
            rules = [r.strip() for r in active.split("\n") if r.strip()][:3]
            return "；".join(rules) if rules else "暂无有效规则"
        return "暂无经验证的规则（规则库尚在建立中）"
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"规则库查询失败：{exc}")
        return "规则库查询失败，以指标判断为准"


# ──────────────────────────────────────────────
# 主函数
# ──────────────────────────────────────────────

def generate_rule_engine_brief(snapshot: dict) -> dict:
    """
    基于本地规则引擎生成降级研判简报。

    返回 dict 与 request_ai_brief() 完全兼容：
      {"content": str, "model": str, "api_base": str,
       "rulebook_summary_text": str, "is_fallback": True,
       "fallback_reason": str}

    安全约束：signal_meta.action 始终为 neutral，
    避免降级模式下误触发模拟盘自动跟单。
    """
    item = _first_item(snapshot)
    symbol = _ss(item.get("symbol", "XAUUSD"))
    price = _sf(item.get("latest_price"))
    price_text = _fmt_price(price)

    emoji, instruction = _describe_instruction(item)
    rr_text, stop_price, target_price = _describe_rr(item)

    boll_text = _describe_bollinger(item, price)
    rsi_text = _describe_rsi(item.get("rsi14"))
    ma_text = _describe_ma(item.get("ma20"), item.get("ma50"))
    macd_text = _describe_macd(
        item.get("macd"), item.get("macd_signal"), item.get("macd_histogram")
    )
    multitf_text = _describe_multitf(item)
    h4_summary = _ss(item.get("tech_summary_h4", ""), "")
    vix_text = _get_vix_text(snapshot)
    event_text = _get_event_text(snapshot)
    rule_text = _get_rulebook_text()

    # 布林带止损/目标 fallback（若 R/R 计算完成则用 R/R 数据）
    boll_upper = _sf(item.get("bollinger_upper"))
    boll_lower = _sf(item.get("bollinger_lower"))
    display_sup = _fmt_price(stop_price if stop_price > 0 else boll_lower)
    display_res = _fmt_price(target_price if target_price > 0 else boll_upper)

    dist_sup = (
        f"{abs(price - stop_price):.2f}" if stop_price > 0 and price > 0
        else (f"{abs(price - boll_lower):.2f}" if boll_lower > 0 and price > 0 else "--")
    )
    dist_res = (
        f"{abs(target_price - price):.2f}" if target_price > 0 and price > 0
        else (f"{abs(boll_upper - price):.2f}" if boll_upper > 0 and price > 0 else "--")
    )

    # 执行建议（保守，不给明确进场指令）
    rr_ready = bool(item.get("risk_reward_ready", False))
    rr_val = _sf(item.get("risk_reward_ratio"))
    alignment = _ss(item.get("multi_timeframe_alignment", "unknown")).lower()

    if rr_ready and rr_val >= 1.5 and alignment in ("bullish", "bearish"):
        direction_cn = "多" if alignment == "bullish" else "空"
        exec_text = (
            f"盈亏比 {rr_text}，多周期偏{direction_cn}，结构具备基本入场条件。"
            f"规则引擎建议参考止损 {_fmt_price(stop_price)} / 目标 {_fmt_price(target_price)}，"
            f"⚠️ 必须人工复核确认后再操作。"
        )
    elif rr_ready and rr_val < 1.0:
        exec_text = f"盈亏比 {rr_text}，低于 1.0 入场门槛，空仓观望，等待更优结构。"
    else:
        exec_text = f"盈亏比 {rr_text}，多周期信号不明，空仓观望，耐心等待共振确认。"

    # H4 补充行
    h4_line = f"\n• H4趋势：{h4_summary}" if h4_summary else ""
    vix_line = f"\n• 宏观情绪：{vix_text}" if vix_text else ""

    content = (
        f"{emoji} 【降级模式⚠️ 当前指令：{instruction}】{symbol} | "
        f"{'多周期偏多' if alignment == 'bullish' else ('多周期偏空' if alignment == 'bearish' else '方向待确认')}"
        f" —— AI 离线，本报告由本地规则引擎生成，请人工复核\n"
        f"\n"
        f"核心逻辑（必含精确价格 + 盈亏比评估）：\n"
        f"价格 ${price_text}$ {boll_text}。\n"
        f"当前风险收益比 ${rr_text}$\n"
        f"\n"
        f"🤖 规则引擎判定：\n"
        f"• 位置：支撑参考 ${display_sup}$ ↔ 压力参考 ${display_res}$"
        f"（距支撑 {dist_sup} 点 / 距压力 {dist_res} 点）\n"
        f"• 信号：{multitf_text}\n"
        f"• 指标：{rsi_text}，{ma_text}\n"
        f"• MACD：{macd_text}"
        f"{h4_line}"
        f"{vix_line}\n"
        f"• 情绪：规则引擎无情绪量化能力，请结合近期 K 线形态人工判断。\n"
        f"\n"
        f"🛠️ 执行建议：{exec_text}\n"
        f"（⚠️ 规则引擎降级提醒：AI 研判当前不可用，本报告由本地规则引擎自动生成。"
        f"所有结论仅供参考，模拟跟单已自动禁用，请人工判断后再操作。）\n"
        f"\n"
        f"📊 知识库命中规则：{rule_text}\n"
        f"\n"
        f"⚠️ 下一个关键窗口：\n"
        f"{event_text}\n"
    )

    return {
        "content": content,
        "signal_meta": {"symbol": symbol, "action": "neutral", "price": 0.0, "sl": 0.0, "tp": 0.0},
        "model": "rule-engine-fallback",
        "api_base": "local",
        "rulebook_summary_text": rule_text,
        "is_fallback": True,
    }
