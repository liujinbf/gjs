import sqlite3
import json
import os

DB_PATH = r"c:\Users\Administrator\Desktop\贵金属机器人\.runtime\mt5_sim_trading.sqlite"

def check_event_trades():
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    rows = cursor.execute("SELECT * FROM sim_trades WHERE strategy_family = 'event'").fetchall()
    print(f"=== Event Trades Detail ({len(rows)}) ===")
    for r in rows:
        print(f"ID: {r['id']}, Symbol: {r['symbol']}, Action: {r['action']}, Entry: {r['entry_price']}, Exit: {r['exit_price']}, PnL: ${r['profit']:.2f}, Reason: {r['reason']}, Param: {r['strategy_param_json']}")
    
    conn.close()

if __name__ == "__main__":
    check_event_trades()
