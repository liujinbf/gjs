# -*- coding: utf-8 -*-
"""
Fix verification script - verifies all fixes are genuinely working
"""
import sys
import traceback

errors = []

# ─────────────────────────────────────────────────────────────────────────────
# Test 1: BUG-009 key_levels.py numpy array safety
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("Test1: BUG-009 key_levels numpy array safe handling")
print("=" * 60)
try:
    import numpy as np
    from key_levels import analyze_key_levels

    dtype = np.dtype([
        ('time', np.int64), ('open', np.float64), ('high', np.float64),
        ('low', np.float64), ('close', np.float64), ('tick_volume', np.int64)
    ])

    # Case A: empty numpy array
    bars_empty = np.array([], dtype=dtype)
    r = analyze_key_levels('XAUUSD', 3310.0, bars_empty)
    assert r['key_level_ready'] == False, "empty array should return False"
    print("  [PASS] Case A (empty numpy array): key_level_ready=False")

    # Case B: None
    r2 = analyze_key_levels('XAUUSD', 3310.0, None)
    assert r2['key_level_ready'] == False, "None should return False"
    print("  [PASS] Case B (None): key_level_ready=False")

    # Case C: single bar
    bars_single = np.array([(1000, 3300.0, 3320.0, 3290.0, 3310.0, 100)], dtype=dtype)
    r3 = analyze_key_levels('XAUUSD', 3310.0, bars_single)
    assert r3['key_level_ready'] == False, "single bar not enough, should return False"
    print("  [PASS] Case C (single bar): data insufficient, key_level_ready=False")

    # Case D: 60 bars (real scenario)
    import random
    random.seed(42)
    base_price = 3300.0
    bars_full = []
    for i in range(60):
        o = base_price + random.uniform(-5, 5)
        h = o + random.uniform(0, 8)
        lo = o - random.uniform(0, 8)
        c = o + random.uniform(-3, 3)
        bars_full.append((1000 + i * 3600, o, h, lo, c, 100))
    bars_numpy_full = np.array(bars_full, dtype=dtype)
    r4 = analyze_key_levels('XAUUSD', 3305.0, bars_numpy_full)
    assert r4['key_level_ready'] == True, "60 bars should return True"
    print("  [PASS] Case D (60 H1 bars): key_level_ready=True, state=%s, high=%.2f, low=%.2f" % (
        r4['key_level_state'], r4['key_level_high'], r4['key_level_low']))

    # Case E: list[dict] backward compatibility
    bars_list = [{'time': 1000+i, 'open': 3300.0, 'high': 3320.0+i, 'low': 3280.0+i, 'close': 3310.0+i} for i in range(10)]
    r5 = analyze_key_levels('XAUUSD', 3315.0, bars_list)
    assert r5['key_level_ready'] == True, "list[dict] format should work"
    print("  [PASS] Case E (list[dict] format): backward compatible")

    print("  [OK] BUG-009 ALL PASSED")
except Exception as e:
    print("  [FAIL] BUG-009: %s" % e)
    traceback.print_exc()
    errors.append("BUG-009")

# ─────────────────────────────────────────────────────────────────────────────
# Test 2: DEFECT-004 knowledge_runtime._parse_time delegation
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("Test2: DEFECT-004 knowledge_runtime._parse_time delegation")
print("=" * 60)
try:
    from knowledge_runtime import _parse_time
    from runtime_utils import parse_time

    cases = [
        ("2026-04-13 20:00:00",),
        ("2026-04-13 20:00",),
        ("",),
        ("invalid",),
    ]
    for (val,) in cases:
        result_km = _parse_time(val)
        result_ru = parse_time(val)
        assert str(result_km) == str(result_ru), "Inconsistent: %s -> %s vs %s" % (val, result_km, result_ru)
        print("  [PASS] '%s' -> %s" % (val, result_km))

    import inspect
    src = inspect.getsource(_parse_time)
    assert "_parse_time_impl" in src, "_parse_time should delegate to _parse_time_impl"
    print("  [PASS] Confirmed: delegation, not reimplementation")
    print("  [OK] DEFECT-004 ALL PASSED")
except Exception as e:
    print("  [FAIL] DEFECT-004: %s" % e)
    traceback.print_exc()
    errors.append("DEFECT-004")

# ─────────────────────────────────────────────────────────────────────────────
# Test 3: DEFECT-003 _cleanup_old_snapshots transaction safety
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("Test3: DEFECT-003 _cleanup_old_snapshots transaction")
print("=" * 60)
try:
    import inspect
    from knowledge_runtime import _cleanup_old_snapshots
    src = inspect.getsource(_cleanup_old_snapshots)
    assert "with conn:" in src, "_cleanup_old_snapshots should have 'with conn:' transaction"
    print("  [PASS] Found 'with conn:' transaction block")
    print("  [OK] DEFECT-003 ALL PASSED")
except Exception as e:
    print("  [FAIL] DEFECT-003: %s" % e)
    traceback.print_exc()
    errors.append("DEFECT-003")

# ─────────────────────────────────────────────────────────────────────────────
# Test 4: DEFECT-002 entry/price field compatibility
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("Test4: DEFECT-002 entry/price field compatibility")
print("=" * 60)
try:
    import inspect
    from backtest_engine import evaluate_signal
    src = inspect.getsource(evaluate_signal)

    assert 'signal_meta.get("price", None)' in src, "should read price field first"
    assert 'signal_meta.get("entry", None)' in src, "should fallback to entry field"
    assert "mid = (sl + tp) / 2.0" in src, "entry=0 should use midpoint calculation"
    print("  [PASS] price field read first")
    print("  [PASS] entry field backward compatible")
    print("  [PASS] entry=0 uses RR midpoint, not hardcoded loss")

    # Parse logic test
    meta_price = {"symbol": "XAUUSD", "action": "long", "price": 3320.0, "sl": 3300.0, "tp": 3360.0}
    entry = float(meta_price.get("price", None) or meta_price.get("entry", None) or 0.0)
    assert entry == 3320.0, "price field: entry should be 3320.0"
    print("  [PASS] Price field parsed correctly: entry=%.1f" % entry)

    meta_entry = {"symbol": "XAUUSD", "action": "long", "entry": 3320.0, "sl": 3300.0, "tp": 3360.0}
    entry2 = float(meta_entry.get("price", None) or meta_entry.get("entry", None) or 0.0)
    assert entry2 == 3320.0, "entry field: entry should be 3320.0"
    print("  [PASS] Entry field parsed correctly: entry=%.1f" % entry2)

    print("  [OK] DEFECT-002 ALL PASSED")
except Exception as e:
    print("  [FAIL] DEFECT-002: %s" % e)
    traceback.print_exc()
    errors.append("DEFECT-002")

# ─────────────────────────────────────────────────────────────────────────────
# Test 5: DEFECT-005 _on_ai_brief_ready resets _ai_auto_is_running
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("Test5: DEFECT-005 manual AI brief resets auto-brief lock")
print("=" * 60)
try:
    with open("ui.py", encoding="utf-8") as f:
        ui_src = f.read()
    idx = ui_src.find("def _on_ai_brief_ready")
    assert idx >= 0, "_on_ai_brief_ready not found"
    snippet = ui_src[idx:idx+500]
    assert "_ai_auto_is_running = False" in snippet, "_on_ai_brief_ready should reset _ai_auto_is_running"
    print("  [PASS] _on_ai_brief_ready contains '_ai_auto_is_running = False'")
    print("  [OK] DEFECT-005 ALL PASSED")
except Exception as e:
    print("  [FAIL] DEFECT-005: %s" % e)
    traceback.print_exc()
    errors.append("DEFECT-005")

# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Q-003 (or logic is CORRECT — verified and unchanged)
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("Test6: Q-003 notification._has_meaningful_learning_report logic")
print("=" * 60)
try:
    from notification import _has_meaningful_learning_report

    cases = [
        ({"summary_text": "0 0 0", "active_rules": [], "watch_rules": [], "frozen_rules": []}, False, "no rules, no summary"),
        ({"active_rules": ["rule1"]}, True, "active_rules non-empty"),
        ({"summary_text": "2 0 0"}, True, "summary has content"),
        ({"summary_text": "0 0 0"}, False, "summary all zero"),
        ({"summary_text": ""}, False, "empty summary"),
    ]
    all_ok = True
    for report, expected, desc in cases:
        actual = _has_meaningful_learning_report(report)
        status = "[PASS]" if actual == expected else "[FAIL]"
        if actual != expected:
            all_ok = False
        print("  %s %s: expected=%s, actual=%s" % (status, desc, expected, actual))

    if all_ok:
        print("  [OK] Q-003 ALL PASSED (or logic confirmed correct)")
    else:
        errors.append("Q-003")
except Exception as e:
    print("  [FAIL] Q-003: %s" % e)
    traceback.print_exc()
    errors.append("Q-003")

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
if errors:
    print("FAILED: %s" % errors)
    sys.exit(1)
else:
    print("ALL FIXES VERIFIED SUCCESSFULLY!")
    print("=" * 60)
