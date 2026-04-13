"""
技术指标计算模块 —— 纯 Python，无需 TA-Lib / numpy。

提供：
  - RSI (14周期)
  - SMA / EMA (MA20, MA50)
  - 布林带 (20, 2σ)
  - MACD (12, 26, 9)
  - 24h涨跌幅

输入：mt5.copy_rates_from_pos() 返回的 rates 数组或 list[dict]。
"""
from __future__ import annotations

from statistics import mean, stdev


def _extract_closes(rates) -> list[float]:
    """从 MT5 rates（结构化数组或 list[dict]）提取收盘价序列。"""
    closes = []
    if rates is None:
        return closes
    try:
        for bar in rates:
            try:
                v = float(bar["close"])
            except (TypeError, KeyError):
                try:
                    v = float(getattr(bar, "close", 0.0) or 0.0)
                except Exception:
                    continue
            if v > 0:
                closes.append(v)
    except Exception:
        pass
    return closes


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder 平滑 RSI，返回 0-100 之间的浮点数，不足 period+1 根柱时返回 None。"""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    # 首次平均
    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])
    # Wilder 平滑
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def calc_sma(closes: list[float], period: int) -> float | None:
    """简单移动均线，不足 period 根时返回 None。"""
    if len(closes) < period:
        return None
    return round(mean(closes[-period:]), 4)


def calc_ema(closes: list[float], period: int) -> float | None:
    """指数移动平均，不足 period 根时返回 None。"""
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = mean(closes[:period])
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 4)


def calc_bollinger(closes: list[float], period: int = 20, sigma: float = 2.0) -> dict | None:
    """布林带，返回 {mid, upper, lower}。不足 period 根时返回 None。"""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = mean(window)
    std = stdev(window)
    return {
        "mid": round(mid, 4),
        "upper": round(mid + sigma * std, 4),
        "lower": round(mid - sigma * std, 4),
    }


def calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    """MACD，返回 {macd, signal_line, histogram}。"""
    if len(closes) < slow + signal:
        return None
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None
    macd_val = ema_fast - ema_slow
    # Signal 线需要 MACD 序列历史，简单近似用最新值
    return {
        "macd": round(macd_val, 4),
        "signal_line": None,   # 需要完整历史序列才能计算，此处留空
        "histogram": None,
    }


def calc_change_pct(closes: list[float], lookback: int = 288) -> float | None:
    """计算最近 N 根 K 线的涨跌幅百分比（默认 M5 * 288 = 24h）。"""
    if len(closes) < 2:
        return None
    n = min(lookback, len(closes) - 1)
    ref = closes[-(n + 1)]
    if ref == 0:
        return None
    return round((closes[-1] - ref) / ref * 100, 2)


def build_technical_indicators(rates_by_timeframe: dict) -> dict:
    """
    输入：{
        "m5":  MT5 rates (at least 60 bars for reliable RSI/MA),
        "h1":  MT5 rates (at least 50 bars for MA50),
    }
    输出：一个含所有指标的 dict，可直接 merge 进 quote row。
    """
    result = {
        "rsi14": None,
        "ma20": None,
        "ma50": None,
        "bollinger_upper": None,
        "bollinger_mid": None,
        "bollinger_lower": None,
        "change_pct_24h": None,
        "tech_summary": "",
    }

    # --- H1 for RSI14 + MA20 + MA50 + Bollinger (needs more history) ---
    h1_closes = _extract_closes(rates_by_timeframe.get("h1"))

    if h1_closes:
        result["rsi14"] = calc_rsi(h1_closes, period=14)
        result["ma20"]  = calc_sma(h1_closes, 20)
        result["ma50"]  = calc_sma(h1_closes, 50)
        boll = calc_bollinger(h1_closes, 20)
        if boll:
            result["bollinger_upper"] = boll["upper"]
            result["bollinger_mid"]   = boll["mid"]
            result["bollinger_lower"] = boll["lower"]

    # --- M5 for 24h change (M5 × 288 bar ≈ 24h) ---
    m5_closes = _extract_closes(rates_by_timeframe.get("m5"))
    if m5_closes:
        result["change_pct_24h"] = calc_change_pct(m5_closes, lookback=288)

    # --- Human-readable summary for AI prompt ---
    parts = []
    if result["rsi14"] is not None:
        rsi = result["rsi14"]
        rsi_tag = "超买" if rsi > 70 else ("超卖" if rsi < 30 else "中性区间")
        parts.append(f"RSI(14)={rsi}({rsi_tag})")
    if result["ma20"] and result["ma50"]:
        cross = "均线多头" if result["ma20"] > result["ma50"] else "均线空头(MA20<MA50)"
        parts.append(f"MA20={result['ma20']:.2f} MA50={result['ma50']:.2f} {cross}")
    if result["bollinger_mid"]:
        last_close = h1_closes[-1] if h1_closes else None
        if last_close:
            boll_pos = (
                "价格在上轨附近" if last_close >= result["bollinger_upper"] * 0.998
                else ("价格在下轨附近" if last_close <= result["bollinger_lower"] * 1.002
                      else "价格在布林带中段")
            )
            parts.append(f"布林中轨={result['bollinger_mid']:.2f} 上轨={result['bollinger_upper']:.2f} 下轨={result['bollinger_lower']:.2f}（{boll_pos}）")
    if result["change_pct_24h"] is not None:
        sign = "+" if result["change_pct_24h"] >= 0 else ""
        parts.append(f"24h涨跌幅: {sign}{result['change_pct_24h']}%")

    result["tech_summary"] = " | ".join(parts) if parts else "技术指标数据不足"
    return result
