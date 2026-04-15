"""
signal_enums.py — 系统级状态枚举常量统一管理。

目的：消灭业务逻辑中散落的"魔法字符串"（可轻仓试仓 / warning / pre_event 等），
      用强类型枚举替代，享受 IDE 提示和防止拼写错误。

设计约定：所有枚举均继承 str，因此可与现存字符串比较完全等价，支持渐进式迁移：
    grade == "可轻仓试仓"           ← 旧写法，仍然有效
    grade == TradeGrade.LIGHT_POSITION  ← 新写法，推荐使用
"""
from __future__ import annotations

from enum import Enum


class TradeGrade(str, Enum):
    """出手分级枚举。对应 monitor_rules.build_trade_grade 的 grade 字段。"""
    NO_TRADE       = "当前不宜出手"
    WAIT_EVENT     = "等待事件落地"
    OBSERVE_ONLY   = "只适合观察"
    LIGHT_POSITION = "可轻仓试仓"


class AlertTone(str, Enum):
    """UI 提示色调枚举。对应 monitor_rules / monitor_engine 返回的 tone 字段。"""
    WARNING = "warning"   # 红/橙，阻断型
    ACCENT  = "accent"    # 黄，警告型
    SUCCESS = "success"   # 绿，放行型
    NEUTRAL = "neutral"   # 灰，默认观察
    NEGATIVE = "negative" # 对应连线失败等严重状态


class EventMode(str, Enum):
    """事件风险纪律模式枚举。对应 event_context["mode"] 字段。"""
    PRE_EVENT  = "pre_event"    # 事件前高敏期
    POST_EVENT = "post_event"   # 事件落地观察期
    ILLIQUID   = "illiquid"     # 流动性偏弱阶段
    NORMAL     = "normal"       # 正常观察


class EventModeText(str, Enum):
    """事件模式的中文显示文本枚举。对应 event_context["mode_text"] 字段。"""
    PRE_EVENT  = "事件前高敏"
    POST_EVENT = "事件落地观察"
    ILLIQUID   = "流动性偏弱"
    NORMAL     = "正常观察"


class MarketBias(str, Enum):
    """多周期偏向枚举。对应 intraday_bias / multi_timeframe_bias 字段。"""
    BULLISH  = "bullish"   # 偏多
    BEARISH  = "bearish"   # 偏空
    SIDEWAYS = "sideways"  # 震荡
    RANGE    = "range"     # 区间
    UNKNOWN  = "unknown"   # 待确认


class MarketAlignment(str, Enum):
    """多周期一致性枚举。对应 multi_timeframe_alignment 字段。"""
    ALIGNED = "aligned"   # 多周期同向
    MIXED   = "mixed"     # 多周期打架
    PARTIAL = "partial"   # 部分一致
    RANGE   = "range"     # 区间震荡
    UNKNOWN = "unknown"


class SignalSide(str, Enum):
    """交易方向枚举。对应 signal_side 字段。"""
    LONG    = "long"
    SHORT   = "short"
    NEUTRAL = "neutral"


class QuoteStatus(str, Enum):
    """报价状态码枚举。对应 quote_status_code 字段。"""
    LIVE = "live"
    INACTIVE = "inactive"
    UNKNOWN_SYMBOL = "unknown_symbol"
    NOT_SELECTED = "not_selected"
    ERROR = "error"


class AlertStateText(str, Enum):
    """Alert 状态显示文本枚举。对应 alert_state_text 字段。"""
    MARKET_CLOSED    = "休市 / 暂无报价"
    SPREAD_ABNORMAL  = "点差异常进行中"
    SPREAD_WIDE      = "点差偏宽观察"
    SPREAD_RECOVERED = "点差已恢复"
    PRE_EVENT        = "事件前"         # 前缀，后接事件类型
    POST_EVENT       = "事件后观察"     # 前缀，后接事件类型
    SETUP_CANDIDATE  = "结构候选"
    EVENT_WINDOW     = "事件窗口观察"
    NORMAL_WATCH     = "报价正常观察"


class AlertGradeSource(str, Enum):
    """出手分级来源枚举。对应 trade_grade_source 字段。"""
    CONNECTION = "connection"  # MT5 断连
    INACTIVE   = "inactive"   # 无活跃报价
    SPREAD     = "spread"     # 点差阻断
    EVENT      = "event"      # 事件窗口
    STRUCTURE  = "structure"  # 结构/技术分析
    SETUP      = "setup"      # 候选机会
    RISK       = "risk"       # 综合风险
