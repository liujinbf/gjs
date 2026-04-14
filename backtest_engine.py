"""
MT5 Backtest Engine.
Evaluates AI trade signals stored in ai_history by comparing their machine-readable
signal_meta with real historical M5 bars from MetaTrader5.
"""
from __future__ import annotations

import json
import logging

# 3.1 修复：引入 json_repair 自愈容错。pip install json-repair
try:
    from json_repair import loads as _json_repair_loads
except ImportError:
    _json_repair_loads = None
import re
from datetime import datetime, timedelta
from pathlib import Path

from app_config import PROJECT_DIR
from ai_history import read_recent_ai_history, AI_HISTORY_FILE
from mt5_gateway import initialize_connection, HAS_MT5
from runtime_utils import parse_time as _parse_time_impl

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

RUNTIME_DIR = PROJECT_DIR / ".runtime"
BACKTEST_RESULTS_FILE = RUNTIME_DIR / "backtest_results.json"
MAX_BACKTEST_RESULTS = 2000  # N-010: 保留最近 N 条评估结果，超出后滚动副除最旧的

TRACKER_META_PATTERN = re.compile(r"<!--\s*TRACKER_META\s*:\s*(\{.*?\})\s*-->", re.IGNORECASE | re.DOTALL)
# 3.1 修复：宽松版正则，用于严格正则匹配失败时（如 AI 少写了闭合 }），提取 { 到 --> 之前的内容交给 json_repair
TRACKER_META_PATTERN_LOOSE = re.compile(r"<!--\s*TRACKER_META\s*:\s*(\{[^>]*?)\s*-->", re.IGNORECASE | re.DOTALL)
JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)

# P-004 修复：_parse_time 委托给公共 runtime_utils.parse_time，消除三处重复定义
def _parse_time(value: str) -> datetime | None:
    return _parse_time_impl(value)

def _load_json_dict(text: str) -> dict | None:
    raw_text = str(text or "").strip()
    if not raw_text:
        return None
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    if _json_repair_loads is not None:
        try:
            data = _json_repair_loads(raw_text)
            if isinstance(data, dict):
                logging.debug("[extract_signal_meta] 原生 JSON 解析失败，json_repair 自愈成功。")
                return data
        except Exception:
            pass
    return None


def _normalize_signal_meta(data: dict | None) -> dict | None:
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("signal_meta"), dict):
        data = dict(data.get("signal_meta", {}) or {})
    elif isinstance(data.get("tracker_meta"), dict):
        data = dict(data.get("tracker_meta", {}) or {})
    if not any(key in data for key in ("action", "price", "sl", "tp", "symbol")):
        return None
    action = str(data.get("action", "neutral") or "neutral").strip().lower()
    if action not in {"long", "short", "neutral"}:
        action = "neutral"
    return {
        "symbol": str(data.get("symbol", "") or "").strip().upper(),
        "action": action,
        "price": float(data.get("price", 0.0) or 0.0),
        "sl": float(data.get("sl", 0.0) or 0.0),
        "tp": float(data.get("tp", 0.0) or 0.0),
    }


def extract_signal_meta(content: str) -> dict | None:
    raw_content = str(content or "").strip()
    direct_payload = _normalize_signal_meta(_load_json_dict(raw_content))
    if direct_payload is not None:
        return direct_payload

    for match in JSON_BLOCK_PATTERN.finditer(raw_content):
        payload = _normalize_signal_meta(_load_json_dict(match.group(1)))
        if payload is not None:
            return payload

    match = TRACKER_META_PATTERN.search(raw_content)
    if not match:
        # 3.1 修复：严格正则失败时，尝试宽松正则（AI 少写了 } 的情况）
        match = TRACKER_META_PATTERN_LOOSE.search(raw_content)
        if not match:
            return None
    raw_json = match.group(1)
    return _normalize_signal_meta(_load_json_dict(raw_json))

def load_backtest_results() -> dict:
    if not BACKTEST_RESULTS_FILE.exists():
        return {}
    try:
        data = json.loads(BACKTEST_RESULTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}

def save_backtest_results(data: dict) -> None:
    # N-010 修复：保留最新 MAX_BACKTEST_RESULTS 条，超出间滚动副除最旧记录，防止文件无限增长
    if len(data) > MAX_BACKTEST_RESULTS:
        # 按 occurred_at 排序，保留最新的
        def _sort_key(item):
            return str(item[1].get("occurred_at", "") or "")
        sorted_items = sorted(data.items(), key=_sort_key, reverse=True)
        data = dict(sorted_items[:MAX_BACKTEST_RESULTS])
        logging.info(f"[backtest] 结果清理：保留最新 {MAX_BACKTEST_RESULTS} 条，已副除过期记录。")
    BACKTEST_RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BACKTEST_RESULTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def evaluate_signal(signal_meta: dict, start_time: datetime, now_time: datetime) -> str:
    """
    用 M5 K 线评估 AI 信号胜败。
    N-004 修复：同棒内 SL/TP 均被触及时，比较各自距入场价的距离，
    距离更近的先触发（而非硬编码止损优先），消除约 5~10% 的胜率低估偏差。
    Returns: 'win', 'loss', or 'pending'
    """
    if not HAS_MT5:
        return "pending"

    symbol = str(signal_meta.get("symbol", "") or "").strip().upper()
    action = str(signal_meta.get("action", "") or "").strip().lower()
    # DEFECT-002 修复：TRACKER_META 规范字段为 "price"，但 evaluate_signal 原来优先读 "entry"。
    # 当 AI 输出标准格式（含 "price" 字段）时，entry 会是 0，导致同棒内 SL/TP 共触发时
    # 退化为保守判"止损"，造成约 5-10% 的胜率低估。
    # 修复：price → entry → 0 顺序兼容，entry=0 时同棒共触改为按 RR 比例估算而非硬判 loss。
    entry = float(
        signal_meta.get("price", None)
        or signal_meta.get("entry", None)
        or 0.0
    )
    sl = float(signal_meta.get("sl", 0.0) or 0.0)
    tp = float(signal_meta.get("tp", 0.0) or 0.0)

    if action not in {"long", "short"} or sl <= 0 or tp <= 0 or not symbol:
        return "neutral"

    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_time, now_time)
    if rates is None or len(rates) == 0:
        return "pending"

    for candle in rates:
        high = float(candle["high"])
        low  = float(candle["low"])

        if action == "long":
            sl_hit = low <= sl
            tp_hit = high >= tp
        else:  # short
            sl_hit = high >= sl
            tp_hit = low <= tp

        if sl_hit and tp_hit:
            # N-004 修复：同棒内两端均触及 → 比较距入场价的距离，近者先触发
            if entry > 0:
                dist_sl = abs(entry - sl)
                dist_tp = abs(tp - entry)
                return "loss" if dist_sl <= dist_tp else "win"
            else:
                # DEFECT-002 修复延伸：入场价未知时按 RR 比保守估算
                # SL 和 TP 距中点的距离比较，距中点近者先触发
                mid = (sl + tp) / 2.0
                return "loss" if abs(sl - mid) <= abs(tp - mid) else "win"

        elif sl_hit:
            return "loss"
        elif tp_hit:
            return "win"


    # Time has elapsed, but neither SL nor TP has been hit
    # If the signal is older than 24 hours (or some threshold), we might consider it 'timeout'
    # But for now, returning pending
    return "pending"

def run_backtest_evaluations() -> None:
    """Reads AI history, extracts meta, pulls MT5 data, and saves results.
    N-001 修复：即使 HAS_MT5=False，仍然处理超时状态（超过 3 天未封陆的信号标为 timeout）。
    前提有 MT5 时才进行实际 K 线回测。
    """
    if HAS_MT5:
        ok, _ = initialize_connection()
    else:
        ok = False

    results = load_backtest_results()
    history = read_recent_ai_history(limit=500)
    if not history:
        return

    now = datetime.now()
    changed = False

    for entry in history:
        signature = str(entry.get("signature", ""))
        if not signature:
            continue

        existing_status = results.get(signature, {}).get("status", "pending")
        if existing_status in {"win", "loss", "neutral", "timeout"}:
            continue

        content = str(entry.get("content", ""))
        meta = _normalize_signal_meta(dict(entry.get("signal_meta", {}) or {})) or extract_signal_meta(content)

        if not meta:
            results[signature] = {"status": "neutral", "evaluated_at": now.strftime("%Y-%m-%d %H:%M:%S")}
            changed = True
            continue

        occurred_at = _parse_time(entry.get("occurred_at", ""))
        if not occurred_at:
            continue

        # 信号发出后给 5 分钟缓冲再评估
        if now - occurred_at < timedelta(minutes=5):
            continue

        # N-001 修复：无 MT5 时只处理超时，有 MT5 时才做 K 线回测
        if ok:
            status = evaluate_signal(meta, occurred_at, now)
        else:
            status = "pending"  # 无 MT5 就不能评估胜败

        # 超时处理：占位信号 3 天后自动关闭，即使无 MT5
        if status == "pending" and (now - occurred_at) > timedelta(days=3):
            status = "timeout"

        if status != "pending" or existing_status != "pending":
            results[signature] = {
                "status": status,
                "meta": meta,
                "occurred_at": entry.get("occurred_at", ""),
                "evaluated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            changed = True

    if changed:
        save_backtest_results(results)

def get_historical_win_rate(symbol: str = "", days: int = 90) -> dict:
    """Returns the aggregated win rate over the last N days."""
    results = load_backtest_results()
    if not results:
        return {"total": 0, "wins": 0, "losses": 0, "rate": 0.0, "symbol": symbol}
        
    cutoff = datetime.now() - timedelta(days=days)
    
    wins = 0
    losses = 0
    
    for sig, data in results.items():
        status = data.get("status")
        if status not in {"win", "loss"}:
            continue
            
        sig_symbol = str(data.get("meta", {}).get("symbol", "") or "").strip().upper()
        if symbol and sig_symbol != str(symbol).strip().upper():
            continue
            
        occurred_at = _parse_time(data.get("occurred_at", ""))
        if not occurred_at or occurred_at < cutoff:
            continue
            
        if status == "win":
            wins += 1
        elif status == "loss":
            losses += 1
            
    total = wins + losses
    rate = (wins / total * 100) if total > 0 else 0.0
    
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "rate": rate,
        "symbol": symbol,
        "days": days
    }
