"""
贵金属监控终端 — 智能启动器

功能：
1. 自动检查并安装缺失依赖
2. 显示启动进度画面
3. 优雅处理异常并写入日志
"""
from __future__ import annotations

import subprocess
import sys
import os
import time
from pathlib import Path

# ── 颜色常量（Windows 控制台 ANSI） ──
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
DIM    = "\033[2m"

PROJECT_DIR = Path(__file__).parent

# ── Windows 控制台强制 UTF-8 输出 ──
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _enable_ansi():
    """启用 Windows 10+ 控制台 ANSI 颜色支持。"""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)


def _print_banner():
    print(f"\n{CYAN}{BOLD}")
    print("  +==================================================+")
    print("  |   [GJS]  Precious Metal Quant Terminal  v0.9    |")
    print("  |   XAUUSD  XAGUSD  EURUSD  USDJPY               |")
    print("  |   AI Research  Macro Data  Sim Trading          |")
    print("  +==================================================+")
    print(RESET)


def _check_step(label: str, ok: bool, detail: str = ""):
    icon = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
    detail_text = f"  {DIM}{detail}{RESET}" if detail else ""
    print(f"  {icon}  {label}{detail_text}")


def _check_dependencies() -> bool:
    """检查核心依赖，缺失则自动安装。"""
    REQUIRED = {
        "PySide6":       ("PySide6",       "PySide6.QtWidgets"),
        "MetaTrader5":   ("MetaTrader5",   "MetaTrader5"),
        "python-dotenv": ("python-dotenv", "dotenv"),
        "json-repair":   ("json-repair",   "json_repair"),
    }

    print(f"\n{BOLD}  [ 依赖检查 ]{RESET}")
    missing = []
    for pkg_name, (install_name, import_name) in REQUIRED.items():
        try:
            __import__(import_name)
            _check_step(pkg_name, True)
        except ImportError:
            _check_step(pkg_name, False, "未安装，即将自动安装…")
            missing.append(install_name)

    if missing:
        print(f"\n{YELLOW}  正在自动安装缺失依赖：{', '.join(missing)}{RESET}")
        pip_cmd = [sys.executable, "-m", "pip", "install"] + missing + ["--quiet"]
        result = subprocess.run(pip_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"{RED}  [错误] 依赖安装失败：{RESET}")
            print(result.stderr[:500])
            return False
        print(f"{GREEN}  ✓  依赖安装完成{RESET}")

    return True


def _check_env() -> bool:
    """检查关键配置文件是否存在。"""
    print(f"\n{BOLD}  [ 配置检查 ]{RESET}")
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        _check_step(".env 配置文件", True)
    else:
        _check_step(".env 配置文件", False, "未找到！请复制 .env.example 并填写配置")
        return False

    # 检查 Alpha Vantage Key
    from dotenv import dotenv_values
    cfg = dotenv_values(str(env_file))
    av_key = cfg.get("ALPHAVANTAGE_API_KEY", "").strip()
    _check_step(
        "Alpha Vantage Key",
        bool(av_key),
        f"{'已配置 (DXY/CPI/NFP 数据已激活)' if av_key else '未配置，宏观数据将降级到免费源'}",
    )

    mt5_path = cfg.get("MT5_PATH", "").strip()
    if mt5_path and Path(mt5_path).exists():
        _check_step("MT5 终端路径", True, mt5_path)
    elif mt5_path:
        _check_step("MT5 终端路径", False, f"路径不存在：{mt5_path}")
    else:
        _check_step("MT5 终端路径", False, "未配置，将使用自动搜索模式")

    return True


def _launch():
    """启动主程序。"""
    print(f"\n{BOLD}  [ 启动中… ]{RESET}")
    print(f"  {DIM}Python: {sys.executable}{RESET}")
    print(f"  {DIM}项目目录: {PROJECT_DIR}{RESET}\n")
    time.sleep(0.3)

    # 切换工作目录，确保相对路径正确
    os.chdir(str(PROJECT_DIR))
    sys.path.insert(0, str(PROJECT_DIR))

    try:
        from app_config import load_project_env
        load_project_env()

        from PySide6.QtWidgets import QApplication
        from ui import MetalMonitorWindow
        import style

        app = QApplication(sys.argv)
        app.setStyleSheet(style.GLOBAL_APP_STYLE)
        app.setApplicationName("贵金属监控终端")
        app.setOrganizationName("GJS")

        window = MetalMonitorWindow()
        window.show()

        print(f"{GREEN}  ✓  终端启动成功！{RESET}\n")
        return app.exec()

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()

        # 写入错误日志
        log_path = PROJECT_DIR / "error_log.txt"
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n{'='*60}\n")
            f.write(f"启动失败 @ {timestamp}\n")
            f.write(f"{'='*60}\n")
            f.write(tb)

        print(f"\n{RED}{BOLD}  ╔══════════════════════════════════════╗")
        print(f"  ║  ❌  启动失败                         ║")
        print(f"  ╚══════════════════════════════════════╝{RESET}")
        print(f"\n  错误信息：{str(exc)[:200]}")
        print(f"\n  完整日志已写入：{RED}{log_path}{RESET}")
        print(f"\n  常见解决方案：")
        print(f"  {DIM}• MT5 未运行 → 先启动 MetaTrader 5 终端后再启动本程序")
        print(f"  • 缺少依赖   → 运行 pip install -r requirements.txt")
        print(f"  • 配置错误   → 检查 .env 文件中的 AI_API_KEY / MT5 配置{RESET}")
        return 1


def main():
    _enable_ansi()
    _print_banner()

    # 依赖 & 配置检查
    if not _check_dependencies():
        print(f"\n{RED}  启动中止：请解决上述问题后重试。{RESET}\n")
        input("  按回车键退出...")
        return 1

    _check_env()  # 配置检查仅提示，不阻断启动

    return _launch()


if __name__ == "__main__":
    raise SystemExit(main())
