"""
多品种方向联动分析 + 单品种 vs 多品种套利价值评估。

核心功能：
  1. 全市场品种方向扫描：统计当前所有被监控品种的方向一致性
  2. 相关性矩阵：黄金/白银/欧美/美日的历史相关性分析
  3. Au/Ag 比价套利信号（黄金价格/白银价格×100）
  4. 单品种 vs 多品种套利综合评分：帮助用户判断当前哪种操作更优

相关性先验（历史统计，在强美元/风险偏好情绪下会变化）：
  XAUUSD 与 XAGUSD：正相关 0.85+（同为贵金属，情绪共振）
  XAUUSD 与 EURUSD：正相关 0.60+（美元反向联动）
  XAUUSD 与 USDJPY：负相关 -0.55（避险资产分流）
  XAGUSD 与 EURUSD：正相关 0.55+
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

# ── Au/Ag 比价套利阈值 ────────────────────────────────────────────────────
_AU_AG_RATIO_LONG_GOLD   = 90.0   # > 90：黄金相对偏贵，白银便宜 → 做多白银 / 做空黄金
_AU_AG_RATIO_SHORT_GOLD  = 70.0   # < 70：黄金相对便宜，白银贵 → 做多黄金 / 做空白银
_AU_AG_RATIO_NEUTRAL_LOW = 75.0
_AU_AG_RATIO_NEUTRAL_HIGH = 85.0
_AU_AG_HISTORY_FILE = Path(__file__).resolve().parent / ".runtime" / "au_ag_ratio_history.jsonl"
_AU_AG_Z_ENTRY = 2.0
_AU_AG_Z_EXIT = 0.35
_AU_AG_MIN_SAMPLES = 40
_AU_AG_LOOKBACK_SAMPLES = 480

# 相关性矩阵（先验值，实时价值超越纯历史统计）
_CORRELATION_MATRIX: dict[tuple[str, str], float] = {
    ("XAUUSD", "XAGUSD"):  0.87,
    ("XAUUSD", "EURUSD"):  0.62,
    ("XAUUSD", "USDJPY"): -0.55,
    ("XAGUSD", "EURUSD"):  0.58,
    ("XAGUSD", "USDJPY"): -0.48,
    ("EURUSD", "USDJPY"): -0.40,
}

# 套利优先级评分权重
_ARBITRAGE_SCORE_WEIGHTS = {
    "ratio_extreme": 3.0,     # 比价极端偏离
    "direction_aligned": 2.0, # 多品种方向一致
    "correlation_trade": 1.5, # 高相关品种背离
    "single_scalp": 1.0,      # 单品种短线信号
}


def _f(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _t(value: object) -> str:
    return str(value or "").strip().lower()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_ratio_history(path: Path | None = None, limit: int = _AU_AG_LOOKBACK_SAMPLES) -> list[float]:
    path = Path(path or _AU_AG_HISTORY_FILE)
    if not path.exists():
        return []
    values: list[float] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-max(1, int(limit)):]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        ratio = _f(payload.get("ratio"))
        if ratio > 0:
            values.append(ratio)
    return values


def _append_ratio_history(ratio: float, path: Path | None = None) -> None:
    if ratio <= 0:
        return
    path = Path(path or _AU_AG_HISTORY_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": _now_text(), "ratio": round(float(ratio), 6)}, ensure_ascii=False) + "\n")
    except OSError:
        return


def _calc_ratio_zscore(current_ratio: float, history: list[float]) -> dict:
    if current_ratio <= 0 or len(history) < 2:
        return {
            "au_ag_zscore_ready": False,
            "au_ag_zscore": 0.0,
            "au_ag_ratio_mean": 0.0,
            "au_ag_ratio_std": 0.0,
            "au_ag_ratio_sample_count": len(history),
        }
    window = list(history[-_AU_AG_LOOKBACK_SAMPLES:])
    sample_count = len(window)
    avg = mean(window)
    std = stdev(window) if sample_count >= 2 else 0.0
    zscore = ((current_ratio - avg) / std) if std > 1e-8 else 0.0
    return {
        "au_ag_zscore_ready": bool(sample_count >= _AU_AG_MIN_SAMPLES and std > 1e-8),
        "au_ag_zscore": round(zscore, 3),
        "au_ag_ratio_mean": round(avg, 4),
        "au_ag_ratio_std": round(std, 4),
        "au_ag_ratio_sample_count": sample_count,
    }


def _calc_ratio_momentum(current_ratio: float, history: list[float], lookback: int = 3) -> dict:
    if current_ratio <= 0 or not history:
        return {
            "au_ag_ratio_momentum": 0.0,
            "au_ag_ratio_momentum_state": "unknown",
        }
    anchor_values = list(history[-max(1, int(lookback)):])
    anchor = mean(anchor_values)
    momentum = round(current_ratio - anchor, 4)
    if momentum > 0.01:
        state = "expanding_up"
    elif momentum < -0.01:
        state = "expanding_down"
    else:
        state = "flat"
    return {
        "au_ag_ratio_momentum": momentum,
        "au_ag_ratio_momentum_state": state,
    }


def get_correlation(sym_a: str, sym_b: str) -> float:
    """返回两个品种之间的先验相关系数。"""
    key1 = (sym_a.upper(), sym_b.upper())
    key2 = (sym_b.upper(), sym_a.upper())
    return _CORRELATION_MATRIX.get(key1, _CORRELATION_MATRIX.get(key2, 0.0))


def analyze_au_ag_ratio(items: list[dict]) -> dict:
    """
    计算并分析 Au/Ag 比价套利信号。

    输入：快照 items 列表（需含 XAUUSD 和 XAGUSD 的最新价格）
    输出：
      {
        "ratio": 比价值,
        "ratio_bias": "high"/"low"/"neutral",
        "signal": "long_xag_short_xau" / "long_xau_short_xag" / "" (无信号),
        "signal_text": 可读描述,
        "confidence": "high"/"medium"/"low",
        "ratio_percentile_text": 历史位置描述,
      }
    """
    xau_price = 0.0
    xag_price = 0.0
    for item in items:
        sym = _t(item.get("symbol", ""))
        price = _f(item.get("latest_price"))
        if "xau" in sym and price > 0:
            xau_price = price
        elif "xag" in sym and price > 0:
            xag_price = price

    empty = {
        "au_ag_ratio": 0.0,
        "au_ag_ratio_bias": "unknown",
        "au_ag_signal": "",
        "au_ag_signal_text": "",
        "au_ag_confidence": "",
        "au_ag_ratio_text": "",
        "au_ag_ready": False,
        "au_ag_zscore_ready": False,
        "au_ag_zscore": 0.0,
        "au_ag_ratio_mean": 0.0,
        "au_ag_ratio_std": 0.0,
        "au_ag_ratio_sample_count": 0,
        "au_ag_ratio_momentum": 0.0,
        "au_ag_ratio_momentum_state": "unknown",
        "au_ag_pair_legs": [],
        "au_ag_exit_zscore": _AU_AG_Z_EXIT,
    }
    if xau_price <= 0 or xag_price <= 0:
        return empty

    ratio = round(xau_price / xag_price, 2)
    history = _load_ratio_history()
    stats = _calc_ratio_zscore(ratio, history)
    momentum_stats = _calc_ratio_momentum(ratio, history)
    _append_ratio_history(ratio)

    if stats["au_ag_zscore_ready"] and float(stats["au_ag_zscore"]) >= _AU_AG_Z_ENTRY:
        signal = "long_xag_short_xau"
        signal_text = (
            f"Au/Ag Z-Score={float(stats['au_ag_zscore']):+.2f}，金银比 {ratio:.2f} "
            f"显著高于滚动均值 {float(stats['au_ag_ratio_mean']):.2f}：黄金相对偏贵、白银相对偏便宜。"
            "配对方向：做空 XAUUSD + 做多 XAGUSD；当 Z 回落至 ±0.35 附近时退出。"
        )
        return {
            **stats,
            **momentum_stats,
            "au_ag_ratio": ratio,
            "au_ag_ratio_bias": "high",
            "au_ag_signal": signal,
            "au_ag_signal_text": signal_text,
            "au_ag_confidence": "high" if float(stats["au_ag_zscore"]) >= 2.5 else "medium",
            "au_ag_ratio_text": f"Au/Ag={ratio:.2f}，Z={float(stats['au_ag_zscore']):+.2f}，高位均值回归候选。",
            "au_ag_ready": True,
            "au_ag_pair_legs": [
                {"symbol": "XAUUSD", "action": "short", "role": "rich_leg"},
                {"symbol": "XAGUSD", "action": "long", "role": "cheap_leg"},
            ],
            "au_ag_exit_zscore": _AU_AG_Z_EXIT,
        }

    if stats["au_ag_zscore_ready"] and float(stats["au_ag_zscore"]) <= -_AU_AG_Z_ENTRY:
        signal = "long_xau_short_xag"
        signal_text = (
            f"Au/Ag Z-Score={float(stats['au_ag_zscore']):+.2f}，金银比 {ratio:.2f} "
            f"显著低于滚动均值 {float(stats['au_ag_ratio_mean']):.2f}：黄金相对偏便宜、白银相对偏贵。"
            "配对方向：做多 XAUUSD + 做空 XAGUSD；当 Z 回落至 ±0.35 附近时退出。"
        )
        return {
            **stats,
            **momentum_stats,
            "au_ag_ratio": ratio,
            "au_ag_ratio_bias": "low",
            "au_ag_signal": signal,
            "au_ag_signal_text": signal_text,
            "au_ag_confidence": "high" if float(stats["au_ag_zscore"]) <= -2.5 else "medium",
            "au_ag_ratio_text": f"Au/Ag={ratio:.2f}，Z={float(stats['au_ag_zscore']):+.2f}，低位均值回归候选。",
            "au_ag_ready": True,
            "au_ag_pair_legs": [
                {"symbol": "XAUUSD", "action": "long", "role": "cheap_leg"},
                {"symbol": "XAGUSD", "action": "short", "role": "rich_leg"},
            ],
            "au_ag_exit_zscore": _AU_AG_Z_EXIT,
        }

    if ratio > _AU_AG_RATIO_LONG_GOLD:
        bias = "high"
        signal = "long_xag_short_xau"
        confidence = "high" if ratio > 95 else "medium"
        signal_text = (
            f"Au/Ag 比价 {ratio:.1f}（历史偏高 > {_AU_AG_RATIO_LONG_GOLD}）：黄金相对高估，"
            f"白银相对低估，套利方向：做多白银（XAGUSD）+ 做空黄金（XAUUSD），"
            f"等待比价向 80 均值回归。"
        )
        ratio_text = f"Au/Ag={ratio:.1f}，历史上方压力区，黄金/白银比价偏高。"
    elif ratio < _AU_AG_RATIO_SHORT_GOLD:
        bias = "low"
        signal = "long_xau_short_xag"
        confidence = "high" if ratio < 65 else "medium"
        signal_text = (
            f"Au/Ag 比价 {ratio:.1f}（历史偏低 < {_AU_AG_RATIO_SHORT_GOLD}）：黄金相对低估，"
            f"白银相对高估，套利方向：做多黄金（XAUUSD）+ 做空白银（XAGUSD），"
            f"等待比价向 80 均值回归。"
        )
        ratio_text = f"Au/Ag={ratio:.1f}，历史下方支撑区，黄金/白银比价偏低。"
    else:
        bias = "neutral"
        signal = ""
        confidence = ""
        signal_text = f"Au/Ag 比价 {ratio:.1f}，处于正常均值区间（{_AU_AG_RATIO_NEUTRAL_LOW}-{_AU_AG_RATIO_NEUTRAL_HIGH}），无明显套利信号。"
        ratio_text = f"Au/Ag={ratio:.1f}，区间中性。"

    return {
        **stats,
        **momentum_stats,
        "au_ag_ratio": ratio,
        "au_ag_ratio_bias": bias,
        "au_ag_signal": signal,
        "au_ag_signal_text": signal_text,
        "au_ag_confidence": confidence,
        "au_ag_ratio_text": ratio_text,
        "au_ag_ready": bool(signal),
        "au_ag_pair_legs": (
            [
                {"symbol": "XAUUSD", "action": "short", "role": "rich_leg"},
                {"symbol": "XAGUSD", "action": "long", "role": "cheap_leg"},
            ]
            if signal == "long_xag_short_xau"
            else (
                [
                    {"symbol": "XAUUSD", "action": "long", "role": "cheap_leg"},
                    {"symbol": "XAGUSD", "action": "short", "role": "rich_leg"},
                ]
                if signal == "long_xau_short_xag"
                else []
            )
        ),
        "au_ag_exit_zscore": _AU_AG_Z_EXIT,
    }


def analyze_multi_symbol_direction(items: list[dict]) -> dict:
    """
    扫描全部品种的方向状态，分析方向一致性和强弱分化。

    输出：
      {
        "aligned_direction": "bullish"/"bearish"/"mixed",
        "aligned_symbols": ["XAUUSD", ...],   # 同方向品种
        "divergent_pairs": [{"sym_a": .., "sym_b": .., ...}],  # 方向背离的相关品种对
        "direction_summary": "可读摘要",
        "multi_symbol_edge": "multi"/"single"/"neutral",  # 哪种操作模式更有边
        "multi_symbol_edge_text": "...",
      }
    """
    direction_map: dict[str, str] = {}
    has_signal: dict[str, bool] = {}

    for item in items:
        sym = str(item.get("symbol", "") or "").strip().upper()
        if not sym:
            continue
        intraday = _t(item.get("intraday_bias", ""))
        multi_b  = _t(item.get("multi_timeframe_bias", ""))
        # 以两者一致且明确的方向为准
        if intraday == multi_b and intraday in {"bullish", "bearish"}:
            direction_map[sym] = intraday
        elif intraday in {"bullish", "bearish"}:
            direction_map[sym] = intraday
        elif multi_b in {"bullish", "bearish"}:
            direction_map[sym] = multi_b
        # 是否有短线信号
        has_signal[sym] = bool(item.get("scalp_ready", False))

    bullish_syms = [s for s, d in direction_map.items() if d == "bullish"]
    bearish_syms = [s for s, d in direction_map.items() if d == "bearish"]
    total = len(direction_map)

    if total == 0:
        return {
            "aligned_direction": "unknown",
            "aligned_symbols": [],
            "divergent_pairs": [],
            "direction_summary": "暂无足够品种数据",
            "multi_symbol_edge": "neutral",
            "multi_symbol_edge_text": "当前品种数据不足，无法判断多品种联动方向。",
        }

    # 确定主方向
    if len(bullish_syms) >= 3:
        aligned_direction = "bullish"
        aligned_symbols = bullish_syms
    elif len(bearish_syms) >= 3:
        aligned_direction = "bearish"
        aligned_symbols = bearish_syms
    elif len(bullish_syms) > len(bearish_syms):
        aligned_direction = "bullish"
        aligned_symbols = bullish_syms
    elif len(bearish_syms) > len(bullish_syms):
        aligned_direction = "bearish"
        aligned_symbols = bearish_syms
    else:
        aligned_direction = "mixed"
        aligned_symbols = []

    # 检测高相关品种方向背离
    divergent_pairs: list[dict] = []
    all_syms = list(direction_map.keys())
    for i, sym_a in enumerate(all_syms):
        for sym_b in all_syms[i + 1:]:
            corr = get_correlation(sym_a, sym_b)
            dir_a = direction_map[sym_a]
            dir_b = direction_map[sym_b]
            # 强正相关但方向背离 → 套利机会
            if corr >= 0.7 and dir_a != dir_b and dir_a in {"bullish", "bearish"} and dir_b in {"bullish", "bearish"}:
                divergent_pairs.append({
                    "sym_a": sym_a,
                    "sym_b": sym_b,
                    "correlation": corr,
                    "dir_a": dir_a,
                    "dir_b": dir_b,
                    "pair_text": (
                        f"{sym_a}({dir_a}) vs {sym_b}({dir_b}) 相关性 {corr:.2f}，"
                        f"当前方向背离，可能存在价差收敛机会。"
                    ),
                })

    # 综合评估：多品种 vs 单品种哪种更有边
    single_signal_count = sum(1 for v in has_signal.values() if v)
    multi_edge_score = 0.0
    if aligned_direction in {"bullish", "bearish"} and len(aligned_symbols) >= 3:
        multi_edge_score += _ARBITRAGE_SCORE_WEIGHTS["direction_aligned"]
    if divergent_pairs:
        multi_edge_score += _ARBITRAGE_SCORE_WEIGHTS["correlation_trade"] * len(divergent_pairs)
    single_edge_score = _ARBITRAGE_SCORE_WEIGHTS["single_scalp"] * single_signal_count

    if multi_edge_score >= single_edge_score * 1.5 and multi_edge_score >= 3.0:
        multi_symbol_edge = "multi"
        edge_text = (
            f"当前多品种联动方向评分（{multi_edge_score:.1f}）高于单品种短线（{single_edge_score:.1f}），"
            f"建议优先关注多品种同向操作或背离套利。"
        )
    elif single_edge_score >= multi_edge_score * 1.5 and single_signal_count >= 1:
        multi_symbol_edge = "single"
        edge_text = (
            f"当前单品种短线信号清晰（信号数={single_signal_count}），"
            f"多品种联动评分（{multi_edge_score:.1f}）不突出，建议专注单品种短线。"
        )
    else:
        multi_symbol_edge = "neutral"
        edge_text = "当前单品种和多品种方向信号均不突出，建议观望或轻仓试探。"

    # 构建摘要文本
    direction_parts = []
    if aligned_direction in {"bullish", "bearish"}:
        dir_cn = "偏多" if aligned_direction == "bullish" else "偏空"
        direction_parts.append(
            f"当前 {len(aligned_symbols)}/{total} 个品种同向 {dir_cn}（{'/'.join(aligned_symbols)}）"
        )
    else:
        direction_parts.append(f"当前 {total} 个品种方向分化（多：{len(bullish_syms)} 空：{len(bearish_syms)}）")
    if divergent_pairs:
        for p in divergent_pairs[:2]:  # 最多展示2对
            direction_parts.append(p["pair_text"])
    direction_summary = "；".join(direction_parts)

    return {
        "aligned_direction": aligned_direction,
        "aligned_symbols": aligned_symbols,
        "divergent_pairs": divergent_pairs,
        "direction_summary": direction_summary,
        "multi_symbol_edge": multi_symbol_edge,
        "multi_symbol_edge_text": edge_text,
    }


def build_correlation_context(items: list[dict]) -> dict:
    """
    综合运行多品种分析 + Au/Ag 套利分析，返回全部联动上下文。
    可直接注入快照 metadata 或 AI prompt。
    """
    try:
        from app_config import get_runtime_config

        au_ag_enabled = bool(getattr(get_runtime_config(), "scalp_au_ag_arbitrage", True))
    except Exception:  # noqa: BLE001
        au_ag_enabled = True

    au_ag = analyze_au_ag_ratio(items) if au_ag_enabled else {
        "au_ag_ratio": 0.0,
        "au_ag_ratio_bias": "disabled",
        "au_ag_signal": "",
        "au_ag_signal_text": "",
        "au_ag_confidence": "",
        "au_ag_ratio_text": "",
        "au_ag_ready": False,
    }
    multi_dir = analyze_multi_symbol_direction(items)

    # 如果有 Au/Ag 比价套利信号，提升多品种评分
    if au_ag.get("au_ag_ready"):
        multi_dir["multi_symbol_edge"] = "multi"
        au_ag_note = f"【Au/Ag套利】{au_ag['au_ag_signal_text']}"
        multi_dir["multi_symbol_edge_text"] = (
            au_ag_note + " " + multi_dir.get("multi_symbol_edge_text", "")
        )

    return {
        **au_ag,
        **multi_dir,
    }
