"""
技术指标计算模块 —— 纯 Python，无需 TA-Lib / numpy。

提供：
  - RSI (14周期) / RSI (6周期, 短线动能)
  - SMA / EMA (MA20, MA50) / EMA(9, 21, M5快慢线)
  - 布林带 (20, 2σ) 含 %B 值（位置百分比）
  - MACD (12, 26, 9) —— 含完整 signal_line / histogram
  - ATR(14, H1) / ATR(5, M1) —— M1 ATR5 用于短线动态止损
  - 24h涨跌幅
  - H4 趋势级指标（RSI14 / MA20 / MA50 / 布林带）
  - M5 短线指标（EMA9/EMA21快慢线 / RSI6 / 布林带%B / ATR5）

输入：mt5.copy_rates_from_pos() 返回的 rates 数组或 list[dict]。

周期分工：
  H1 → 节奏指标（短期 RSI/MA/布林带，用于日内出手时机）
  H4 → 趋势指标（中期方向判断，防止"逆大势"入场）
  M5 → 短线快轨指标（EMA9/21快慢线 / RSI6 / 布林带%B）+ 24h涨跌幅
  M1 → 极速止损锚（ATR5，用于计算分钟级止损距离）
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


def _extract_hlc(rates) -> list[tuple[float, float, float]]:
    """从 MT5 rates（结构化数组或 list[dict]）提取 high / low / close 序列。"""
    result = []
    if rates is None:
        return result
    try:
        for bar in rates:
            try:
                high = float(bar["high"])
                low = float(bar["low"])
                close = float(bar["close"])
            except (TypeError, KeyError):
                try:
                    high = float(getattr(bar, "high", 0.0) or 0.0)
                    low = float(getattr(bar, "low", 0.0) or 0.0)
                    close = float(getattr(bar, "close", 0.0) or 0.0)
                except Exception:
                    continue
            if min(high, low, close) > 0 and high >= low:
                result.append((high, low, close))
    except Exception:
        pass
    return result


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
    """MACD，返回 {macd, signal_line, histogram}。

    修复 DEFECT-001：逐根计算完整 MACD 序列，再对序列做 EMA-9 得到 signal_line。
    需要至少 slow + signal 根 K 线。
    """
    min_bars = slow + signal
    if len(closes) < min_bars:
        return None

    k_fast = 2 / (fast + 1)
    k_slow = 2 / (slow + 1)
    k_sig  = 2 / (signal + 1)

    # 初始化两条 EMA（从前 slow 根的 SMA 出发）
    ema_f = sum(closes[:fast]) / fast
    ema_s = sum(closes[:slow]) / slow

    # 暖机阶段：从 slow 根起逐根更新，同时收集 MACD 值
    macd_series: list[float] = []
    for price in closes[slow:]:
        ema_f = price * k_fast + ema_f * (1 - k_fast)
        ema_s = price * k_slow + ema_s * (1 - k_slow)
        macd_series.append(ema_f - ema_s)

    if len(macd_series) < signal:
        return None

    # 用 MACD 序列的前 signal 根 SMA 初始化 signal_line
    sig_ema = sum(macd_series[:signal]) / signal
    for val in macd_series[signal:]:
        sig_ema = val * k_sig + sig_ema * (1 - k_sig)

    macd_val = macd_series[-1]
    histogram = macd_val - sig_ema
    return {
        "macd": round(macd_val, 4),
        "signal_line": round(sig_ema, 4),
        "histogram": round(histogram, 4),
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


def calc_atr(rates, period: int = 14) -> float | None:
    """ATR(真实波动幅度均值)，使用 Wilder 平滑，不足 period+1 根 K 线时返回 None。"""
    hlc = _extract_hlc(rates)
    if len(hlc) < period + 1:
        return None

    trs: list[float] = []
    previous_close = hlc[0][2]
    for high, low, close in hlc[1:]:
        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        trs.append(max(true_range, 0.0))
        previous_close = close

    if len(trs) < period:
        return None

    atr = mean(trs[:period])
    for value in trs[period:]:
        atr = ((atr * (period - 1)) + value) / period
    return round(atr, 6)


def calc_adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """
    Wilder 平滑 ADX(14) 指标计算，纯 Python 实现。
    需要至少 2 * period + 1 根 K 线以获得足够平滑度。
    """
    min_bars = period * 2 + 1
    if len(highs) < min_bars or len(lows) < min_bars or len(closes) < min_bars:
        return None

    # 1. 计算 TR, +DM, -DM
    tr_series: list[float] = []
    pdm_series: list[float] = []
    ndm_series: list[float] = []

    for i in range(1, len(closes)):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]

        # TR
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_series.append(max(tr, 0.0))

        # +DM & -DM
        if high_diff > low_diff and high_diff > 0:
            pdm_series.append(high_diff)
        else:
            pdm_series.append(0.0)

        if low_diff > high_diff and low_diff > 0:
            ndm_series.append(low_diff)
        else:
            ndm_series.append(0.0)

    # 2. 对 TR, +DM, -DM 进行 Wilder 平滑
    tr_smooth = sum(tr_series[:period])
    pdm_smooth = sum(pdm_series[:period])
    ndm_smooth = sum(ndm_series[:period])

    dx_series: list[float] = []

    # 首个 DI 值的计算
    if tr_smooth > 1e-8:
        plus_di = 100.0 * (pdm_smooth / tr_smooth)
        minus_di = 100.0 * (ndm_smooth / tr_smooth)
    else:
        plus_di, minus_di = 0.0, 0.0

    di_sum = plus_di + minus_di
    dx = 100.0 * (abs(plus_di - minus_di) / di_sum) if di_sum > 1e-8 else 0.0
    dx_series.append(dx)

    # 平滑计算其余 DX 值
    for i in range(period, len(tr_series)):
        tr_smooth = tr_smooth - (tr_smooth / period) + tr_series[i]
        pdm_smooth = pdm_smooth - (pdm_smooth / period) + pdm_series[i]
        ndm_smooth = ndm_smooth - (ndm_smooth / period) + ndm_series[i]

        if tr_smooth > 1e-8:
            p_di = 100.0 * (pdm_smooth / tr_smooth)
            n_di = 100.0 * (ndm_smooth / tr_smooth)
        else:
            p_di, n_di = 0.0, 0.0

        d_sum = p_di + n_di
        dx_val = 100.0 * (abs(p_di - n_di) / d_sum) if d_sum > 1e-8 else 0.0
        dx_series.append(dx_val)

    # 3. 对 DX 序列进行 Wilder 平滑移动平均得到 ADX
    if len(dx_series) < period:
        return None

    adx = mean(dx_series[:period])
    for value in dx_series[period:]:
        adx = ((adx * (period - 1)) + value) / period

    return round(adx, 2)



def build_technical_indicators(rates_by_timeframe: dict) -> dict:
    """
    输入：{
        "m5":  MT5 rates (至少 288 bars，用于 24h 涨跌幅),
        "h1":  MT5 rates (至少 60 bars，用于节奏 RSI/MA/布林带),
        "h4":  MT5 rates (至少 60 bars，用于趋势 RSI/MA/布林带),  # 新增
    }
    输出：一个含所有指标的 dict，可直接 merge 进 quote row。

    字段分工：
      rsi14 / ma20 / ma50 / bollinger_* / tech_summary         → H1 节奏指标
      rsi14_h4 / ma20_h4 / ma50_h4 / bollinger_*_h4 / tech_summary_h4 → H4 趋势指标
    """
    result = {
        # H1 节奏指标
        "rsi14": None,
        "ma20": None,
        "ma50": None,
        "bollinger_upper": None,
        "bollinger_mid": None,
        "bollinger_lower": None,
        "macd": None,
        "macd_signal": None,
        "macd_histogram": None,
        "change_pct_24h": None,
        "atr14": None,
        "tech_summary": "",
        # H4 趋势指标
        "rsi14_h4": None,
        "ma20_h4": None,
        "ma50_h4": None,
        "bollinger_upper_h4": None,
        "bollinger_mid_h4": None,
        "bollinger_lower_h4": None,
        "atr14_h4": None,
        "tech_summary_h4": "",
        # M5 短线快轨指标（新增）
        "ema9_m5": None,
        "ema21_m5": None,
        "prev_ema9_m5": None,
        "prev_ema21_m5": None,
        "rsi6_m5": None,
        "bb_pct_m5": None,       # 布林带 %B（0=下轨，0.5=中轨，1=上轨）
        "bb_upper_m5": None,
        "bb_mid_m5": None,
        "bb_lower_m5": None,
        "bb_width_m5": None,     # 布林带宽度（上轨-下轨）/中轨，衡量波动收窄
        "atr5_m5": None,         # M5 ATR(5)，短线止损参考
        "atr5_m1": None,         # M1 ATR(5)，极速止损锚
        "m5_last_high": None,
        "m5_last_low": None,
        "m5_last_close": None,
        "m5_prev_high": None,
        "m5_prev_low": None,
        "m5_prev_close": None,
        "m5_scalp_summary": "",  # M5 短线摘要（供 AI prompt 使用）
        "adx_m5": None,          # M5 趋势强度 ADX(14)
    }


    # ------------------------------------------------------------------ #
    # H1 节奏指标：RSI14 / MA20 / MA50 / 布林带 / MACD                  #
    # ------------------------------------------------------------------ #
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
        macd_res = calc_macd(h1_closes)
        if macd_res:
            result["macd"]           = macd_res["macd"]
            result["macd_signal"]    = macd_res["signal_line"]
            result["macd_histogram"] = macd_res["histogram"]
        result["atr14"] = calc_atr(rates_by_timeframe.get("h1"), period=14)

    # ------------------------------------------------------------------ #
    # M5：24h 涨跌幅 + 短线快轨指标（EMA9/EMA21 / RSI6 / 布林带%B）    #
    # ------------------------------------------------------------------ #
    m5_closes = _extract_closes(rates_by_timeframe.get("m5"))
    if m5_closes:
        result["change_pct_24h"] = calc_change_pct(m5_closes, lookback=288)
        # EMA 快慢线
        result["ema9_m5"]  = calc_ema(m5_closes, 9)
        result["ema21_m5"] = calc_ema(m5_closes, 21)
        if len(m5_closes) > 9:
            result["prev_ema9_m5"] = calc_ema(m5_closes[:-1], 9)
        if len(m5_closes) > 21:
            result["prev_ema21_m5"] = calc_ema(m5_closes[:-1], 21)
        # RSI6：比 RSI14 更灵敏，专为分钟级动能设计
        result["rsi6_m5"] = calc_rsi(m5_closes, period=6)
        # M5 布林带（20周期）及 %B 位置
        boll_m5 = calc_bollinger(m5_closes, period=20)
        if boll_m5:
            upper_m5 = boll_m5["upper"]
            mid_m5   = boll_m5["mid"]
            lower_m5 = boll_m5["lower"]
            result["bb_upper_m5"] = upper_m5
            result["bb_mid_m5"]   = mid_m5
            result["bb_lower_m5"] = lower_m5
            band_width = upper_m5 - lower_m5
            result["bb_width_m5"] = round(band_width / mid_m5, 6) if mid_m5 > 0 else None
            if band_width > 1e-8:
                last_m5 = m5_closes[-1]
                result["bb_pct_m5"] = round((last_m5 - lower_m5) / band_width, 4)
        # M5 ATR(5)：短线止损参考
        result["atr5_m5"] = calc_atr(rates_by_timeframe.get("m5"), period=5)
        m5_hlc = _extract_hlc(rates_by_timeframe.get("m5"))
        if m5_hlc:
            high, low, close = m5_hlc[-1]
            result["m5_last_high"] = high
            result["m5_last_low"] = low
            result["m5_last_close"] = close
        if len(m5_hlc) >= 2:
            high, low, close = m5_hlc[-2]
            result["m5_prev_high"] = high
            result["m5_prev_low"] = low
            result["m5_prev_close"] = close

        # 动态计算 M5 周期 ADX(14) 趋势强度指标
        highs = [float(h) for h, l, c in m5_hlc]
        lows = [float(l) for h, l, c in m5_hlc]
        closes = [float(c) for h, l, c in m5_hlc]
        result["adx_m5"] = calc_adx(highs, lows, closes, period=14)


    # ------------------------------------------------------------------ #
    # M1：极速止损锚 ATR(5)                                              #
    # ------------------------------------------------------------------ #
    if rates_by_timeframe.get("m1") is not None:
        result["atr5_m1"] = calc_atr(rates_by_timeframe.get("m1"), period=5)

    # ------------------------------------------------------------------ #
    # H4 趋势指标：RSI14 / MA20 / MA50 / 布林带                          #
    # ------------------------------------------------------------------ #
    h4_closes = _extract_closes(rates_by_timeframe.get("h4"))

    if h4_closes:
        result["rsi14_h4"] = calc_rsi(h4_closes, period=14)
        result["ma20_h4"]  = calc_sma(h4_closes, 20)
        result["ma50_h4"]  = calc_sma(h4_closes, 50)
        boll4 = calc_bollinger(h4_closes, 20)
        if boll4:
            result["bollinger_upper_h4"] = boll4["upper"]
            result["bollinger_mid_h4"]   = boll4["mid"]
            result["bollinger_lower_h4"] = boll4["lower"]
        result["atr14_h4"] = calc_atr(rates_by_timeframe.get("h4"), period=14)

    # ------------------------------------------------------------------ #
    # H1 节奏摘要（供 AI prompt 展示）                                   #
    # ------------------------------------------------------------------ #
    h1_parts = []
    if result["rsi14"] is not None:
        rsi = result["rsi14"]
        rsi_tag = "超买" if rsi > 70 else ("超卖" if rsi < 30 else "中性区间")
        h1_parts.append(f"RSI(14)={rsi}({rsi_tag})")
    if result["ma20"] and result["ma50"]:
        cross = "均线多头" if result["ma20"] > result["ma50"] else "均线空头(MA20<MA50)"
        h1_parts.append(f"MA20={result['ma20']:.2f} MA50={result['ma50']:.2f} {cross}")
    if result["bollinger_mid"]:
        last_h1 = h1_closes[-1] if h1_closes else None
        if last_h1:
            boll_pos = (
                "价格在上轨附近" if last_h1 >= result["bollinger_upper"] * 0.998
                else ("价格在下轨附近" if last_h1 <= result["bollinger_lower"] * 1.002
                      else "价格在布林带中段")
            )
            h1_parts.append(
                f"布林中轨={result['bollinger_mid']:.2f} "
                f"上轨={result['bollinger_upper']:.2f} "
                f"下轨={result['bollinger_lower']:.2f}（{boll_pos}）"
            )
    if result["macd"] is not None and result["macd_signal"] is not None:
        hist = result["macd_histogram"] or 0.0
        macd_state = "金叉偏多" if hist > 0 else "死叉偏空"
        h1_parts.append(
            f"MACD={result['macd']:.4f} Signal={result['macd_signal']:.4f} "
            f"Hist={result['macd_histogram']:.4f}（{macd_state}）"
        )
    if result["atr14"] is not None:
        h1_parts.append(f"ATR(14)={result['atr14']:.4f}")
    if result["change_pct_24h"] is not None:
        sign = "+" if result["change_pct_24h"] >= 0 else ""
        h1_parts.append(f"24h涨跌幅: {sign}{result['change_pct_24h']}%")

    result["tech_summary"] = " | ".join(h1_parts) if h1_parts else "技术指标数据不足"

    # ------------------------------------------------------------------ #
    # H4 趋势摘要（供 AI prompt 展示大级别结构）                          #
    # ------------------------------------------------------------------ #
    h4_parts = []
    if result["rsi14_h4"] is not None:
        rsi4 = result["rsi14_h4"]
        rsi4_tag = "超买区" if rsi4 > 70 else ("超卖区" if rsi4 < 30 else "中性区")
        h4_parts.append(f"RSI(14)={rsi4}({rsi4_tag})")
    if result["ma20_h4"] and result["ma50_h4"]:
        cross4 = "趋势多头" if result["ma20_h4"] > result["ma50_h4"] else "趋势空头(MA20<MA50)"
        h4_parts.append(f"MA20={result['ma20_h4']:.2f} MA50={result['ma50_h4']:.2f} {cross4}")
    if result["bollinger_mid_h4"]:
        last_h4 = h4_closes[-1] if h4_closes else None
        if last_h4:
            boll4_pos = (
                "价格在H4上轨附近" if last_h4 >= result["bollinger_upper_h4"] * 0.998
                else ("价格在H4下轨附近" if last_h4 <= result["bollinger_lower_h4"] * 1.002
                      else "价格在H4布林带中段")
            )
            h4_parts.append(
                f"布林中轨={result['bollinger_mid_h4']:.2f} "
                f"上轨={result['bollinger_upper_h4']:.2f} "
                f"下轨={result['bollinger_lower_h4']:.2f}（{boll4_pos}）"
            )
    if result["atr14_h4"] is not None:
        h4_parts.append(f"H4 ATR(14)={result['atr14_h4']:.4f}")

    result["tech_summary_h4"] = " | ".join(h4_parts) if h4_parts else ""

    # ------------------------------------------------------------------ #
    # M5 短线摘要（供 AI prompt / 短线信号引擎使用）                     #
    # ------------------------------------------------------------------ #
    m5_parts = []
    ema9 = result.get("ema9_m5")
    ema21 = result.get("ema21_m5")
    if ema9 is not None and ema21 is not None:
        cross_text = "EMA9>EMA21(多头排列)" if ema9 > ema21 else "EMA9<EMA21(空头排列)"
        gap_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
        m5_parts.append(f"M5 {cross_text} 乖离{gap_pct:.3f}%")
    rsi6 = result.get("rsi6_m5")
    if rsi6 is not None:
        rsi6_tag = (
            "超买（短线注意反转）" if rsi6 > 80
            else "超卖（短线注意反弹）" if rsi6 < 20
            else "强势" if rsi6 > 60
            else "弱势" if rsi6 < 40
            else "中性"
        )
        m5_parts.append(f"M5 RSI(6)={rsi6}({rsi6_tag})")
    bb_pct = result.get("bb_pct_m5")
    bb_width = result.get("bb_width_m5")
    if bb_pct is not None:
        if bb_pct > 0.95:
            bb_pos_text = "触及上轨（超买扩张）"
        elif bb_pct < 0.05:
            bb_pos_text = "触及下轨（超卖扩张）"
        elif 0.45 <= bb_pct <= 0.55:
            bb_pos_text = "中轨附近（等待方向）"
        else:
            bb_pos_text = f"%B={bb_pct:.2f}"
        width_text = f" 带宽{bb_width*100:.2f}%" if bb_width is not None else ""
        m5_parts.append(f"M5布林{bb_pos_text}{width_text}")
    atr5_m5 = result.get("atr5_m5")
    atr5_m1 = result.get("atr5_m1")
    if atr5_m5 is not None:
        m5_parts.append(f"M5 ATR(5)={atr5_m5:.4f}")
    if atr5_m1 is not None:
        m5_parts.append(f"M1 ATR(5)={atr5_m1:.4f}（极速止损参考）")
    result["m5_scalp_summary"] = " | ".join(m5_parts) if m5_parts else ""

    return result
