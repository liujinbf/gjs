import sqlite3
import json
from collections import defaultdict
import os
import sys
import io

# 强制输出为 UTF-8，解决 Windows 控制台乱码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_PATH = r"c:\Users\Administrator\Desktop\贵金属机器人\.runtime\mt5_sim_trading.sqlite"

def analyze():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. 整体账户信息
    account = cursor.execute("SELECT * FROM sim_accounts").fetchone()
    if account:
        print("=== 模拟账户概览 ===")
        print(f"账户余额: ${account['balance']:,.2f}")
        print(f"账户净值: ${account['equity']:,.2f}")
        print(f"已用保证金: ${account['used_margin']:,.2f}")
        print(f"总盈亏: ${account['total_profit']:,.2f}")
        print(f"胜率: {account['win_count']}胜 / {account['loss_count']}负")
        total_finished = account['win_count'] + account['loss_count']
        if total_finished > 0:
            rate = account['win_count'] / total_finished * 100
            print(f"综合胜率: {rate:.2f}%")
        print()

    # 2. 统计已平仓交易
    trades = cursor.execute("SELECT * FROM sim_trades").fetchall()
    print(f"=== 已平仓交易总数: {len(trades)} ===")
    if not trades:
        print("暂无已平仓交易记录。")
        return

    # 按 strategy_family 归类统计
    stats = defaultdict(lambda: {
        "count": 0, "win": 0, "loss": 0, "draw": 0, "total_pnl": 0.0,
        "reasons": defaultdict(int)
    })

    for t in trades:
        family = t["strategy_family"] or "unknown"
        # 从 strategy_param_json 进一步读取 setup_kind
        param_json = t["strategy_param_json"]
        if param_json:
            try:
                params = json.loads(param_json)
                setup_kind = params.get("setup_kind") or params.get("strategy_family")
                if setup_kind:
                    family = setup_kind
            except:
                pass
        
        # 统一大写/小写
        family = family.strip().lower()
        pnl = t["profit"]
        reason = t["reason"] or "unknown"

        stats[family]["count"] += 1
        stats[family]["total_pnl"] += pnl
        stats[family]["reasons"][reason] += 1

        if pnl > 0.01:
            stats[family]["win"] += 1
        elif pnl < -0.01:
            stats[family]["loss"] += 1
        else:
            stats[family]["draw"] += 1

    # 英文头防止表格对齐乱码
    print(f"{'Strategy':<25}{'Total':<8}{'Win':<6}{'Loss':<6}{'Draw':<6}{'Net PnL':<15}{'Avg PnL':<12}{'WinRate':<8}")
    print("-" * 90)
    for family, data in stats.items():
        win_rate = 0.0
        active_trades = data["win"] + data["loss"]
        if active_trades > 0:
            win_rate = data["win"] / active_trades * 100
        avg_pnl = data["total_pnl"] / data["count"]
        print(f"{family:<25}{data['count']:<8}{data['win']:<6}{data['loss']:<6}{data['draw']:<6}${data['total_pnl']:<14.2f}${avg_pnl:<11.2f}{win_rate:.1f}%")

    print("\n=== 各策略平仓原因明细 ===")
    for family, data in stats.items():
        print(f"\n策略 [{family}] (总共 {data['count']} 单):")
        sorted_reasons = sorted(data["reasons"].items(), key=lambda x: -x[1])
        for r, cnt in sorted_reasons:
            pct = cnt / data["count"] * 100
            print(f"  - {r}: {cnt}次 ({pct:.1f}%)")

    # 3. 统计未平仓持仓
    open_positions = cursor.execute("SELECT * FROM sim_positions WHERE status='open'").fetchall()
    if open_positions:
        print(f"\n=== 当前未平仓持仓 ({len(open_positions)}单) ===")
        for pos in open_positions:
            print(f"  - {pos['symbol']} {pos['action'].upper()}: 手数={pos['quantity']}, 入场={pos['entry_price']}, 浮盈=${pos['floating_pnl']:.2f}, 策略={pos['strategy_family']}")

    conn.close()

if __name__ == "__main__":
    analyze()
