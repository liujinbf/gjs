"""
MT5 终端连接与报价获取。
"""
from datetime import datetime, timezone
import logging
import os
import threading
import time
from pathlib import Path

from app_config import load_project_env
from broker_gateway import resolve_broker_symbol
from breakout_context import analyze_breakout_signal, build_empty_breakout_context
from intraday_context import analyze_intraday_bars, analyze_multi_timeframe_context, build_empty_intraday_context
from key_levels import analyze_key_levels, build_empty_key_level_context
from quote_models import QuoteRow
from technical_indicators import build_technical_indicators

try:
    import MetaTrader5 as mt5

    HAS_MT5 = True
except ImportError:
    mt5 = None
    HAS_MT5 = False

# ── 线程安全锁（T-001）──────────────────────────────────────────────────────────
# GUI 主线程与 MonitorWorker 子线程可能同时调用 initialize/shutdown。
# 所有修改全局连接状态的操作必须持有此锁。
_mt5_lock = threading.Lock()

_mt5_initialized = False
_mt5_terminal_path = None
LIVE_TICK_MAX_AGE_SEC = 180


def get_mt5_call_lock() -> threading.Lock:
    """返回 MT5 C 扩展调用共享锁，供报价、实盘等链路统一串行访问。"""
    return _mt5_lock

# M-001 修复：断线指标，用于计数和告警
_disconnect_count: int = 0            # 连续断线次数
_last_disconnect_logged: float = 0.0  # 上次记录断线日志的时间戳
DISCONNECT_ALERT_THRESHOLD = 3        # 连续 N 次断线后升级为 CRITICAL
DISCONNECT_LOG_INTERVAL_SEC = 60      # 相同条件的日志最小间隔（避免刷屏）

# 自动重连节流：两次主动重连尝试之间的最短间隔（秒），防止高频轮询时过于频繁地重连
_last_reconnect_attempt: float = 0.0
AUTO_RECONNECT_MIN_INTERVAL_SEC = 15  # 至少 15 秒才允许再次主动重连

# ── 经纪商 UTC 时差校准（TZ-001）───────────────────────────────────────────────
# MT5 tick.time 是经纪商服务器本地时间戳（EET/GMT+2~3），与 Python time.time()
# （UTC）直接相减会产生数小时时差。通过第一个有效 tick 自动校准时差，
# 消除之前 delta > 3600 的脆弱 hardfix。
_broker_utc_offset_sec: float | None = None   # 经纪商 UTC 偏移（秒），None 表示未校准
_BROKER_OFFSET_SNAP_HOUR = 1800.0             # 将估算值对齐到最近 30 分钟，兼容半小时偏移的服务器时区

INTRADAY_CONTEXT_SPECS = [
    ("m5",  "TIMEFRAME_M5",  288, "近24小时"),   # 288 M5 bars = 24h
    ("m15", "TIMEFRAME_M15", 12,  "近3小时"),
    ("h1",  "TIMEFRAME_H1",  60,  "近12小时"),   # 60 H1 bars for MA50+RSI
    ("h4",  "TIMEFRAME_H4",  120, "近20天"),   # 120 H4 bars ≈ 20d，用于趋势判断
]



def _iter_mt5_terminal_candidates():
    env_path = str(os.getenv("MT5_PATH", "") or "").strip().strip('"')
    if env_path:
        yield env_path

    program_files = str(os.getenv("ProgramFiles", r"C:\Program Files") or r"C:\Program Files")
    program_files_x86 = str(os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)") or r"C:\Program Files (x86)")
    local_app = str(os.getenv("LOCALAPPDATA", "") or "")

    defaults = [
        os.path.join(program_files, "MetaTrader 5", "terminal64.exe"),
        os.path.join(program_files_x86, "MetaTrader 5", "terminal64.exe"),
    ]
    if local_app:
        defaults.append(os.path.join(local_app, "Programs", "MetaTrader 5", "terminal64.exe"))

    seen = set()
    for path in defaults:
        normalized = os.path.normpath(str(path or ""))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        yield normalized


def resolve_mt5_terminal_path(refresh: bool = False) -> str:
    global _mt5_terminal_path
    if _mt5_terminal_path and not refresh and Path(_mt5_terminal_path).exists():
        return _mt5_terminal_path

    for candidate in _iter_mt5_terminal_candidates():
        if Path(candidate).exists():
            _mt5_terminal_path = candidate
            return candidate

    _mt5_terminal_path = ""
    return ""


def _estimate_broker_utc_offset(tick_time: int, now_ts: float) -> float:
    """根据首个有效 tick 估算经纪商服务器相对于 UTC 的时差（秒）。

    策略：raw_delta = tick.time - now_utc，将其对齐到最近 30 分钟（-14h ~ +14h），
    用于后续将 tick.time 转换为近似 UTC 时间戳进行新鲜度判断。

    Args:
        tick_time: MT5 tick.time（经纪商本地时间的 Unix 时间戳）
        now_ts:    当前 UTC 时间戳（time.time()）

    Returns:
        经纪商时差估算值（秒），例如 EET GMT+3 → 10800.0
    """
    raw = float(tick_time) - now_ts
    # 对齐到最近 30 分钟，兼容 UTC+5:30 这类非整点服务器偏移
    snapped = round(raw / _BROKER_OFFSET_SNAP_HOUR) * _BROKER_OFFSET_SNAP_HOUR
    # 限制在合理范围内：UTC-14 ~ UTC+14
    return max(-50400.0, min(50400.0, snapped))


def _format_utc_text(ts: float) -> str:
    if float(ts or 0.0) <= 0:
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _inspect_tick_activity(tick, now_ts: float | None = None, max_age_sec: int = LIVE_TICK_MAX_AGE_SEC) -> dict:
    """返回 tick 活跃性诊断，并在必要时自动纠正经纪商时差估算。"""
    global _broker_utc_offset_sec

    current_ts = float(now_ts if now_ts is not None else time.time())
    activity = {
        "is_live": False,
        "reason": "no_tick",
        "reason_text": "MT5 未返回 tick",
        "diagnostic_text": "MT5 当前没有返回可用 tick。",
        "tick_time": 0,
        "tick_utc_ts": 0.0,
        "tick_utc_text": "",
        "now_utc_text": _format_utc_text(current_ts),
        "delta_sec": 0.0,
        "max_age_sec": float(max(5, int(max_age_sec))),
        "broker_offset_sec": float(_broker_utc_offset_sec or 0.0),
        "offset_recalibrated": False,
        "price_available": False,
    }

    if tick is None:
        return activity

    tick_time = int(getattr(tick, "time", 0) or 0)
    bid = float(getattr(tick, "bid", 0.0) or 0.0)
    ask = float(getattr(tick, "ask", 0.0) or 0.0)
    last = float(getattr(tick, "last", 0.0) or 0.0)
    activity["tick_time"] = tick_time

    if tick_time <= 0:
        activity.update(
            {
                "reason": "no_tick_time",
                "reason_text": "tick 时间戳无效",
                "diagnostic_text": "MT5 返回了 tick，但没有有效时间戳。",
            }
        )
        return activity

    if max(bid, ask, last) <= 0:
        activity.update(
            {
                "reason": "no_price",
                "reason_text": "tick 缺少价格",
                "diagnostic_text": "MT5 返回了 tick，但 bid/ask/last 都为空。",
            }
        )
        return activity

    activity["price_available"] = True
    candidate_offset = _estimate_broker_utc_offset(tick_time, current_ts)
    if _broker_utc_offset_sec is None:
        _broker_utc_offset_sec = candidate_offset

    used_offset = float(_broker_utc_offset_sec or 0.0)
    tick_utc = float(tick_time) - used_offset
    delta_sec = abs(current_ts - tick_utc)
    candidate_tick_utc = float(tick_time) - candidate_offset
    candidate_delta_sec = abs(current_ts - candidate_tick_utc)

    if (
        candidate_delta_sec <= max(5, int(max_age_sec))
        and candidate_delta_sec + 5 < delta_sec
    ):
        _broker_utc_offset_sec = candidate_offset
        used_offset = candidate_offset
        tick_utc = candidate_tick_utc
        delta_sec = candidate_delta_sec
        activity["offset_recalibrated"] = True

    is_live = delta_sec <= max(5, int(max_age_sec))
    if is_live:
        reason = "live"
        reason_text = "报价活跃"
        diagnostic_text = f"最新 tick 约延迟 {delta_sec:.0f} 秒，仍在活跃阈值 {max(5, int(max_age_sec))} 秒内。"
    else:
        reason = "stale_tick"
        reason_text = "tick 已过旧"
        diagnostic_text = f"最新 tick 约延迟 {delta_sec:.0f} 秒，超过活跃阈值 {max(5, int(max_age_sec))} 秒。"

    activity.update(
        {
            "is_live": is_live,
            "reason": reason,
            "reason_text": reason_text,
            "diagnostic_text": diagnostic_text,
            "tick_utc_ts": tick_utc,
            "tick_utc_text": _format_utc_text(tick_utc),
            "delta_sec": float(delta_sec),
            "broker_offset_sec": float(used_offset),
        }
    )
    return activity


def _is_live_tick(tick, now_ts: float | None = None, max_age_sec: int = LIVE_TICK_MAX_AGE_SEC) -> bool:
    """判断 MT5 tick 是否为活跃报价（新鲜度验证）。

    TZ-001 修复：通过 _broker_utc_offset_sec 将经纪商本地时间戳换算为 UTC，
    再与 time.time() 比较，消除之前 delta > 3600 的 hardfix。
    """
    return bool(_inspect_tick_activity(tick, now_ts=now_ts, max_age_sec=max_age_sec).get("is_live", False))



def _is_connection_alive() -> bool:
    if not HAS_MT5 or not _mt5_initialized:
        return False
    try:
        return mt5.terminal_info() is not None
    except Exception:  # noqa: BLE001
        return False


def _reset_broker_utc_offset() -> None:
    """重置经纪商时差缓存，避免重连后继续沿用旧偏移。"""
    global _broker_utc_offset_sec
    _broker_utc_offset_sec = None


def _force_reconnect_if_needed() -> bool:
    """fetch_quotes 每次调用前的心跳探测 + 自动重连。

    解决问题：_mt5_initialized=True 但 MT5 进程已挂的「僵死连接」场景。
    initialize_connection() 只在程序启动和明显断线时触发，无法感知进程重启或网络超时。
    本函数在每次拉取报价前直接调用 terminal_info()，发现失败则强制 shutdown→reinitialize。

    节流规则：两次主动重连之间至少间隔 AUTO_RECONNECT_MIN_INTERVAL_SEC 秒，
    防止高频轮询在短时间内反复 shutdown/initialize 占用线程。

    线程安全：通过 _mt5_lock 保护全局状态写入（T-001）。
    返回 True 表示连接健康（或重连成功），False 表示仍然无法连接。
    """
    global _mt5_initialized, _disconnect_count, _last_disconnect_logged, _last_reconnect_attempt

    if not HAS_MT5:
        return False

    # 快速路径：连接正常，直接返回（不需要锁）
    if _mt5_initialized and _is_connection_alive():
        return True

    with _mt5_lock:
        # 进入锁后再次检查，防止两个线程都通过了快速路径检查
        if _mt5_initialized and _is_connection_alive():
            return True

        # 节流：距上次主动重连不足最短间隔，暂不重试
        now_ts = time.time()
        if now_ts - _last_reconnect_attempt < AUTO_RECONNECT_MIN_INTERVAL_SEC:
            return False
        _last_reconnect_attempt = now_ts

        # 清理旧连接（防止 mt5.initialize 在旧会话上叠加）
        if _mt5_initialized:
            try:
                mt5.shutdown()
            except Exception as exc:  # noqa: BLE001
                logging.warning(f"MT5 shutdown 异常（强制重连阶段）：{exc}")
            _mt5_initialized = False
            _reset_broker_utc_offset()
            _disconnect_count += 1
            if now_ts - _last_disconnect_logged >= DISCONNECT_LOG_INTERVAL_SEC:
                _last_disconnect_logged = now_ts
                if _disconnect_count >= DISCONNECT_ALERT_THRESHOLD:
                    logging.critical(
                        f"🚨 MT5 连接僵死，正在执行自动强制重连"
                        f"（已连续断线 {_disconnect_count} 次）"
                    )
                else:
                    logging.warning(
                        f"⚠️ MT5 心跳失败，尝试自动重连（第 {_disconnect_count} 次）…"
                    )

        # 重新初始化
        try:
            kwargs = _build_initialize_kwargs()
            if mt5.initialize(**kwargs):
                _mt5_initialized = True
                _reset_broker_utc_offset()
                if _disconnect_count > 0:
                    logging.warning(
                        f"✅ MT5 自动重连成功（曾连续断线 {_disconnect_count} 次）"
                    )
                _disconnect_count = 0
                return True
            err = mt5.last_error()
            logging.warning(f"⚠️ MT5 自动重连失败，错误码：{err}")
            return False
        except Exception as exc:  # noqa: BLE001
            logging.exception(f"MT5 自动重连异常：{exc}")
            return False



def _build_initialize_kwargs() -> dict:
    load_project_env()
    kwargs = {}
    terminal_path = resolve_mt5_terminal_path()
    if terminal_path:
        kwargs["path"] = terminal_path

    login = str(os.getenv("MT5_LOGIN", "") or "").strip()
    password = str(os.getenv("MT5_PASSWORD", "") or "").strip()
    server = str(os.getenv("MT5_SERVER", "") or "").strip()
    if login and password and server:
        kwargs["login"] = int(login)
        kwargs["password"] = password
        kwargs["server"] = server
    return kwargs


def initialize_connection() -> tuple[bool, str]:
    """建立 MT5 连接。线程安全：通过 _mt5_lock 保护所有全局状态写入。"""
    global _mt5_initialized, _disconnect_count, _last_disconnect_logged
    load_project_env()

    if not HAS_MT5:
        return False, "未安装 MetaTrader5 Python 库，请先执行 pip install MetaTrader5。"

    with _mt5_lock:
        if _mt5_initialized and _is_connection_alive():
            # 连接正常，若之前有过断线则输出恢复日志
            if _disconnect_count > 0:
                logging.warning(
                    f"✅ MT5 连接已恢复（断线过 {_disconnect_count} 次轮询）"
                )
                _disconnect_count = 0
            path = resolve_mt5_terminal_path()
            return True, f"MT5 已连接：{path}" if path else "MT5 已连接。"

        if _mt5_initialized and not _is_connection_alive():
            try:
                mt5.shutdown()
            except Exception as exc:  # noqa: BLE001
                logging.warning(f"MT5 shutdown 异常（连接检测阶段）：{exc}")
            _mt5_initialized = False
            _reset_broker_utc_offset()
            # M-001 修复：断线计数，并按频率输出日志
            _disconnect_count += 1
            now_ts = time.time()
            if now_ts - _last_disconnect_logged >= DISCONNECT_LOG_INTERVAL_SEC:
                _last_disconnect_logged = now_ts
                if _disconnect_count >= DISCONNECT_ALERT_THRESHOLD:
                    logging.critical(
                        f"🚨 MT5 断线持续！已连续 {_disconnect_count} 次无法重连，"
                        f"请检查 MT5 终端是否正常运行。"
                    )
                else:
                    logging.warning(
                        f"⚠️ MT5 心跳失败（第 {_disconnect_count} 次），尝试重连…"
                    )

        try:
            kwargs = _build_initialize_kwargs()
            if not mt5.initialize(**kwargs):
                err = mt5.last_error()
                _disconnect_count += 1
                now_ts = time.time()
                if now_ts - _last_disconnect_logged >= DISCONNECT_LOG_INTERVAL_SEC:
                    _last_disconnect_logged = now_ts
                    if _disconnect_count >= DISCONNECT_ALERT_THRESHOLD:
                        logging.critical(
                            f"🚨 MT5 初始化失败（已连续 {_disconnect_count} 次），错误码：{err}"
                        )
                    else:
                        logging.warning(f"⚠️ MT5 重连失败（第 {_disconnect_count} 次），错误码：{err}")
                return False, f"MT5 初始化失败，错误码：{err}"
            _mt5_initialized = True
            _reset_broker_utc_offset()
            _disconnect_count = 0  # 重连成功，重置计数器
            path = kwargs.get("path", "") or resolve_mt5_terminal_path()
            logging.info(f"🟢 MT5 重连成功：{path or '未知终端路径'}")
            return True, f"MT5 连接成功：{path}" if path else "MT5 连接成功。"
        except Exception as exc:
            logging.exception("MT5 初始化异常")
            return False, f"MT5 初始化异常：{exc}"


def shutdown_connection() -> None:
    """安全关闭 MT5 连接。线程安全：通过 _mt5_lock 保护全局状态写入。"""
    global _mt5_initialized
    with _mt5_lock:
        if HAS_MT5 and _mt5_initialized:
            try:
                mt5.shutdown()
            except Exception as exc:  # noqa: BLE001
                logging.warning(f"MT5 shutdown 异常：{exc}")
            finally:
                _mt5_initialized = False
                _reset_broker_utc_offset()


def fetch_quotes(symbols: list[str], include_inactive: bool = True) -> list[dict]:
    global _mt5_initialized
    # initialize_connection() 已负责一次完整的连接探测：
    # - 从未连接时尝试初始化
    # - 已连接但心跳失败时执行 shutdown -> reinitialize
    # 因此这里只在初始化失败后再补一次强制重连，避免每轮轮询重复探测 MT5 心跳。
    ok, _message = initialize_connection()
    if not ok:
        # 连接明显失败时，再尝试一次强制重连（节流保护内）
        ok = _force_reconnect_if_needed()
        if not ok:
            return []
    elif not _mt5_initialized:
        # 防御性自愈：只要 initialize_connection() 已明确返回成功，
        # 本轮报价拉取就应视为连接可用，避免状态位与真实连接结果短暂失同步。
        _mt5_initialized = True

    rows = []
    for symbol in symbols or []:
        resolved_symbol = resolve_broker_symbol(str(symbol or ""))
        symbol_key = resolved_symbol.internal
        broker_symbol = resolved_symbol.broker
        if not symbol_key:
            continue
        try:
            with _mt5_lock:
                # 必须在锁内调用 C 扩展，防止 shutdown 并发导致 Segfault
                if not _mt5_initialized:
                    raise RuntimeError("MT5 connection was shut down")
                selected = mt5.symbol_select(broker_symbol, True)
                info = mt5.symbol_info(broker_symbol)
                tick = mt5.symbol_info_tick(broker_symbol)
            tick_activity = _inspect_tick_activity(tick)
            has_live_quote = bool(tick_activity.get("is_live", False))

            if not include_inactive and not has_live_quote:
                continue

            bid = float(getattr(tick, "bid", 0.0) or 0.0) if tick is not None else 0.0
            ask = float(getattr(tick, "ask", 0.0) or 0.0) if tick is not None else 0.0
            last = float(getattr(tick, "last", 0.0) or 0.0) if tick is not None else 0.0
            latest = last if last > 0 else ((bid + ask) / 2.0 if max(bid, ask) > 0 else 0.0)
            point = float(getattr(info, "point", 0.0) or 0.0) if info is not None else 0.0
            if point > 0 and bid > 0 and ask > 0 and ask >= bid:
                spread = round(max((ask - bid) / point, 0.0), 6)
            else:
                spread = float(getattr(info, "spread", 0.0) or 0.0) if info is not None else 0.0
            tick_time = int(getattr(tick, "time", 0) or 0) if tick is not None else 0
            intraday_context = build_empty_intraday_context()
            multi_timeframe_context = {
                "multi_timeframe_context_ready": False,
                "multi_timeframe_alignment": "unknown",
                "multi_timeframe_alignment_text": "多周期不足",
                "multi_timeframe_bias": "unknown",
                "multi_timeframe_bias_text": "待确认",
                "multi_timeframe_context_text": "",
                "multi_timeframe_detail": "",
                "m15_context_text": "",
                "h1_context_text": "",
                "h4_context_text": "",
            }
            key_level_context = build_empty_key_level_context()
            breakout_context = build_empty_breakout_context()
            if selected:
                timeframe_contexts = {}
                timeframe_rates = {}
                for key, timeframe_attr, count, label in INTRADAY_CONTEXT_SPECS:
                    timeframe_value = getattr(mt5, timeframe_attr, None)
                    if timeframe_value is None:
                        continue
                    try:
                        with _mt5_lock:
                            if not _mt5_initialized:
                                raise RuntimeError("MT5 connection was shut down")
                            recent_rates = mt5.copy_rates_from_pos(broker_symbol, timeframe_value, 1, count)
                        timeframe_rates[key] = recent_rates
                        timeframe_contexts[key] = analyze_intraday_bars(symbol_key, recent_rates, lookback_label=label)
                    except Exception:  # noqa: BLE001
                        timeframe_contexts[key] = build_empty_intraday_context()
                intraday_context = dict(timeframe_contexts.get("m5", build_empty_intraday_context()))
                multi_timeframe_context = analyze_multi_timeframe_context(timeframe_contexts)
                multi_timeframe_context["m15_context_text"] = str(timeframe_contexts.get("m15", {}).get("intraday_context_text", "") or "").strip()
                multi_timeframe_context["h1_context_text"] = str(timeframe_contexts.get("h1", {}).get("intraday_context_text", "") or "").strip()
                multi_timeframe_context["h4_context_text"] = str(timeframe_contexts.get("h4", {}).get("intraday_context_text", "") or "").strip()
                try:
                    key_level_context = analyze_key_levels(symbol_key, latest, timeframe_rates.get("h1", []))
                except Exception:  # noqa: BLE001
                    key_level_context = build_empty_key_level_context()
                try:
                    breakout_context = analyze_breakout_signal(key_level_context, timeframe_rates.get("m5", []))
                except Exception:  # noqa: BLE001
                    breakout_context = build_empty_breakout_context()
                tech_indicators = build_technical_indicators({
                    "m5": timeframe_rates.get("m5"),
                    "h1": timeframe_rates.get("h1"),
                    "h4": timeframe_rates.get("h4"),
                })
            else:
                tech_indicators = {}

            if info is None:
                status = "未识别品种"
                status_code = "unknown_symbol"
            elif not selected:
                status = "未加入市场报价"
                status_code = "not_selected"
            elif has_live_quote:
                status = "实时报价"
                status_code = "live"
            else:
                inactive_reason = str(tick_activity.get("reason", "") or "").strip().lower()
                if inactive_reason == "stale_tick":
                    status = f"报价延迟（{float(tick_activity.get('delta_sec', 0.0) or 0.0):.0f}秒）"
                elif inactive_reason == "no_price":
                    status = "MT5 返回空报价"
                elif inactive_reason == "no_tick_time":
                    status = "tick 时间异常"
                else:
                    status = "非活跃或暂无实时报价"
                status_code = "inactive"

            rows.append(
                QuoteRow(
                    symbol=symbol_key,
                    latest_price=latest,
                    bid=bid,
                    ask=ask,
                    spread_points=spread,
                    point=point,
                    tick_time=tick_time,
                    status=status,
                    quote_status_code=status_code,
                    has_live_quote=has_live_quote,
                    extra={
                        "broker_symbol": broker_symbol,
                        "broker_symbol_mapped": bool(resolved_symbol.is_mapped),
                        "volume_step": float(getattr(info, "volume_step", 0.0) or 0.0) if info is not None else 0.0,
                        "volume_min": float(getattr(info, "volume_min", 0.0) or 0.0) if info is not None else 0.0,
                        "quote_live_reason": str(tick_activity.get("reason", "") or "").strip(),
                        "quote_live_reason_text": str(tick_activity.get("reason_text", "") or "").strip(),
                        "quote_live_diagnostic_text": str(tick_activity.get("diagnostic_text", "") or "").strip(),
                        "quote_live_delta_sec": float(tick_activity.get("delta_sec", 0.0) or 0.0),
                        "quote_live_max_age_sec": float(tick_activity.get("max_age_sec", 0.0) or 0.0),
                        "quote_tick_utc_text": str(tick_activity.get("tick_utc_text", "") or "").strip(),
                        "quote_now_utc_text": str(tick_activity.get("now_utc_text", "") or "").strip(),
                        "quote_broker_offset_sec": float(tick_activity.get("broker_offset_sec", 0.0) or 0.0),
                        "quote_offset_recalibrated": bool(tick_activity.get("offset_recalibrated", False)),
                        "quote_price_available": bool(tick_activity.get("price_available", False)),
                        **intraday_context,
                        **multi_timeframe_context,
                        **key_level_context,
                        **breakout_context,
                        **tech_indicators,
                    },
                ).to_dict()
            )
        except Exception as exc:  # noqa: BLE001
            logging.exception(f"MT5 拉取 {symbol_key}({broker_symbol}) 报价异常：{exc}")
            if not include_inactive:
                continue
            rows.append(
                QuoteRow(
                    symbol=symbol_key,
                    status=f"报价拉取异常：{exc}",
                    quote_status_code="error",
                    has_live_quote=False,
                    extra={
                        "broker_symbol": broker_symbol,
                        "broker_symbol_mapped": bool(resolved_symbol.is_mapped),
                        **build_empty_intraday_context(),
                        **{
                            "multi_timeframe_context_ready": False,
                            "multi_timeframe_alignment": "unknown",
                            "multi_timeframe_alignment_text": "多周期不足",
                            "multi_timeframe_bias": "unknown",
                            "multi_timeframe_bias_text": "待确认",
                            "multi_timeframe_context_text": "",
                            "multi_timeframe_detail": "",
                            "m15_context_text": "",
                            "h1_context_text": "",
                            "h4_context_text": "",
                        },
                        **build_empty_key_level_context(),
                        **build_empty_breakout_context(),
                    },
                ).to_dict()
            )
    return rows
