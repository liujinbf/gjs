from __future__ import annotations
import json
import logging

logger = logging.getLogger(__name__)

def _resolve_value(snapshot: dict, field: str):
    """从 snapshot 中提取所需字段的值，支持从 feature_json 展开"""
    if field in snapshot:
        return snapshot[field]
    
    # 尝试从 feature_json 里提取
    if "feature_json" in snapshot:
        try:
            features = json.loads(str(snapshot["feature_json"] or "{}"))
            if isinstance(features, dict) and field in features:
                return features[field]
        except Exception:
            pass
            
    return None

def _compare(actual_val, op: str, expected_val) -> bool:
    op = str(op).lower().strip()
    
    # 统一类型
    if isinstance(expected_val, (int, float)) and actual_val is not None:
        try:
            actual_val = float(actual_val)
        except (ValueError, TypeError):
            return False
            
    if op == "==" or op == "=":
        return actual_val == expected_val
    elif op == "!=":
        return actual_val != expected_val
    elif op == ">":
        return actual_val > expected_val if actual_val is not None else False
    elif op == "<":
        return actual_val < expected_val if actual_val is not None else False
    elif op == ">=":
        return actual_val >= expected_val if actual_val is not None else False
    elif op == "<=":
        return actual_val <= expected_val if actual_val is not None else False
    elif op == "in":
        if not isinstance(expected_val, (list, tuple, set)):
            return False
        return actual_val in expected_val
    elif op == "not_in":
        if not isinstance(expected_val, (list, tuple, set)):
            return False
        return actual_val not in expected_val
    elif op == "contains":
        return str(expected_val) in str(actual_val) if actual_val else False
        
    return False

def evaluate_rule_logic(logic_dict: dict, snapshot: dict) -> bool:
    """
    递归解析 logic_json 表达式树。
    示例 logic_dict:
    {
        "op": "AND",
        "conditions": [
            {"field": "signal_side", "op": "==", "value": "long"},
            {"op": "OR", "conditions": [ ... ]}
        ]
    }
    """
    if not isinstance(logic_dict, dict) or not logic_dict:
        return False
        
    op = str(logic_dict.get("op", "")).upper()
    
    # 终结符 condition
    if "field" in logic_dict:
        field = logic_dict["field"]
        target_val = logic_dict.get("value")
        actual_val = _resolve_value(snapshot, field)
        return _compare(actual_val, op, target_val)
        
    # 结合符 AND / OR
    conditions = logic_dict.get("conditions", [])
    if not isinstance(conditions, list) or not conditions:
        return False
        
    if op == "AND":
        return all(evaluate_rule_logic(cond, snapshot) for cond in conditions)
    elif op == "OR":
        return any(evaluate_rule_logic(cond, snapshot) for cond in conditions)
        
    return False
