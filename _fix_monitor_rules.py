"""重写 monitor_rules.py 的 _build_event_mode_adjustment 函数（lines 80-186）为枚举版本。"""

with open("monitor_rules.py", "r", encoding="utf-8") as f:
    content = f.read()

# 找到函数开始（第80行附近）和结束（return None 后）
import re

# 定位 _build_event_mode_adjustment 函数的完整范围
START_MARKER = "def _build_event_mode_adjustment("
END_MARKER_AFTER = "\ndef _build_clean_quote_grade_with_context("

start_idx = content.find(START_MARKER)
end_idx = content.find(END_MARKER_AFTER)

if start_idx == -1 or end_idx == -1:
    print(f"ERROR: Cannot find markers! start={start_idx}, end={end_idx}")
    exit(1)

print(f"Found function at char {start_idx}-{end_idx}")

CLEAN_FUNC = '''\
def _build_event_mode_adjustment(event_risk_mode: str, event_context: dict | None = None, symbol: str = "") -> dict[str, str] | None:
    mode = str(event_risk_mode or "normal").strip().lower()
    context = dict(event_context or {})
    active_name = str(context.get("active_event_name", "") or "").strip()
    active_time_text = str(context.get("active_event_time_text", "") or "").strip()
    importance = _normalize_event_importance(str(context.get("active_event_importance", "") or "").strip())
    importance_text = str(context.get("active_event_importance_text", "") or "").strip() or {
        "high": "高影响",
        "medium": "中影响",
        "low": "低影响",
    }.get(importance, "中影响")
    scope_text = str(context.get("active_event_scope_text", "") or "").strip()

    if mode in {"pre_event", "post_event"} and not _event_targets_symbol(context, symbol):
        return None

    if mode == "pre_event":
        if active_name:
            if importance == "high":
                return {
                    "grade": TradeGrade.NO_TRADE,
                    "detail": (
                        f"{importance_text}窗口：{active_name} 将在 {active_time_text or \'稍后\'} 落地，"
                        f"{scope_text or \'会直接影响当前品种\'}，数据前第一脚和点差都更容易失真。"
                    ),
                    "next_review": "至少等事件公布后 15-20 分钟，并确认点差明显收敛后再复核。",
                    "tone": AlertTone.WARNING,
                    "source": AlertGradeSource.EVENT,
                }
            if importance == "low":
                return {
                    "grade": TradeGrade.OBSERVE_ONLY,
                    "detail": (
                        f"{importance_text}窗口：{active_name} 将在 {active_time_text or \'稍后\'} 落地，"
                        "但短线节奏仍可能被打乱，先观察别抢。"
                    ),
                    "next_review": "等事件落地后 5-10 分钟，再复核短线节奏和点差。",
                    "tone": AlertTone.ACCENT,
                    "source": AlertGradeSource.EVENT,
                }
            return {
                "grade": TradeGrade.WAIT_EVENT,
                "detail": (
                    f"{importance_text}窗口：{active_name} 将在 {active_time_text or \'稍后\'} 落地，"
                    "当前先别抢第一脚波动。"
                ),
                "next_review": "等事件公布后 10-15 分钟，并确认点差开始收敛后再复核。",
                "tone": AlertTone.WARNING,
                "source": AlertGradeSource.EVENT,
            }
        return {
            "grade": TradeGrade.WAIT_EVENT,
            "detail": "当前处于事件前高敏阶段，第一脚波动和点差都更容易失真，先别抢。",
            "next_review": "等事件公布后 15 分钟，并确认点差明显收敛后再复核。",
            "tone": AlertTone.WARNING,
            "source": AlertGradeSource.EVENT,
        }
    if mode == "post_event":
        if active_name:
            if importance == "high":
                return {
                    "grade": TradeGrade.NO_TRADE,
                    "detail": (
                        f"{importance_text}窗口：{active_name} 已在 {active_time_text or \'刚才\'} 落地，"
                        "市场往往还在重新定价阶段，别急着追第二脚。"
                    ),
                    "next_review": "至少等 15-20 分钟，并确认关键位与点差一起稳定后再复核。",
                    "tone": AlertTone.WARNING,
                    "source": AlertGradeSource.EVENT,
                }
            if importance == "low":
                return {
                    "grade": TradeGrade.OBSERVE_ONLY,
                    "detail": (
                        f"{importance_text}窗口：{active_name} 已在 {active_time_text or \'刚才\'} 落地，"
                        "但短线还可能有一次回摆，先别急着追。"
                    ),
                    "next_review": "建议 5-10 分钟后再复核方向、点差和关键位。",
                    "tone": AlertTone.ACCENT,
                    "source": AlertGradeSource.EVENT,
                }
            return {
                "grade": TradeGrade.OBSERVE_ONLY,
                "detail": (
                    f"{importance_text}窗口：{active_name} 已在 {active_time_text or \'刚才\'} 落地，"
                    "方向还在重新定价阶段，先观察再决定更稳。"
                ),
                "next_review": "建议 10-15 分钟后再复核方向、点差和关键位。",
                "tone": AlertTone.ACCENT,
                "source": AlertGradeSource.EVENT,
            }
        return {
            "grade": TradeGrade.OBSERVE_ONLY,
            "detail": "事件刚落地，方向还在重新定价阶段，先等波动和报价稳定下来。",
            "next_review": "建议 10-15 分钟后再复核方向、点差和关键位。",
            "tone": AlertTone.ACCENT,
            "source": AlertGradeSource.EVENT,
        }
    if mode == "illiquid":
        return {
            "grade": TradeGrade.NO_TRADE,
            "detail": "当前人为标记为流动性偏弱阶段，点差和执行成本都不适合普通用户硬做。",
            "next_review": "等进入正常观察模式后再复核。",
            "tone": AlertTone.WARNING,
            "source": AlertGradeSource.EVENT,
        }
    return None
'''

new_content = content[:start_idx] + CLEAN_FUNC + content[end_idx:]

with open("monitor_rules.py", "w", encoding="utf-8") as f:
    f.write(new_content)

print("Done replacing _build_event_mode_adjustment")

# 快速语法检查
import py_compile
try:
    py_compile.compile("monitor_rules.py", doraise=True)
    print("Syntax OK")
except py_compile.PyCompileError as e:
    print(f"Syntax ERROR: {e}")
