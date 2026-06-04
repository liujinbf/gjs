import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
from app_config import MetalMonitorConfig
from signal_enums import TradeGrade, AlertTone
from monitor_rules import _build_scalp_ready_candidate

def _build_test_config(**kwargs) -> MetalMonitorConfig:
    cfg = MetalMonitorConfig(
        symbols=["XAUUSD", "XAGUSD"],
        refresh_interval_sec=30,
        event_risk_mode="normal",
        mt5_path="",
        mt5_login="",
        mt5_password="",
        mt5_server="",
        dingtalk_webhook="",
        pushplus_token="",
        notify_cooldown_min=30,
        ai_api_key="demo",
        ai_api_base="https://api.demo.com",
        ai_model="demo",
        ai_push_enabled=False,
        ai_push_summary_only=True,
    )
    for k, v in kwargs.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg

@patch("app_config.get_runtime_config")
@patch("app_config.load_ai_steering_cache")
@patch("monitor_rules._get_24h_avg_spread")
def test_scalp_ai_steering_filter_conflict(mock_get_spread, mock_load_ai, mock_get_cfg):
    """验证当 AI 意志与短线信号方向冲突时，被零延迟拦截并返回 OBSERVE_ONLY。"""
    # 模拟配置：启用 AI 异步舵手
    mock_get_cfg.return_value = _build_test_config(scalp_ai_steering_enabled=True)
    # 模拟 AI 偏多意志
    mock_load_ai.return_value = {"action": "long", "score": 85.0, "timestamp": 123456.0}
    # 模拟点差均值
    mock_get_spread.return_value = 18.0

    # 模拟短线就绪，但方向偏空
    row = {
        "scalp_ready": True,
        "scalp_direction": "short",
        "scalp_rr": 1.5,
        "scalp_confidence": "high",
        "scalp_signal_type": "momentum_reversal",
        "spread_points": 15.0,
        "point": 0.01,
        "atr5_m5": 0.85,  # 85点
        "contract_size": 100.0,
    }

    result = _build_scalp_ready_candidate("XAUUSD", row)
    assert result is not None
    assert result["grade"] == TradeGrade.OBSERVE_ONLY.value
    assert "冲突" in result["detail"]
    assert "AI 宏观意志 (LONG) 冲突" in result["detail"]
    assert result["setup_kind"] == "scalp_filter"
    assert result["scalp_setup_kind"] == "scalp_ai_filter"

@patch("app_config.get_runtime_config")
@patch("app_config.load_ai_steering_cache")
@patch("monitor_rules._get_24h_avg_spread")
def test_scalp_spread_guard_exceeded(mock_get_spread, mock_load_ai, mock_get_cfg):
    """验证当实时点差超过历史均值倍数限制时，被自适应点差风控拦截并返回 NO_TRADE。"""
    # 模拟配置
    mock_get_cfg.return_value = _build_test_config(
        scalp_ai_steering_enabled=True,
        scalp_max_spread_multiplier=1.5
    )
    # 模拟 AI 偏多，短线偏多（意志一致）
    mock_load_ai.return_value = {"action": "long", "score": 85.0, "timestamp": 123456.0}
    # 模拟点差均值为 18.0，最大允许点差就是 18.0 * 1.5 = 27.0
    mock_get_spread.return_value = 18.0

    # 模拟短线就绪，但实时点差达到 30 点
    row = {
        "scalp_ready": True,
        "scalp_direction": "long",
        "scalp_rr": 1.5,
        "scalp_confidence": "high",
        "scalp_signal_type": "momentum_breakout",
        "spread_points": 30.0,  # 30 > 27
        "point": 0.01,
        "atr5_m5": 0.85,
        "contract_size": 100.0,
    }

    result = _build_scalp_ready_candidate("XAUUSD", row)
    assert result is not None
    assert result["grade"] == TradeGrade.NO_TRADE.value
    assert "点差" in result["detail"]
    assert "避险拦截" in result["detail"]
    assert result["scalp_setup_kind"] == "scalp_spread_guard"

@patch("app_config.get_runtime_config")
@patch("app_config.load_ai_steering_cache")
@patch("monitor_rules._get_24h_avg_spread")
def test_scalp_cost_compensation_insufficient(mock_get_spread, mock_load_ai, mock_get_cfg):
    """验证当短线 ATR 空间不足以弥补总摩擦成本的指定倍数时，性价比不足拦截并返回 OBSERVE_ONLY。"""
    # 模拟配置：最小成本性价比倍数为 3.0，手续费单边 5.0
    mock_get_cfg.return_value = _build_test_config(
        scalp_ai_steering_enabled=False,
        scalp_min_reward_cost_ratio=3.0,
        scalp_commission_per_lot=5.0
    )
    mock_get_spread.return_value = 18.0

    # 黄金：point=0.01, contract_size=100.0
    # 手续费点数成本 = 5.0 / (100.0 * 0.01) = 5.0 点
    # 实时点差 = 15.0 点
    # 总摩擦成本 = 15.0 + 5.0 = 20.0 点
    # 最低空间要求 = 3.0 * 20.0 = 60.0 点价格波幅 (即 0.60 黄金价格)
    # 模拟 ATR 空间极小，只有 0.40 (即 40.0 点，低于 60 点要求)
    row = {
        "scalp_ready": True,
        "scalp_direction": "long",
        "scalp_rr": 1.5,
        "scalp_confidence": "high",
        "scalp_signal_type": "momentum_breakout",
        "spread_points": 15.0,
        "point": 0.01,
        "atr5_m5": 0.40,  # 40点 < 60点
        "contract_size": 100.0,
    }

    result = _build_scalp_ready_candidate("XAUUSD", row)
    assert result is not None
    assert result["grade"] == TradeGrade.OBSERVE_ONLY.value
    assert "波动空间" in result["detail"]
    assert "性价比不足拦截" in result["detail"]
    assert result["scalp_setup_kind"] == "scalp_cost_guard"

@patch("app_config.get_runtime_config")
@patch("app_config.load_ai_steering_cache")
@patch("monitor_rules._get_24h_avg_spread")
def test_scalp_normal_release(mock_get_spread, mock_load_ai, mock_get_cfg):
    """验证当全部优化规则及风控限制完美放行时，高优直接输出 LIGHT_POSITION 短线就绪。"""
    # 模拟配置：全部合规
    mock_get_cfg.return_value = _build_test_config(
        scalp_ai_steering_enabled=True,
        scalp_min_reward_cost_ratio=3.0,
        scalp_commission_per_lot=5.0,
        scalp_max_spread_multiplier=1.5,
        scalp_min_rr=1.2
    )
    # AI 意志与短线偏好对齐
    mock_load_ai.return_value = {"action": "long", "score": 85.0, "timestamp": 123456.0}
    mock_get_spread.return_value = 18.0

    # 实时点差 = 15.0，往返摩擦点数 = (15.0 + 5.0) * 2 = 40.0
    # ATR(5) = 1.30 (即 130点 > 40*3=120点)
    row = {
        "scalp_ready": True,
        "scalp_direction": "long",
        "scalp_rr": 1.5,
        "scalp_confidence": "high",
        "scalp_signal_text": "黄金强势突破，短线动能可轻仓试仓",
        "scalp_signal_type": "momentum_breakout",
        "spread_points": 15.0,
        "point": 0.01,
        "atr5_m5": 1.30,
        "contract_size": 100.0,
    }

    result = _build_scalp_ready_candidate("XAUUSD", row)
    assert result is not None
    assert result["grade"] == TradeGrade.LIGHT_POSITION.value
    assert result["setup_kind"] == "scalp"
    assert "强势突破" in result["detail"]
    assert result["scalp_rr"] == 1.5

@patch("app_config.get_runtime_config")
@patch("app_config.load_ai_steering_cache")
@patch("monitor_rules._get_24h_avg_spread")
def test_scalp_adx_filter_blocks_under_threshold(mock_get_spread, mock_load_ai, mock_get_cfg):
    """验证当 M5 ADX 趋势强度低于阈值时，触发震荡避险强行拦截，返回 OBSERVE_ONLY。"""
    # 模拟配置：设置 scalp_min_adx=22.0
    mock_get_cfg.return_value = _build_test_config(
        scalp_ai_steering_enabled=False,
        scalp_min_reward_cost_ratio=2.0,
        scalp_commission_per_lot=5.0,
        scalp_min_adx=22.0
    )
    mock_get_spread.return_value = 18.0

    # ADX = 18.5 (< 22.0)，进入震荡避险强行拦截
    row = {
        "scalp_ready": True,
        "scalp_direction": "long",
        "scalp_rr": 1.5,
        "scalp_confidence": "high",
        "scalp_signal_type": "momentum_breakout",
        "spread_points": 15.0,
        "point": 0.01,
        "atr5_m5": 1.0,
        "contract_size": 100.0,
        "adx_m5": 18.5,  # 低于 22.0
    }

    result = _build_scalp_ready_candidate("XAUUSD", row)
    assert result is not None
    assert result["grade"] == TradeGrade.OBSERVE_ONLY.value
    assert "避开震荡扫损" in result["detail"]
    assert "ADX=18.5 < 阈值 22.0" in result["detail"]
    assert result["scalp_setup_kind"] == "scalp_adx_filter"


@patch("app_config.get_runtime_config")
@patch("app_config.load_ai_steering_cache")
@patch("monitor_rules._get_24h_avg_spread")
def test_scalp_adx_filter_allows_above_threshold(mock_get_spread, mock_load_ai, mock_get_cfg):
    """验证当 M5 ADX 趋势强度高于阈值时，不触发震荡避险，正常放行。"""
    mock_get_cfg.return_value = _build_test_config(
        scalp_ai_steering_enabled=False,
        scalp_min_reward_cost_ratio=2.0,
        scalp_commission_per_lot=5.0,
        scalp_min_adx=22.0
    )
    mock_get_spread.return_value = 18.0

    # ADX = 26.5 (> 22.0)，正常放行
    row = {
        "scalp_ready": True,
        "scalp_direction": "long",
        "scalp_rr": 1.5,
        "scalp_confidence": "high",
        "scalp_signal_text": "正常信号放行",
        "scalp_signal_type": "momentum_breakout",
        "spread_points": 15.0,
        "point": 0.01,
        "atr5_m5": 1.0,
        "contract_size": 100.0,
        "adx_m5": 26.5,  # 高于 22.0
    }

    result = _build_scalp_ready_candidate("XAUUSD", row)
    assert result is not None
    assert result["grade"] == TradeGrade.LIGHT_POSITION.value
    assert "正常信号放行" in result["detail"]


# ── 自适应时机与策略自进化引擎单元测试 ──
from datetime import datetime, timedelta

@patch("app_config.get_runtime_config")
def test_adaptive_ratio_disabled(mock_get_cfg):
    """验证当 sim_adaptive_evolution_enabled = False 时，自适应乘数固定返回 1.0。"""
    from runtime_utils import get_adaptive_filter_ratio
    mock_get_cfg.return_value = _build_test_config(sim_adaptive_evolution_enabled=False)
    assert get_adaptive_filter_ratio() == 1.0


@patch("app_config.get_runtime_config")
@patch("knowledge_base.open_knowledge_connection")
def test_adaptive_ratio_defense_losing_streak_3_losses(mock_conn, mock_get_cfg):
    """验证当最近 3 笔均亏损时，防御收紧，乘数返回 1.2。"""
    from runtime_utils import get_adaptive_filter_ratio
    mock_get_cfg.return_value = _build_test_config(sim_adaptive_evolution_enabled=True)
    
    class MockConn:
        def execute(self, sql, *args):
            class MockCursor:
                def fetchone(self):
                    return (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
                def fetchall(self):
                    return [(-10.0,), (-5.0,), (-15.0,)]
            return MockCursor()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        
    mock_conn.return_value = MockConn()
    assert get_adaptive_filter_ratio() == 1.2


@patch("app_config.get_runtime_config")
@patch("knowledge_base.open_knowledge_connection")
def test_adaptive_ratio_defense_losing_streak_5_losses(mock_conn, mock_get_cfg):
    """验证当最近 5 笔有 4 笔亏损时，防御收紧，乘数返回 1.2。"""
    from runtime_utils import get_adaptive_filter_ratio
    mock_get_cfg.return_value = _build_test_config(sim_adaptive_evolution_enabled=True)
    
    class MockConn:
        def execute(self, sql, *args):
            class MockCursor:
                def fetchone(self):
                    return (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
                def fetchall(self):
                    return [(-10.0,), (20.0,), (-15.0,), (-8.0,), (-2.0,)] # 4 losses
            return MockCursor()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        
    mock_conn.return_value = MockConn()
    assert get_adaptive_filter_ratio() == 1.2


@patch("app_config.get_runtime_config")
@patch("knowledge_base.open_knowledge_connection")
def test_adaptive_ratio_idle_no_history(mock_conn, mock_get_cfg):
    """验证当没有历史交易记录时，自适应放宽门槛，乘数返回 0.85。"""
    from runtime_utils import get_adaptive_filter_ratio
    mock_get_cfg.return_value = _build_test_config(sim_adaptive_evolution_enabled=True)
    
    class MockConn:
        def execute(self, sql, *args):
            class MockCursor:
                def fetchone(self):
                    return None
                def fetchall(self):
                    return []
            return MockCursor()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        
    mock_conn.return_value = MockConn()
    assert get_adaptive_filter_ratio() == 0.85


@patch("app_config.get_runtime_config")
@patch("knowledge_base.open_knowledge_connection")
def test_adaptive_ratio_idle_over_24h(mock_conn, mock_get_cfg):
    """验证当闲置时间超过 24 小时且无连败时，自适应放宽门槛，乘数返回 0.70。"""
    from runtime_utils import get_adaptive_filter_ratio
    mock_get_cfg.return_value = _build_test_config(sim_adaptive_evolution_enabled=True)
    
    t_25h_ago = (datetime.now() - timedelta(hours=25.0)).strftime("%Y-%m-%d %H:%M:%S")
    class MockConn:
        def execute(self, sql, *args):
            class MockCursor:
                def fetchone(self):
                    return (t_25h_ago,)
                def fetchall(self):
                    return [(10.0,), (20.0,), (15.0,)] # All profit
            return MockCursor()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        
    mock_conn.return_value = MockConn()
    assert get_adaptive_filter_ratio() == 0.70


@patch("app_config.get_runtime_config")
@patch("knowledge_base.open_knowledge_connection")
def test_adaptive_ratio_idle_over_12h(mock_conn, mock_get_cfg):
    """验证当闲置时间在 12 到 24 小时之间且无连败时，自适应放宽门槛，乘数返回 0.85。"""
    from runtime_utils import get_adaptive_filter_ratio
    mock_get_cfg.return_value = _build_test_config(sim_adaptive_evolution_enabled=True)
    
    t_13h_ago = (datetime.now() - timedelta(hours=13.0)).strftime("%Y-%m-%d %H:%M:%S")
    class MockConn:
        def execute(self, sql, *args):
            class MockCursor:
                def fetchone(self):
                    return (t_13h_ago,)
                def fetchall(self):
                    return [(10.0,), (20.0,), (15.0,)] # All profit
            return MockCursor()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        
    mock_conn.return_value = MockConn()
    assert get_adaptive_filter_ratio() == 0.85


@patch("app_config.get_runtime_config")
@patch("knowledge_base.open_knowledge_connection")
def test_adaptive_ratio_normal_behavior(mock_conn, mock_get_cfg):
    """验证当闲置时间小于 12 小时且无连败时，乘数正常返回 1.0。"""
    from runtime_utils import get_adaptive_filter_ratio
    mock_get_cfg.return_value = _build_test_config(sim_adaptive_evolution_enabled=True)
    
    t_2h_ago = (datetime.now() - timedelta(hours=2.0)).strftime("%Y-%m-%d %H:%M:%S")
    class MockConn:
        def execute(self, sql, *args):
            class MockCursor:
                def fetchone(self):
                    return (t_2h_ago,)
                def fetchall(self):
                    return [(10.0,), (20.0,), (15.0,)] # All profit
            return MockCursor()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        
    mock_conn.return_value = MockConn()
    assert get_adaptive_filter_ratio() == 1.0

