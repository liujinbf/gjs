"""
M5 短线信号引擎 —— 识别4种高胜率短线入场结构。

信号类型（按优先级）：
  1. pullback_ema21     : 回调至 M5 EMA21 狙击（趋势延续，最高置信）
  2. ema_crossover      : EMA9/EMA21 交叉 + H1 同向（动能确认）
  3. bb_squeeze_breakout: 布林带收窄后爆破（波动率扩张）
  4. liquidity_grab     : 假突破陷阱逆向（猎取流动性后反转）

每种信号输出标准 dict，可直接注入快照 item，也可被 monitor_rules.py 消费。

依赖字段（来自 technical_indicators.py + 现有快照）：
  ema9_m5 / ema21_m5 / rsi6_m5 / bb_pct_m5 / bb_width_m5
  atr5_m1 / atr5_m5 / rsi14 / ma20 / ma50
  intraday_bias / multi_timeframe_bias / multi_timeframe_alignment
  latest_price / atr14 / key_level_high / key_level_low
"""
from __future__ import annotations

# ── 阈值常量 ────────────────────────────────────────────────────────────────
# 布林带收窄判定：带宽/中轨 < 此值视为收窄
_BB_SQUEEZE_WIDTH_THRESHOLD = 0.004      # 0.4% 以下算收窄
# EMA 乖离率：低于此值认为"贴合"，可能即将交叉
_EMA_CROSSOVER_GAP_PCT = 0.0008          # 0.08%
# 假突破判定：价格刺穿前高/低后回收的幅度门槛（以 ATR5 M5 为单位）
_LIQUIDITY_GRAB_SPIKE_RATIO = 0.6        # 刺穿超过 0.6 × ATR5_M5 才算有效假突破
# RSI6 超买/超卖
_RSI6_OVERBOUGHT = 75.0
_RSI6_OVERSOLD   = 25.0


def _t(value: object) -> str:
    return str(value or "").strip().lower()


def _f(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _scalp_empty() -> dict:
    """返回无信号时的空结构。"""
    return {
        "scalp_signal_type": "",
        "scalp_direction": "",
        "scalp_entry_price": 0.0,
        "scalp_stop_price": 0.0,
        "scalp_target_price": 0.0,
        "scalp_target_2_price": 0.0,
        "scalp_rr": 0.0,
        "scalp_signal_text": "",
        "scalp_invalidation_text": "",
        "scalp_confidence": "",
        "scalp_ready": False,
        "scalp_setup_kind": "",
    }


def _build_scalp_result(
    signal_type: str,
    direction: str,
    entry: float,
    stop: float,
    target1: float,
    target2: float,
    signal_text: str,
    invalidation_text: str,
    confidence: str,
    setup_kind: str = "",
) -> dict:
    risk = abs(entry - stop)
    reward = abs(target1 - entry)
    rr = round(reward / risk, 2) if risk > 1e-8 else 0.0
    return {
        "scalp_signal_type": signal_type,
        "scalp_direction": direction,
        "scalp_entry_price": round(entry, 5),
        "scalp_stop_price": round(stop, 5),
        "scalp_target_price": round(target1, 5),
        "scalp_target_2_price": round(target2, 5),
        "scalp_rr": rr,
        "scalp_signal_text": signal_text,
        "scalp_invalidation_text": invalidation_text,
        "scalp_confidence": confidence,
        "scalp_ready": rr >= 1.2,
        "scalp_setup_kind": setup_kind or signal_type,
    }


def _detect_pullback_ema21(item: dict) -> dict | None:
    """
    策略一：回调至 M5 EMA21 狙击
    条件：
      - H1 + M5 同向（intraday_bias / multi_timeframe_bias）
      - 价格回调接触 M5 EMA21（ ±0.5×ATR5_M5 容差）
      - RSI6 未超买/超卖（30-70 区间）
      - M5 EMA9 仍站在 EMA21 同向侧
    """
    price    = _f(item.get("latest_price"))
    ema9     = _f(item.get("ema9_m5"))
    ema21    = _f(item.get("ema21_m5"))
    rsi6     = _f(item.get("rsi6_m5"))
    atr5_m5  = _f(item.get("atr5_m5"))
    atr5_m1  = _f(item.get("atr5_m1"))
    intraday = _t(item.get("intraday_bias"))
    multi_b  = _t(item.get("multi_timeframe_bias"))
    multi_a  = _t(item.get("multi_timeframe_alignment"))

    if min(price, ema9, ema21, atr5_m5) <= 0:
        return None
    if intraday not in {"bullish", "bearish"}:
        return None
    if multi_b not in {"bullish", "bearish"}:
        return None
    # 要求方向一致
    if intraday != multi_b:
        return None
    if multi_a not in {"aligned", "partial"}:
        return None
    # RSI6 在合理区间
    if not (28 <= rsi6 <= 72):
        return None

    tolerance = max(atr5_m5 * 0.5, price * 0.0003)

    if intraday == "bullish":
        # 价格应在 EMA21 附近（刚刚回调触及）
        near_ema21 = abs(price - ema21) <= tolerance and price >= ema21 - tolerance
        # EMA9 仍在 EMA21 上方（趋势未破）
        ema9_aligned = ema9 > ema21
        if not near_ema21 or not ema9_aligned:
            return None
        stop_dist = max(atr5_m1 * 1.5, atr5_m5 * 0.8)
        stop_p    = price - stop_dist
        target1   = price + stop_dist * 1.8
        target2   = price + stop_dist * 3.0
        direction = "long"
        text = (
            f"M5 EMA21 回调狙击（多）：价格 {price:.2f} 回踩至 EMA21={ema21:.2f}，"
            f"EMA9={ema9:.2f} 仍在上方，H1/M5 同向偏多，RSI6={rsi6:.1f} 未过热，"
            f"建议在 EMA21 附近轻仓试多。"
        )
        inval = f"若价格收破 EMA21 下方 {stop_p:.2f}，信号失效。"
    else:
        near_ema21 = abs(price - ema21) <= tolerance and price <= ema21 + tolerance
        ema9_aligned = ema9 < ema21
        if not near_ema21 or not ema9_aligned:
            return None
        stop_dist = max(atr5_m1 * 1.5, atr5_m5 * 0.8)
        stop_p    = price + stop_dist
        target1   = price - stop_dist * 1.8
        target2   = price - stop_dist * 3.0
        direction = "short"
        text = (
            f"M5 EMA21 回调狙击（空）：价格 {price:.2f} 反弹至 EMA21={ema21:.2f}，"
            f"EMA9={ema9:.2f} 仍在下方，H1/M5 同向偏空，RSI6={rsi6:.1f} 未过冷，"
            f"建议在 EMA21 附近轻仓试空。"
        )
        inval = f"若价格收破 EMA21 上方 {stop_p:.2f}，信号失效。"

    confidence = "high" if multi_a == "aligned" else "medium"
    return _build_scalp_result(
        "pullback_ema21", direction, price, stop_p, target1, target2,
        text, inval, confidence, "pullback_ema21",
    )


def _detect_ema_crossover(item: dict) -> dict | None:
    """
    策略二：EMA9/EMA21 交叉 + H1 同向
    条件：
      - EMA9 刚穿过 EMA21（乖离率 < 0.12%，非常贴近，刚发生交叉）
      - H1 技术面同向（intraday_bias / multi_timeframe_bias）
      - RSI6 > 55（多）或 < 45（空），有动能
    """
    price   = _f(item.get("latest_price"))
    ema9    = _f(item.get("ema9_m5"))
    ema21   = _f(item.get("ema21_m5"))
    prev_ema9 = _f(item.get("prev_ema9_m5"))
    prev_ema21 = _f(item.get("prev_ema21_m5"))
    rsi6    = _f(item.get("rsi6_m5"))
    atr5_m1 = _f(item.get("atr5_m1"))
    atr5_m5 = _f(item.get("atr5_m5"))
    intraday = _t(item.get("intraday_bias"))
    multi_b  = _t(item.get("multi_timeframe_bias"))

    if min(price, ema9, ema21, atr5_m5) <= 0:
        return None
    if min(prev_ema9, prev_ema21) <= 0:
        return None
    if intraday not in {"bullish", "bearish"}:
        return None

    gap_pct = abs(ema9 - ema21) / ema21 if ema21 > 0 else 1.0
    # 乖离率必须很小（刚交叉），但 ema9 已经在正确一侧
    if gap_pct > _EMA_CROSSOVER_GAP_PCT * 2:
        return None

    if intraday == "bullish":
        if prev_ema9 >= prev_ema21:
            return None  # 不是刚刚由下向上穿越
        if ema9 <= ema21:
            return None  # 还未上穿
        if rsi6 < 52:
            return None  # 动能不足
        stop_dist = max(atr5_m1 * 1.3, atr5_m5 * 0.7)
        stop_p  = ema21 - stop_dist * 0.6
        target1 = price + stop_dist * 1.6
        target2 = price + stop_dist * 2.8
        direction = "long"
        text = (
            f"M5 EMA 金叉（多）：EMA9={ema9:.2f} 刚上穿 EMA21={ema21:.2f}，"
            f"乖离{gap_pct*100:.3f}%，RSI6={rsi6:.1f} 动能偏强，"
            f"H1 偏{intraday}，可追金叉第一段动能。"
        )
        inval = f"若 EMA9 重新跌破 EMA21 且价格跌至 {stop_p:.2f} 下，信号失效。"
    else:
        if prev_ema9 <= prev_ema21:
            return None  # 不是刚刚由上向下穿越
        if ema9 >= ema21:
            return None
        if rsi6 > 48:
            return None
        stop_dist = max(atr5_m1 * 1.3, atr5_m5 * 0.7)
        stop_p  = ema21 + stop_dist * 0.6
        target1 = price - stop_dist * 1.6
        target2 = price - stop_dist * 2.8
        direction = "short"
        text = (
            f"M5 EMA 死叉（空）：EMA9={ema9:.2f} 刚下穿 EMA21={ema21:.2f}，"
            f"乖离{gap_pct*100:.3f}%，RSI6={rsi6:.1f} 动能偏弱，"
            f"H1 偏{intraday}，可追死叉第一段动能。"
        )
        inval = f"若 EMA9 重新站回 EMA21 且价格升至 {stop_p:.2f} 上，信号失效。"

    # 多空方向与 H1 大级别对齐时置信度更高
    confidence = "high" if multi_b == intraday else "medium"
    return _build_scalp_result(
        "ema_crossover", direction, price, stop_p, target1, target2,
        text, inval, confidence, "ema_crossover",
    )


def _detect_bb_squeeze_breakout(item: dict) -> dict | None:
    """
    策略三：布林带收窄后爆破
    条件：
      - M5 布林带宽度收窄（< _BB_SQUEEZE_WIDTH_THRESHOLD）
      - 价格突破至上轨（%B > 0.85）或下轨（%B < 0.15）
      - H1 方向与突破方向一致
      - RSI6 不处于严重超买/超卖
    """
    price    = _f(item.get("latest_price"))
    bb_pct   = _f(item.get("bb_pct_m5"))
    bb_width = _f(item.get("bb_width_m5"))
    bb_upper = _f(item.get("bb_upper_m5"))
    bb_lower = _f(item.get("bb_lower_m5"))
    bb_mid   = _f(item.get("bb_mid_m5"))
    rsi6     = _f(item.get("rsi6_m5"))
    atr5_m1  = _f(item.get("atr5_m1"))
    atr5_m5  = _f(item.get("atr5_m5"))
    intraday = _t(item.get("intraday_bias"))

    if min(price, bb_upper, bb_lower, bb_mid, atr5_m5) <= 0:
        return None
    if bb_width <= 0:
        return None
    # 必须是收窄状态
    if bb_width > _BB_SQUEEZE_WIDTH_THRESHOLD:
        return None

    if bb_pct > 0.82 and intraday == "bullish":
        # 上轨突破
        if rsi6 > 82:
            return None  # 已经严重超买
        stop_dist = max(atr5_m1 * 1.2, atr5_m5 * 0.6)
        stop_p  = max(bb_mid, price - stop_dist)
        target1 = price + stop_dist * 1.5
        target2 = price + stop_dist * 2.5
        direction = "long"
        text = (
            f"M5 布林收窄上轨爆破（多）：带宽仅 {bb_width*100:.2f}%，"
            f"价格突破至上轨附近（%B={bb_pct:.2f}），"
            f"RSI6={rsi6:.1f}，H1 偏多，波动率扩张信号。"
        )
        inval = f"若价格跌回布林中轨 {bb_mid:.2f} 以下，信号失效。"
    elif bb_pct < 0.18 and intraday == "bearish":
        # 下轨突破
        if rsi6 < 18:
            return None
        stop_dist = max(atr5_m1 * 1.2, atr5_m5 * 0.6)
        stop_p  = min(bb_mid, price + stop_dist)
        target1 = price - stop_dist * 1.5
        target2 = price - stop_dist * 2.5
        direction = "short"
        text = (
            f"M5 布林收窄下轨爆破（空）：带宽仅 {bb_width*100:.2f}%，"
            f"价格突破至下轨附近（%B={bb_pct:.2f}），"
            f"RSI6={rsi6:.1f}，H1 偏空，波动率扩张信号。"
        )
        inval = f"若价格反弹回布林中轨 {bb_mid:.2f} 以上，信号失效。"
    else:
        return None

    return _build_scalp_result(
        "bb_squeeze_breakout", direction, price, stop_p, target1, target2,
        text, inval, "medium", "bb_squeeze_breakout",
    )


def _detect_liquidity_grab(item: dict) -> dict | None:
    """
    策略四：流动性猎取陷阱（假突破后逆向）
    条件：
      - 价格曾刺穿前高或前低（key_level_high / key_level_low）
      - 当前价格已拉回到关键位内侧（假突破被吸收）
      - RSI6 从超买/超卖区反转（已经反向）
      - H1 方向与逆向操作一致或中性

    这是专门"猎取"过度追涨/砸盘仓位的反向策略。
    """
    price     = _f(item.get("latest_price"))
    key_high  = _f(item.get("key_level_high"))
    key_low   = _f(item.get("key_level_low"))
    rsi6      = _f(item.get("rsi6_m5"))
    atr5_m5   = _f(item.get("atr5_m5"))
    atr5_m1   = _f(item.get("atr5_m1"))
    intraday  = _t(item.get("intraday_bias"))
    last_high = _f(item.get("m5_last_high"))
    last_low = _f(item.get("m5_last_low"))
    prev_high = _f(item.get("m5_prev_high"))
    prev_low = _f(item.get("m5_prev_low"))

    if min(price, atr5_m5) <= 0:
        return None
    if min(last_high, last_low, prev_high, prev_low) <= 0:
        return None

    spike_threshold = atr5_m5 * _LIQUIDITY_GRAB_SPIKE_RATIO

    # 场景一：刺穿前高后被打回 → 做空
    if key_high > 0:
        # 最近两根 M5 必须真实刺穿前高，并且当前价格已经拉回前高下方。
        was_above = max(last_high, prev_high) > key_high + spike_threshold
        pulled_back = price < key_high                  # 回到前高下方
        if (
            pulled_back
            and was_above
            and rsi6 > _RSI6_OVERBOUGHT - 8  # RSI6 曾在超买区
            and intraday in {"bearish", "unknown", "sideways", ""}  # H1 不是强多
        ):
            stop_dist = max(atr5_m1 * 1.5, atr5_m5 * 0.8)
            stop_p  = key_high + stop_dist * 0.5
            target1 = price - stop_dist * 1.8
            target2 = price - stop_dist * 3.2
            text = (
                f"流动性猎取（做空）：价格刺穿前高 {key_high:.2f} 后被快速打回，"
                f"RSI6={rsi6:.1f} 开始回落，疑似多头陷阱，可轻仓顺势做空。"
            )
            inval = f"若价格重新站回前高 {key_high:.2f} 上方，信号失效。"
            return _build_scalp_result(
                "liquidity_grab", "short", price, stop_p, target1, target2,
                text, inval, "medium", "liquidity_grab",
            )

    # 场景二：刺穿前低后被打回 → 做多
    if key_low > 0:
        was_below   = min(last_low, prev_low) < key_low - spike_threshold
        pulled_back = price > key_low
        if (
            pulled_back
            and was_below
            and rsi6 < _RSI6_OVERSOLD + 8
            and intraday in {"bullish", "unknown", "sideways", ""}
        ):
            stop_dist = max(atr5_m1 * 1.5, atr5_m5 * 0.8)
            stop_p  = key_low - stop_dist * 0.5
            target1 = price + stop_dist * 1.8
            target2 = price + stop_dist * 3.2
            text = (
                f"流动性猎取（做多）：价格刺穿前低 {key_low:.2f} 后被快速收回，"
                f"RSI6={rsi6:.1f} 开始回升，疑似空头陷阱，可轻仓顺势做多。"
            )
            inval = f"若价格重新跌破前低 {key_low:.2f} 下方，信号失效。"
            return _build_scalp_result(
                "liquidity_grab", "long", price, stop_p, target1, target2,
                text, inval, "medium", "liquidity_grab",
            )

    return None


# ── 信号优先级定义 ──────────────────────────────────────────────────────────
_SIGNAL_PRIORITY = ["pullback_ema21", "ema_crossover", "bb_squeeze_breakout", "liquidity_grab"]
_DETECTORS = {
    "pullback_ema21":      _detect_pullback_ema21,
    "ema_crossover":       _detect_ema_crossover,
    "bb_squeeze_breakout": _detect_bb_squeeze_breakout,
    "liquidity_grab":      _detect_liquidity_grab,
}
_CONFIDENCE_SCORE = {"high": 3, "medium": 2, "low": 1, "": 0}


def detect_scalp_signal(item: dict) -> dict:
    """
    对单个品种的快照项运行全部短线信号检测，返回最高置信度信号。
    若无任何信号触发，返回空结构（scalp_ready=False）。
    """
    candidates: list[tuple[int, int, dict]] = []

    for priority_idx, signal_type in enumerate(_SIGNAL_PRIORITY):
        detector = _DETECTORS.get(signal_type)
        if not detector:
            continue
        try:
            result = detector(item)
        except Exception:  # noqa: BLE001
            result = None
        if result and result.get("scalp_ready"):
            conf_score = _CONFIDENCE_SCORE.get(
                str(result.get("scalp_confidence", "") or ""), 0
            )
            candidates.append((conf_score, priority_idx, result))

    if not candidates:
        return _scalp_empty()

    # 按置信度降序、优先级升序选最佳
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2]


def detect_all_scalp_signals(snapshot: dict) -> list[dict]:
    """
    对快照中所有品种运行短线信号检测，返回有效信号列表（scalp_ready=True）。
    每个元素额外包含 symbol 字段。
    """
    results: list[dict] = []
    for item in list((snapshot or {}).get("items", []) or []):
        symbol = str(item.get("symbol", "") or "").strip().upper()
        if not symbol:
            continue
        sig = detect_scalp_signal(item)
        if sig.get("scalp_ready"):
            sig["symbol"] = symbol
            results.append(sig)
    # 按置信度降序排列
    results.sort(
        key=lambda x: (-_CONFIDENCE_SCORE.get(str(x.get("scalp_confidence", "") or ""), 0))
    )
    return results
