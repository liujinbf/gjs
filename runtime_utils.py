"""
runtime_utils.py - 运行时公共工具函数。

P-004 修复：将多个模块中重复定义的 _parse_time 提取到此处，
统一维护，避免三份相同代码导致修改不一致的风险。
"""
from __future__ import annotations

from datetime import datetime

_SUPPORTED_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_time(value: str) -> datetime | None:
    """将字符串解析为 datetime，支持 'YYYY-MM-DD HH:MM:SS' / 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD'。
    解析失败时返回 None（而非抛出异常）。
    """
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _SUPPORTED_FMTS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def get_adaptive_filter_ratio() -> float:
    """
    自适应动态阈值乘数计算。
    基于 trade_learning_journal 数据库中的最后一笔平仓/开仓时间，
    以及最近 N 笔交易的表现：
    - 闲置时间（无任何成交）超过 12 小时：放宽过滤门槛 (ratio < 1.0)
    - 近期亏损（近 5 笔有 4 笔及以上亏损）：收紧防守门槛 (ratio > 1.0)
    - 兜底返回 ratio，作为所有风控门槛的乘数因子。
    """
    try:
        from app_config import get_runtime_config
        config = get_runtime_config()
        if not bool(getattr(config, "sim_adaptive_evolution_enabled", False)):
            return 1.0
    except Exception:
        return 1.0

    try:
        from knowledge_base import open_knowledge_connection
        with open_knowledge_connection() as conn:
            # 1. 查找最后一次交易发生的时间
            row = conn.execute(
                "SELECT opened_at FROM trade_learning_journal ORDER BY id DESC LIMIT 1"
            ).fetchone()
            
            # 2. 查找最近 5 笔已结算的订单盈亏
            recent_rows = conn.execute(
                "SELECT profit FROM trade_learning_journal ORDER BY id DESC LIMIT 5"
            ).fetchall()
    except Exception:
        return 1.0

    # 胜率与近期回撤防守计算
    loss_count = 0
    total_recent = len(recent_rows)
    for r in recent_rows:
        try:
            profit_val = float(r[0] if isinstance(r, tuple) else r.get("profit", 0.0) or 0.0)
            if profit_val < 0:
                loss_count += 1
        except Exception:
            pass
            
    # 防守乘数：如果最近连败严重（5笔亏4笔或以上，或者3笔均亏损）
    if (total_recent >= 3 and loss_count == total_recent) or (total_recent >= 5 and loss_count >= 4):
        # 强制防守：门槛收紧 20%
        return 1.2

    # 闲置时间评估
    if not row:
        # 如果历史没有任何交易，说明是新账户或无动作，自动下调 15% 门槛促进出单
        return 0.85
        
    last_time_str = row[0] if isinstance(row, tuple) else row.get("opened_at", "")
    last_time = parse_time(last_time_str)
    if not last_time:
        return 1.0
        
    idle_hours = (datetime.now() - last_time).total_seconds() / 3600.0
    
    # 闲置时间比例放宽
    if idle_hours > 24.0:
        return 0.70  # 闲置超过24小时，门槛放宽 30%
    elif idle_hours > 12.0:
        return 0.85  # 闲置超过12小时，门槛放宽 15%
        
    return 1.0

