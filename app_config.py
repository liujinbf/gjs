import os
import json
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv, set_key

PROJECT_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_DIR / ".env"
DEFAULT_SYMBOLS = ["XAUUSD", "XAGUSD", "EURUSD", "USDJPY"]
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


def _clean_env_value(value: object) -> str:
    return str(value or "").strip().strip("'\"")


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
        ai_auto_interval_min=max(0, int(str(os.getenv("AI_AUTO_INTERVAL_MIN", "0") or "0").strip() or "0")),
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
    )


def _set_env_key(key: str, value: str) -> None:
    """同步写入 .env 文件与内存环境变量，消除重复赋值。"""
    set_key(str(ENV_FILE), key, value)
    os.environ[key] = value


def save_runtime_config(config: MetalMonitorConfig) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        ENV_FILE.write_text("", encoding="utf-8")

    symbols_text = ",".join(normalize_symbols(",".join(config.symbols)))
    _set_env_key("TARGET_SYMBOLS", symbols_text)
    _set_env_key("REFRESH_INTERVAL_SEC", str(max(5, int(config.refresh_interval_sec))))
    _set_env_key("EVENT_RISK_MODE", normalize_event_risk_mode(config.event_risk_mode))
    _set_env_key("MT5_PATH", str(config.mt5_path or "").strip())
    _set_env_key("MT5_LOGIN", str(config.mt5_login or "").strip())
    _set_env_key("MT5_PASSWORD", str(config.mt5_password or "").strip())
    _set_env_key("MT5_SERVER", str(config.mt5_server or "").strip())
    _set_env_key("DINGTALK_WEBHOOK", str(config.dingtalk_webhook or "").strip())
    _set_env_key("PUSHPLUS_TOKEN", str(config.pushplus_token or "").strip())
    _set_env_key("NOTIFY_COOLDOWN_MIN", str(max(5, int(config.notify_cooldown_min))))
    _set_env_key("AI_API_KEY", str(config.ai_api_key or "").strip())
    _set_env_key("AI_API_BASE", str(config.ai_api_base or "").strip())
    _set_env_key("AI_MODEL", str(config.ai_model or "").strip())
    _set_env_key("AI_PUSH_ENABLED", "1" if bool(config.ai_push_enabled) else "0")
    _set_env_key("AI_PUSH_SUMMARY_ONLY", "1" if bool(config.ai_push_summary_only) else "0")
    _set_env_key("AI_AUTO_INTERVAL_MIN", str(max(0, int(config.ai_auto_interval_min))))
    _set_env_key("EVENT_AUTO_MODE_ENABLED", "1" if bool(config.event_auto_mode_enabled) else "0")
    _set_env_key("EVENT_SCHEDULES", str(config.event_schedule_text or "").strip())
    _set_env_key("EVENT_PRE_WINDOW_MIN", str(max(5, int(config.event_pre_window_min))))
    _set_env_key("EVENT_POST_WINDOW_MIN", str(max(5, int(config.event_post_window_min))))
    _set_env_key("EVENT_FEED_ENABLED", "1" if bool(config.event_feed_enabled) else "0")
    _set_env_key("EVENT_FEED_URL", str(config.event_feed_url or "").strip())
    _set_env_key("EVENT_FEED_REFRESH_MIN", str(max(5, int(config.event_feed_refresh_min))))
    _set_env_key("MACRO_NEWS_FEED_ENABLED", "1" if bool(config.macro_news_feed_enabled) else "0")
    _set_env_key("MACRO_NEWS_FEED_URLS", str(config.macro_news_feed_urls or "").strip())
    _set_env_key("MACRO_NEWS_FEED_REFRESH_MIN", str(max(5, int(config.macro_news_feed_refresh_min))))
    _set_env_key("MACRO_DATA_FEED_ENABLED", "1" if bool(config.macro_data_feed_enabled) else "0")
    _set_env_key("MACRO_DATA_FEED_SPECS", str(config.macro_data_feed_specs or "").strip())
    _set_env_key("MACRO_DATA_FEED_REFRESH_MIN", str(max(5, int(config.macro_data_feed_refresh_min))))
    _set_env_key("LEARNING_PUSH_ENABLED", "1" if bool(config.learning_push_enabled) else "0")
    _set_env_key("LEARNING_PUSH_MIN_INTERVAL_HOUR", str(max(1, int(config.learning_push_min_interval_hour))))
    _set_env_key(LEGACY_MIGRATION_DONE_KEY, "1")
