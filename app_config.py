import os
import json
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv, set_key

PROJECT_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_DIR / ".env"
DEFAULT_SYMBOLS = ["XAUUSD", "XAGUSD", "EURUSD", "USDJPY"]
DEFAULT_QUOTE_RISK_THRESHOLDS = {
    "XAU": {"warn_points": 45.0, "alert_points": 70.0, "warn_pct": 0.018, "alert_pct": 0.030},
    "XAG": {"warn_points": 80.0, "alert_points": 120.0, "warn_pct": 0.040, "alert_pct": 0.065},
    "FX": {"warn_points": 25.0, "alert_points": 40.0, "warn_pct": 0.020, "alert_pct": 0.035},
}
DEFAULT_SIM_STRATEGY_MIN_RR = {
    "early_momentum": 1.30,
    "direct_momentum": 1.40,
    "pullback_sniper_probe": 1.45,
    "directional_probe": 1.80,
    "structure": 1.80,
}
DEFAULT_SIM_STRATEGY_DAILY_LIMIT = {
    "early_momentum": 3,
    "direct_momentum": 3,
    "pullback_sniper_probe": 3,
    "directional_probe": 3,
    "structure": 3,
}
DEFAULT_SIM_STRATEGY_COOLDOWN_MIN = {
    "early_momentum": 10,
    "direct_momentum": 10,
    "pullback_sniper_probe": 10,
    "directional_probe": 10,
    "structure": 10,
}
EVENT_RISK_MODES = {
    "normal": "正常观察",
    "pre_event": "事件前高敏",
    "post_event": "事件落地观察",
    "illiquid": "流动性偏弱",
}
LEGACY_PROJECT_DIR = PROJECT_DIR.parent
LEGACY_ENV_FILE = LEGACY_PROJECT_DIR / ".env"
LEGACY_RUNTIME_CONFIG = LEGACY_PROJECT_DIR / ".runtime" / "config.json"
LEGACY_MIGRATION_DONE_KEY = "LEGACY_MIGRATION_DONE"


def load_project_env() -> Path:
    """加载独立项目目录下的 .env。"""
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    migrate_legacy_ai_settings_if_needed()
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    return ENV_FILE


def _dedupe_keep_order(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

# 常见品种的中文别名映射辞典
SYMBOL_ALIAS_MAP = {
    "黄金": "XAUUSD",
    "白银": "XAGUSD",
    "欧美": "EURUSD",
    "欧元": "EURUSD",
    "美日": "USDJPY",
    "日元": "USDJPY",
    "磅美": "GBPUSD",
    "镑美": "GBPUSD",
    "英镑": "GBPUSD",
    "澳美": "AUDUSD",
    "原油": "USOIL",
    "比特币": "BTCUSD",
    "以太坊": "ETHUSD",
}

def extract_supported_symbols(raw_text: str) -> list[str]:
    """过滤并去重，保留用户输入的品种，支持中文别名转换。"""
    raw = str(raw_text or "").replace("；", ",").replace("，", ",").replace(" ", ",")
    cleaned = []
    for item in raw.split(","):
        symbol = str(item or "").strip()
        if symbol:
            # 先去映射表里找找有没有匹配的中文（部分大写化不影响中文）
            mapped_symbol = SYMBOL_ALIAS_MAP.get(symbol, symbol).upper()
            cleaned.append(mapped_symbol)
    return _dedupe_keep_order(cleaned)


def normalize_symbols(raw_text: str, fallback_to_defaults: bool = True) -> list[str]:
    """仅保留贵金属监控项目支持的 MT5 标准品种。"""
    cleaned = extract_supported_symbols(raw_text)
    if cleaned:
        return cleaned
    return list(DEFAULT_SYMBOLS) if bool(fallback_to_defaults) else []


def get_quote_risk_thresholds(symbol: str) -> dict[str, float]:
    """读取点差风险阈值，优先使用环境变量覆盖。"""
    load_project_env()
    payload = str(os.getenv("QUOTE_RISK_THRESHOLDS_JSON", "") or "").strip()
    overrides = {}
    if payload:
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                overrides = parsed
        except json.JSONDecodeError:
            overrides = {}

    symbol_key = str(symbol or "").strip().upper()
    family_key = "FX"
    if symbol_key.startswith("XAU"):
        family_key = "XAU"
    elif symbol_key.startswith("XAG"):
        family_key = "XAG"

    merged = dict(DEFAULT_QUOTE_RISK_THRESHOLDS.get(family_key, DEFAULT_QUOTE_RISK_THRESHOLDS["FX"]))
    override_payload = overrides.get(family_key, {}) if isinstance(overrides, dict) else {}
    if isinstance(override_payload, dict):
        for key in ("warn_points", "alert_points", "warn_pct", "alert_pct"):
            value = override_payload.get(key)
            try:
                if value is not None:
                    merged[key] = float(value)
            except (TypeError, ValueError):
                continue
    return merged


@dataclass
class MetalMonitorConfig:
    symbols: list[str]
    refresh_interval_sec: int
    event_risk_mode: str
    mt5_path: str
    mt5_login: str
    mt5_password: str
    mt5_server: str
    dingtalk_webhook: str
    pushplus_token: str
    notify_cooldown_min: int
    ai_api_key: str
    ai_api_base: str
    ai_model: str
    ai_push_enabled: bool
    ai_push_summary_only: bool
    ai_auto_interval_min: int = 0
    event_auto_mode_enabled: bool = False
    event_schedule_text: str = ""
    event_pre_window_min: int = 30
    event_post_window_min: int = 15
    event_feed_enabled: bool = False
    event_feed_url: str = ""
    event_feed_refresh_min: int = 60
    macro_news_feed_enabled: bool = False
    macro_news_feed_urls: str = ""
    macro_news_feed_refresh_min: int = 30
    macro_data_feed_enabled: bool = False
    macro_data_feed_specs: str = ""
    macro_data_feed_refresh_min: int = 60
    learning_push_enabled: bool = False
    learning_push_min_interval_hour: int = 12
    notify_dnd_enabled: bool = True
    notify_dnd_start_hour: int = 0
    notify_dnd_end_hour: int = 7
    overnight_spread_guard_enabled: bool = True
    overnight_spread_guard_start_hour: int = 5
    overnight_spread_guard_end_hour: int = 7
    trade_mode: str = "simulation"
    live_max_drawdown_pct: float = 0.05
    live_order_precheck_only: bool = True
    live_max_open_positions: int = 1
    live_max_orders_per_day: int = 3
    sim_initial_balance: float = 1000.0
    sim_no_tp2_lock_r: float = 0.5
    sim_no_tp2_partial_close_ratio: float = 0.5
    sim_min_rr: float = 1.6
    sim_relaxed_rr: float = 1.3
    sim_model_min_probability: float = 0.68
    sim_exploratory_daily_limit: int = 3
    sim_exploratory_cooldown_min: int = 10
    sim_exploratory_base_balance: float = 1000.0
    sim_strategy_min_rr: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SIM_STRATEGY_MIN_RR))
    sim_strategy_daily_limit: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SIM_STRATEGY_DAILY_LIMIT))
    sim_strategy_cooldown_min: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SIM_STRATEGY_COOLDOWN_MIN))


def _clean_env_value(value: object) -> str:
    return str(value or "").strip().strip("'\"")


def _quote_env_value(value: object) -> str:
    text = str(value or "")
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"'{escaped}'"


def _atomic_write_env_values(values: dict[str, str], target: Path | None = None) -> None:
    """原子写入 .env，避免保存设置时中断导致配置文件被截断。"""
    env_path = Path(target) if target else ENV_FILE
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={_quote_env_value(value)}" for key, value in sorted(values.items())]
    temp_file = env_path.with_name(f"{env_path.name}.tmp")
    temp_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp_file.replace(env_path)


def _read_legacy_runtime_config() -> dict:
    if not LEGACY_RUNTIME_CONFIG.exists():
        return {}
    try:
        payload = json.loads(LEGACY_RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_legacy_ai_payload() -> dict:
    legacy_runtime = _read_legacy_runtime_config()
    legacy_env = dotenv_values(str(LEGACY_ENV_FILE)) if LEGACY_ENV_FILE.exists() else {}
    return {
        "AI_API_KEY": _clean_env_value(legacy_env.get("AI_API_KEY", "")),
        "AI_API_BASE": _clean_env_value(legacy_runtime.get("AI_BASE_URL", "")),
        "AI_MODEL": _clean_env_value(legacy_runtime.get("AI_MODEL", "")),
        "DINGTALK_WEBHOOK": _clean_env_value(legacy_env.get("DINGTALK_WEBHOOK", "")),
        "PUSHPLUS_TOKEN": _clean_env_value(legacy_env.get("PUSHPLUS_TOKEN", "")),
        "MT5_PATH": _clean_env_value(legacy_env.get("MT5_PATH", "")),
        "MT5_LOGIN": _clean_env_value(legacy_env.get("MT5_LOGIN", "")),
        "MT5_PASSWORD": _clean_env_value(legacy_env.get("MT5_PASSWORD", "")),
        "MT5_SERVER": _clean_env_value(legacy_env.get("MT5_SERVER", "")),
        "TARGET_SYMBOLS": ",".join(normalize_symbols(",".join(list(legacy_runtime.get("TARGET_SYMBOLS", []) or [])))),
    }


def migrate_legacy_ai_settings_if_needed() -> bool:
    """若独立项目缺少关键配置，则尝试从老项目自动迁移一次。"""
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        ENV_FILE.write_text("", encoding="utf-8")

    current = dotenv_values(str(ENV_FILE))
    if _clean_env_value(current.get(LEGACY_MIGRATION_DONE_KEY, "")) == "1":
        return False
    current_key = _clean_env_value(current.get("AI_API_KEY", ""))
    current_base = _clean_env_value(current.get("AI_API_BASE", ""))
    current_model = _clean_env_value(current.get("AI_MODEL", ""))
    current_webhook = _clean_env_value(current.get("DINGTALK_WEBHOOK", ""))
    current_pushplus = _clean_env_value(current.get("PUSHPLUS_TOKEN", ""))
    current_mt5_path = _clean_env_value(current.get("MT5_PATH", ""))
    current_mt5_login = _clean_env_value(current.get("MT5_LOGIN", ""))
    current_mt5_password = _clean_env_value(current.get("MT5_PASSWORD", ""))
    current_mt5_server = _clean_env_value(current.get("MT5_SERVER", ""))
    current_symbols = _clean_env_value(current.get("TARGET_SYMBOLS", ""))
    if (
        current_key and current_base and current_model
        and current_webhook
        and current_mt5_server
        and current_symbols
    ):
        return False

    legacy = _extract_legacy_ai_payload()
    migrated = False
    if legacy.get("AI_API_KEY") and not current_key:
        set_key(str(ENV_FILE), "AI_API_KEY", legacy["AI_API_KEY"])
        migrated = True
    if legacy.get("AI_API_BASE") and not current_base:
        set_key(str(ENV_FILE), "AI_API_BASE", legacy["AI_API_BASE"])
        migrated = True
    if legacy.get("AI_MODEL") and not current_model:
        set_key(str(ENV_FILE), "AI_MODEL", legacy["AI_MODEL"])
        migrated = True
    if legacy.get("DINGTALK_WEBHOOK") and not current_webhook:
        set_key(str(ENV_FILE), "DINGTALK_WEBHOOK", legacy["DINGTALK_WEBHOOK"])
        migrated = True
    if legacy.get("PUSHPLUS_TOKEN") and not current_pushplus:
        set_key(str(ENV_FILE), "PUSHPLUS_TOKEN", legacy["PUSHPLUS_TOKEN"])
        migrated = True
    if legacy.get("MT5_PATH") and not current_mt5_path:
        set_key(str(ENV_FILE), "MT5_PATH", legacy["MT5_PATH"])
        migrated = True
    if legacy.get("MT5_LOGIN") and not current_mt5_login:
        set_key(str(ENV_FILE), "MT5_LOGIN", legacy["MT5_LOGIN"])
        migrated = True
    if legacy.get("MT5_PASSWORD") and not current_mt5_password:
        set_key(str(ENV_FILE), "MT5_PASSWORD", legacy["MT5_PASSWORD"])
        migrated = True
    if legacy.get("MT5_SERVER") and not current_mt5_server:
        set_key(str(ENV_FILE), "MT5_SERVER", legacy["MT5_SERVER"])
        migrated = True
    if legacy.get("TARGET_SYMBOLS") and not current_symbols:
        set_key(str(ENV_FILE), "TARGET_SYMBOLS", legacy["TARGET_SYMBOLS"])
        migrated = True
    if migrated:
        set_key(str(ENV_FILE), LEGACY_MIGRATION_DONE_KEY, "1")
        os.environ[LEGACY_MIGRATION_DONE_KEY] = "1"
    return migrated


def _parse_bool_env(name: str, default: bool = False) -> bool:
    text = str(os.getenv(name, "1" if default else "0") or ("1" if default else "0")).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _parse_int_env(name: str, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip() or str(default))
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def normalize_sim_strategy_min_rr(value: object | None = None) -> dict[str, float]:
    result = dict(DEFAULT_SIM_STRATEGY_MIN_RR)
    payload = value
    if payload is None:
        payload = os.getenv("SIM_STRATEGY_MIN_RR_JSON", "")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return result
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return result
    if not isinstance(payload, dict):
        return result
    for key, raw_value in payload.items():
        clean_key = str(key or "").strip().lower()
        if not clean_key:
            continue
        try:
            result[clean_key] = max(0.50, min(10.0, float(raw_value)))
        except (TypeError, ValueError):
            continue
    return result


def normalize_sim_strategy_daily_limit(value: object | None = None) -> dict[str, int]:
    result = dict(DEFAULT_SIM_STRATEGY_DAILY_LIMIT)
    payload = value
    if payload is None:
        payload = os.getenv("SIM_STRATEGY_DAILY_LIMIT_JSON", "")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return result
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return result
    if not isinstance(payload, dict):
        return result
    for key, raw_value in payload.items():
        clean_key = str(key or "").strip().lower()
        if not clean_key:
            continue
        try:
            result[clean_key] = max(0, min(50, int(raw_value)))
        except (TypeError, ValueError):
            continue
    return result


def normalize_sim_strategy_cooldown_min(value: object | None = None) -> dict[str, int]:
    result = dict(DEFAULT_SIM_STRATEGY_COOLDOWN_MIN)
    payload = value
    if payload is None:
        payload = os.getenv("SIM_STRATEGY_COOLDOWN_JSON", "")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return result
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return result
    if not isinstance(payload, dict):
        return result
    for key, raw_value in payload.items():
        clean_key = str(key or "").strip().lower()
        if not clean_key:
            continue
        try:
            result[clean_key] = max(0, min(240, int(raw_value)))
        except (TypeError, ValueError):
            continue
    return result


def get_sim_strategy_min_rr(strategy_family: str, default: float | None = None, config: MetalMonitorConfig | None = None) -> float:
    clean_key = str(strategy_family or "").strip().lower()
    fallback = float(default if default is not None else DEFAULT_SIM_STRATEGY_MIN_RR.get(clean_key, 1.60))
    source = getattr(config, "sim_strategy_min_rr", None) if config is not None else None
    rr_map = normalize_sim_strategy_min_rr(source)
    return float(rr_map.get(clean_key, fallback) or fallback)


def get_sim_strategy_daily_limit(strategy_family: str, default: int | None = None, config: MetalMonitorConfig | None = None) -> int:
    clean_key = str(strategy_family or "").strip().lower()
    fallback = int(default if default is not None else DEFAULT_SIM_STRATEGY_DAILY_LIMIT.get(clean_key, 3))
    source = getattr(config, "sim_strategy_daily_limit", None) if config is not None else None
    limit_map = normalize_sim_strategy_daily_limit(source)
    return int(limit_map.get(clean_key, fallback) or fallback)


def get_sim_strategy_cooldown_min(strategy_family: str, default: int | None = None, config: MetalMonitorConfig | None = None) -> int:
    clean_key = str(strategy_family or "").strip().lower()
    fallback = int(default if default is not None else DEFAULT_SIM_STRATEGY_COOLDOWN_MIN.get(clean_key, 10))
    source = getattr(config, "sim_strategy_cooldown_min", None) if config is not None else None
    cooldown_map = normalize_sim_strategy_cooldown_min(source)
    return int(cooldown_map.get(clean_key, fallback) or fallback)


def normalize_event_risk_mode(value: str) -> str:
    key = str(value or "").strip().lower()
    return key if key in EVENT_RISK_MODES else "normal"


def get_runtime_config() -> MetalMonitorConfig:
    load_project_env()
    symbols = normalize_symbols(os.getenv("TARGET_SYMBOLS", ",".join(DEFAULT_SYMBOLS)))
    try:
        refresh_interval_sec = max(5, int(str(os.getenv("REFRESH_INTERVAL_SEC", "30") or "30").strip()))
    except ValueError:
        refresh_interval_sec = 30
    try:
        notify_cooldown_min = max(5, int(str(os.getenv("NOTIFY_COOLDOWN_MIN", "30") or "30").strip()))
    except ValueError:
        notify_cooldown_min = 30
    try:
        event_pre_window_min = max(5, int(str(os.getenv("EVENT_PRE_WINDOW_MIN", "30") or "30").strip()))
    except ValueError:
        event_pre_window_min = 30
    try:
        event_post_window_min = max(5, int(str(os.getenv("EVENT_POST_WINDOW_MIN", "15") or "15").strip()))
    except ValueError:
        event_post_window_min = 15
    try:
        event_feed_refresh_min = max(5, int(str(os.getenv("EVENT_FEED_REFRESH_MIN", "60") or "60").strip()))
    except ValueError:
        event_feed_refresh_min = 60
    try:
        macro_news_feed_refresh_min = max(5, int(str(os.getenv("MACRO_NEWS_FEED_REFRESH_MIN", "30") or "30").strip()))
    except ValueError:
        macro_news_feed_refresh_min = 30
    try:
        macro_data_feed_refresh_min = max(5, int(str(os.getenv("MACRO_DATA_FEED_REFRESH_MIN", "60") or "60").strip()))
    except ValueError:
        macro_data_feed_refresh_min = 60
    try:
        learning_push_min_interval_hour = max(
            1,
            int(str(os.getenv("LEARNING_PUSH_MIN_INTERVAL_HOUR", "12") or "12").strip()),
        )
    except ValueError:
        learning_push_min_interval_hour = 12
    try:
        notify_dnd_start_hour = min(23, max(0, int(str(os.getenv("NOTIFY_DND_START_HOUR", "0") or "0").strip())))
    except ValueError:
        notify_dnd_start_hour = 0
    try:
        notify_dnd_end_hour = min(23, max(0, int(str(os.getenv("NOTIFY_DND_END_HOUR", "7") or "7").strip())))
    except ValueError:
        notify_dnd_end_hour = 7
    try:
        overnight_spread_guard_start_hour = min(
            23,
            max(0, int(str(os.getenv("OVERNIGHT_SPREAD_GUARD_START_HOUR", "5") or "5").strip())),
        )
    except ValueError:
        overnight_spread_guard_start_hour = 5
    try:
        overnight_spread_guard_end_hour = min(
            23,
            max(0, int(str(os.getenv("OVERNIGHT_SPREAD_GUARD_END_HOUR", "7") or "7").strip())),
        )
    except ValueError:
        overnight_spread_guard_end_hour = 7

    try:
        live_max_drawdown_pct = max(0.01, min(0.99, float(str(os.getenv("LIVE_MAX_DRAWDOWN_PCT", "0.05") or "0.05").strip())))
    except ValueError:
        live_max_drawdown_pct = 0.05
    try:
        sim_initial_balance = max(
            100.0,
            min(1000000.0, float(str(os.getenv("SIM_INITIAL_BALANCE", "1000") or "1000").strip())),
        )
    except ValueError:
        sim_initial_balance = 1000.0
    try:
        sim_no_tp2_lock_r = max(0.10, min(5.0, float(str(os.getenv("SIM_NO_TP2_LOCK_R", "0.5") or "0.5").strip())))
    except ValueError:
        sim_no_tp2_lock_r = 0.5
    try:
        sim_no_tp2_partial_close_ratio = max(
            0.10,
            min(0.90, float(str(os.getenv("SIM_NO_TP2_PARTIAL_CLOSE_RATIO", "0.5") or "0.5").strip())),
        )
    except ValueError:
        sim_no_tp2_partial_close_ratio = 0.5
    try:
        sim_min_rr = max(0.50, min(10.0, float(str(os.getenv("SIM_MIN_RR", "1.6") or "1.6").strip())))
    except ValueError:
        sim_min_rr = 1.6
    try:
        sim_relaxed_rr = max(0.50, min(10.0, float(str(os.getenv("SIM_RELAXED_RR", "1.3") or "1.3").strip())))
    except ValueError:
        sim_relaxed_rr = 1.3
    try:
        sim_model_min_probability = max(
            0.0,
            min(1.0, float(str(os.getenv("SIM_MODEL_MIN_PROBABILITY", "0.68") or "0.68").strip())),
        )
    except ValueError:
        sim_model_min_probability = 0.68
    sim_exploratory_daily_limit = _parse_int_env("SIM_EXPLORATORY_DAILY_LIMIT", default=3, minimum=0, maximum=50)
    sim_exploratory_cooldown_min = _parse_int_env("SIM_EXPLORATORY_COOLDOWN_MIN", default=10, minimum=0, maximum=240)
    sim_strategy_min_rr = normalize_sim_strategy_min_rr()
    sim_strategy_daily_limit = normalize_sim_strategy_daily_limit()
    sim_strategy_cooldown_min = normalize_sim_strategy_cooldown_min()
    try:
        sim_exploratory_base_balance = max(
            100.0,
            min(
                1000000.0,
                float(str(os.getenv("SIM_EXPLORATORY_BASE_BALANCE", str(sim_initial_balance)) or str(sim_initial_balance)).strip()),
            ),
        )
    except ValueError:
        sim_exploratory_base_balance = sim_initial_balance

    return MetalMonitorConfig(
        symbols=symbols,
        refresh_interval_sec=refresh_interval_sec,
        event_risk_mode=normalize_event_risk_mode(os.getenv("EVENT_RISK_MODE", "normal")),
        mt5_path=str(os.getenv("MT5_PATH", "") or "").strip(),
        mt5_login=str(os.getenv("MT5_LOGIN", "") or "").strip(),
        mt5_password=str(os.getenv("MT5_PASSWORD", "") or "").strip(),
        mt5_server=str(os.getenv("MT5_SERVER", "") or "").strip(),
        dingtalk_webhook=str(os.getenv("DINGTALK_WEBHOOK", "") or "").strip(),
        pushplus_token=str(os.getenv("PUSHPLUS_TOKEN", "") or "").strip(),
        notify_cooldown_min=notify_cooldown_min,
        ai_api_key=str(os.getenv("AI_API_KEY", "") or "").strip(),
        ai_api_base=str(os.getenv("AI_API_BASE", "https://api.siliconflow.cn/v1") or "https://api.siliconflow.cn/v1").strip(),
        ai_model=str(os.getenv("AI_MODEL", "deepseek-ai/DeepSeek-R1") or "deepseek-ai/DeepSeek-R1").strip(),
        ai_push_enabled=_parse_bool_env("AI_PUSH_ENABLED", default=False),
        ai_push_summary_only=_parse_bool_env("AI_PUSH_SUMMARY_ONLY", default=True),
        ai_auto_interval_min=_parse_int_env("AI_AUTO_INTERVAL_MIN", default=0, minimum=0),
        event_auto_mode_enabled=_parse_bool_env("EVENT_AUTO_MODE_ENABLED", default=False),
        event_schedule_text=str(os.getenv("EVENT_SCHEDULES", "") or "").strip(),
        event_pre_window_min=event_pre_window_min,
        event_post_window_min=event_post_window_min,
        event_feed_enabled=_parse_bool_env("EVENT_FEED_ENABLED", default=False),
        event_feed_url=str(os.getenv("EVENT_FEED_URL", "") or "").strip(),
        event_feed_refresh_min=event_feed_refresh_min,
        macro_news_feed_enabled=_parse_bool_env("MACRO_NEWS_FEED_ENABLED", default=False),
        macro_news_feed_urls=str(os.getenv("MACRO_NEWS_FEED_URLS", "") or "").strip(),
        macro_news_feed_refresh_min=macro_news_feed_refresh_min,
        macro_data_feed_enabled=_parse_bool_env("MACRO_DATA_FEED_ENABLED", default=False),
        macro_data_feed_specs=str(os.getenv("MACRO_DATA_FEED_SPECS", "") or "").strip(),
        macro_data_feed_refresh_min=macro_data_feed_refresh_min,
        learning_push_enabled=_parse_bool_env("LEARNING_PUSH_ENABLED", default=False),
        learning_push_min_interval_hour=learning_push_min_interval_hour,
        notify_dnd_enabled=_parse_bool_env("NOTIFY_DND_ENABLED", default=True),
        notify_dnd_start_hour=notify_dnd_start_hour,
        notify_dnd_end_hour=notify_dnd_end_hour,
        overnight_spread_guard_enabled=_parse_bool_env("OVERNIGHT_SPREAD_GUARD_ENABLED", default=True),
        overnight_spread_guard_start_hour=overnight_spread_guard_start_hour,
        overnight_spread_guard_end_hour=overnight_spread_guard_end_hour,
        trade_mode=str(os.getenv("TRADE_MODE", "simulation") or "simulation").strip().lower(),
        live_max_drawdown_pct=live_max_drawdown_pct,
        live_order_precheck_only=_parse_bool_env("LIVE_ORDER_PRECHECK_ONLY", default=True),
        live_max_open_positions=_parse_int_env("LIVE_MAX_OPEN_POSITIONS", default=1, minimum=1, maximum=20),
        live_max_orders_per_day=_parse_int_env("LIVE_MAX_ORDERS_PER_DAY", default=3, minimum=1, maximum=100),
        sim_initial_balance=sim_initial_balance,
        sim_no_tp2_lock_r=sim_no_tp2_lock_r,
        sim_no_tp2_partial_close_ratio=sim_no_tp2_partial_close_ratio,
        sim_min_rr=sim_min_rr,
        sim_relaxed_rr=sim_relaxed_rr,
        sim_model_min_probability=sim_model_min_probability,
        sim_exploratory_daily_limit=sim_exploratory_daily_limit,
        sim_exploratory_cooldown_min=sim_exploratory_cooldown_min,
        sim_exploratory_base_balance=sim_exploratory_base_balance,
        sim_strategy_min_rr=sim_strategy_min_rr,
        sim_strategy_daily_limit=sim_strategy_daily_limit,
        sim_strategy_cooldown_min=sim_strategy_cooldown_min,
    )


def _set_env_key(key: str, value: str) -> None:
    """同步写入 .env 文件与内存环境变量，消除重复赋值。"""
    set_key(str(ENV_FILE), key, value)
    os.environ[key] = value


def _apply_env_updates(updates: dict[str, str]) -> None:
    current = {
        str(key): _clean_env_value(value)
        for key, value in dict(dotenv_values(str(ENV_FILE)) if ENV_FILE.exists() else {}).items()
        if key is not None
    }
    for key, value in dict(updates or {}).items():
        current[str(key)] = str(value)
        os.environ[str(key)] = str(value)
    _atomic_write_env_values(current, ENV_FILE)


def save_runtime_config(config: MetalMonitorConfig) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        _atomic_write_env_values({}, ENV_FILE)

    symbols_text = ",".join(normalize_symbols(",".join(config.symbols)))
    _apply_env_updates(
        {
            "TARGET_SYMBOLS": symbols_text,
            "REFRESH_INTERVAL_SEC": str(max(5, int(config.refresh_interval_sec))),
            "EVENT_RISK_MODE": normalize_event_risk_mode(config.event_risk_mode),
            "MT5_PATH": str(config.mt5_path or "").strip(),
            "MT5_LOGIN": str(config.mt5_login or "").strip(),
            "MT5_PASSWORD": str(config.mt5_password or "").strip(),
            "MT5_SERVER": str(config.mt5_server or "").strip(),
            "DINGTALK_WEBHOOK": str(config.dingtalk_webhook or "").strip(),
            "PUSHPLUS_TOKEN": str(config.pushplus_token or "").strip(),
            "NOTIFY_COOLDOWN_MIN": str(max(5, int(config.notify_cooldown_min))),
            "AI_API_KEY": str(config.ai_api_key or "").strip(),
            "AI_API_BASE": str(config.ai_api_base or "").strip(),
            "AI_MODEL": str(config.ai_model or "").strip(),
            "AI_PUSH_ENABLED": "1" if bool(config.ai_push_enabled) else "0",
            "AI_PUSH_SUMMARY_ONLY": "1" if bool(config.ai_push_summary_only) else "0",
            "AI_AUTO_INTERVAL_MIN": str(max(0, int(config.ai_auto_interval_min))),
            "EVENT_AUTO_MODE_ENABLED": "1" if bool(config.event_auto_mode_enabled) else "0",
            "EVENT_SCHEDULES": str(config.event_schedule_text or "").strip(),
            "EVENT_PRE_WINDOW_MIN": str(max(5, int(config.event_pre_window_min))),
            "EVENT_POST_WINDOW_MIN": str(max(5, int(config.event_post_window_min))),
            "EVENT_FEED_ENABLED": "1" if bool(config.event_feed_enabled) else "0",
            "EVENT_FEED_URL": str(config.event_feed_url or "").strip(),
            "EVENT_FEED_REFRESH_MIN": str(max(5, int(config.event_feed_refresh_min))),
            "MACRO_NEWS_FEED_ENABLED": "1" if bool(config.macro_news_feed_enabled) else "0",
            "MACRO_NEWS_FEED_URLS": str(config.macro_news_feed_urls or "").strip(),
            "MACRO_NEWS_FEED_REFRESH_MIN": str(max(5, int(config.macro_news_feed_refresh_min))),
            "MACRO_DATA_FEED_ENABLED": "1" if bool(config.macro_data_feed_enabled) else "0",
            "MACRO_DATA_FEED_SPECS": str(config.macro_data_feed_specs or "").strip(),
            "MACRO_DATA_FEED_REFRESH_MIN": str(max(5, int(config.macro_data_feed_refresh_min))),
            "LEARNING_PUSH_ENABLED": "1" if bool(config.learning_push_enabled) else "0",
            "LEARNING_PUSH_MIN_INTERVAL_HOUR": str(max(1, int(config.learning_push_min_interval_hour))),
            "NOTIFY_DND_ENABLED": "1" if bool(config.notify_dnd_enabled) else "0",
            "NOTIFY_DND_START_HOUR": str(min(23, max(0, int(config.notify_dnd_start_hour)))),
            "NOTIFY_DND_END_HOUR": str(min(23, max(0, int(config.notify_dnd_end_hour)))),
            "OVERNIGHT_SPREAD_GUARD_ENABLED": "1" if bool(config.overnight_spread_guard_enabled) else "0",
            "OVERNIGHT_SPREAD_GUARD_START_HOUR": str(min(23, max(0, int(config.overnight_spread_guard_start_hour)))),
            "OVERNIGHT_SPREAD_GUARD_END_HOUR": str(min(23, max(0, int(config.overnight_spread_guard_end_hour)))),
            "TRADE_MODE": str(config.trade_mode),
            "LIVE_MAX_DRAWDOWN_PCT": str(config.live_max_drawdown_pct),
            "LIVE_ORDER_PRECHECK_ONLY": "1" if bool(config.live_order_precheck_only) else "0",
            "LIVE_MAX_OPEN_POSITIONS": str(max(1, min(20, int(config.live_max_open_positions)))),
            "LIVE_MAX_ORDERS_PER_DAY": str(max(1, min(100, int(config.live_max_orders_per_day)))),
            "SIM_INITIAL_BALANCE": str(max(100.0, min(1000000.0, float(config.sim_initial_balance)))),
            "SIM_NO_TP2_LOCK_R": str(max(0.10, min(5.0, float(config.sim_no_tp2_lock_r)))),
            "SIM_NO_TP2_PARTIAL_CLOSE_RATIO": str(
                max(0.10, min(0.90, float(config.sim_no_tp2_partial_close_ratio)))
            ),
            "SIM_MIN_RR": str(max(0.50, min(10.0, float(config.sim_min_rr)))),
            "SIM_RELAXED_RR": str(max(0.50, min(10.0, float(config.sim_relaxed_rr)))),
            "SIM_MODEL_MIN_PROBABILITY": str(max(0.0, min(1.0, float(config.sim_model_min_probability)))),
            "SIM_EXPLORATORY_DAILY_LIMIT": str(max(0, min(50, int(config.sim_exploratory_daily_limit)))),
            "SIM_EXPLORATORY_COOLDOWN_MIN": str(max(0, min(240, int(config.sim_exploratory_cooldown_min)))),
            "SIM_EXPLORATORY_BASE_BALANCE": str(
                max(100.0, min(1000000.0, float(config.sim_exploratory_base_balance)))
            ),
            "SIM_STRATEGY_MIN_RR_JSON": json.dumps(
                normalize_sim_strategy_min_rr(getattr(config, "sim_strategy_min_rr", {})),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "SIM_STRATEGY_DAILY_LIMIT_JSON": json.dumps(
                normalize_sim_strategy_daily_limit(getattr(config, "sim_strategy_daily_limit", {})),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "SIM_STRATEGY_COOLDOWN_JSON": json.dumps(
                normalize_sim_strategy_cooldown_min(getattr(config, "sim_strategy_cooldown_min", {})),
                ensure_ascii=False,
                sort_keys=True,
            ),
            LEGACY_MIGRATION_DONE_KEY: "1",
        }
    )
