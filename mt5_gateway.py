"""
MT5 终端连接与报价获取。
"""
import logging
import os
import time
from pathlib import Path

from app_config import load_project_env

try:
    import MetaTrader5 as mt5

    HAS_MT5 = True
except ImportError:
    mt5 = None
    HAS_MT5 = False

_mt5_initialized = False
_mt5_terminal_path = None
LIVE_TICK_MAX_AGE_SEC = 180


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


def _is_live_tick(tick, now_ts: float | None = None, max_age_sec: int = LIVE_TICK_MAX_AGE_SEC) -> bool:
    if tick is None:
        return False
    tick_time = int(getattr(tick, "time", 0) or 0)
    bid = float(getattr(tick, "bid", 0.0) or 0.0)
    ask = float(getattr(tick, "ask", 0.0) or 0.0)
    last = float(getattr(tick, "last", 0.0) or 0.0)
    if tick_time <= 0 or max(bid, ask, last) <= 0:
        return False
    current_ts = float(now_ts if now_ts is not None else time.time())
    return current_ts - float(tick_time) <= max(5, int(max_age_sec))


def _is_connection_alive() -> bool:
    if not HAS_MT5 or not _mt5_initialized:
        return False
    try:
        return mt5.terminal_info() is not None
    except Exception:  # noqa: BLE001
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
    global _mt5_initialized
    load_project_env()

    if not HAS_MT5:
        return False, "未安装 MetaTrader5 Python 库，请先执行 pip install MetaTrader5。"
    if _mt5_initialized and _is_connection_alive():
        path = resolve_mt5_terminal_path()
        return True, f"MT5 已连接：{path}" if path else "MT5 已连接。"
    if _mt5_initialized and not _is_connection_alive():
        try:
            mt5.shutdown()
        except Exception:  # noqa: BLE001
            pass
        _mt5_initialized = False

    try:
        kwargs = _build_initialize_kwargs()
        if not mt5.initialize(**kwargs):
            return False, f"MT5 初始化失败，错误码：{mt5.last_error()}"
        _mt5_initialized = True
        path = kwargs.get("path", "") or resolve_mt5_terminal_path()
        return True, f"MT5 连接成功：{path}" if path else "MT5 连接成功。"
    except Exception as exc:
        logging.exception("MT5 初始化异常")
        return False, f"MT5 初始化异常：{exc}"


def shutdown_connection() -> None:
    global _mt5_initialized
    if HAS_MT5 and _mt5_initialized:
        mt5.shutdown()
        _mt5_initialized = False


def fetch_quotes(symbols: list[str], include_inactive: bool = True) -> list[dict]:
    ok, _message = initialize_connection()
    if not ok:
        return []

    rows = []
    for symbol in symbols or []:
        symbol_key = str(symbol or "").strip().upper()
        if not symbol_key:
            continue

        selected = mt5.symbol_select(symbol_key, True)
        info = mt5.symbol_info(symbol_key)
        tick = mt5.symbol_info_tick(symbol_key)
        has_live_quote = _is_live_tick(tick)

        if not include_inactive and not has_live_quote:
            continue

        bid = float(getattr(tick, "bid", 0.0) or 0.0) if tick is not None else 0.0
        ask = float(getattr(tick, "ask", 0.0) or 0.0) if tick is not None else 0.0
        last = float(getattr(tick, "last", 0.0) or 0.0) if tick is not None else 0.0
        latest = last if last > 0 else ((bid + ask) / 2.0 if max(bid, ask) > 0 else 0.0)
        spread = float(getattr(info, "spread", 0.0) or 0.0) if info is not None else 0.0
        point = float(getattr(info, "point", 0.0) or 0.0) if info is not None else 0.0
        tick_time = int(getattr(tick, "time", 0) or 0) if tick is not None else 0

        if info is None:
            status = "未识别品种"
        elif not selected:
            status = "未加入市场报价"
        elif has_live_quote:
            status = "实时报价"
        else:
            status = "休市或暂无实时报价"

        rows.append(
            {
                "symbol": symbol_key,
                "latest_price": latest,
                "bid": bid,
                "ask": ask,
                "spread_points": spread,
                "point": point,
                "tick_time": tick_time,
                "status": status,
                "has_live_quote": has_live_quote,
            }
        )
    return rows
