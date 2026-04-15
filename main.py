import logging
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from app_config import load_project_env
from ui import MetalMonitorWindow

# 运行时日志文件，与现有 error_log.txt 共用
_LOG_FILE = Path(__file__).parent / "error_log.txt"


def _configure_logging() -> None:
    """配置全局日志：同时输出到控制台和 error_log.txt。追加模式，保留历史。"""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, encoding="utf-8", mode="a"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)


def _install_global_exception_hooks(app: QApplication) -> None:
    """注入全局异常捕获钉子，防止程序闪退无痕。

    覆盖两个钉子：
    - sys.excepthook  —— 当主线程有未处理异常时触发
    - threading.excepthook —— 当子线程有未处理异常时触发

    Args:
        app: QApplication 实例，用于展示错误弹窗。
    """

    def _write_and_alert(exc_type, exc_value, exc_tb, thread_name: str = "") -> None:
        """将层叠信息写入日志文件并尝试弹窗。"""
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        prefix = f"[Thread: {thread_name}] " if thread_name else ""
        msg = f"未处理异常 {prefix}\n{tb_text}"
        logging.critical(msg)
        # 尝试弹出错误对话框，如果 GUI 已关闭则静默跳过
        try:
            box = QMessageBox()
            box.setWindowTitle("程序错误")
            box.setIcon(QMessageBox.Icon.Critical)
            box.setText(
                f"程序发生未处理异常，错误已写入 error_log.txt。\n\n"
                f"{prefix}{exc_type.__name__}: {exc_value}"
            )
            box.exec()
        except Exception:  # noqa: BLE001
            pass  # GUI 已关闭时弹窗会失败，属于已知情况，不再递归

    def _main_thread_hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            # Ctrl+C 正常退出，不弹窗
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        _write_and_alert(exc_type, exc_value, exc_tb)

    def _thread_hook(args: threading.ExceptHookArgs):
        if args.exc_type is SystemExit:
            return
        _write_and_alert(
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
            thread_name=getattr(args.thread, "name", ""),
        )

    sys.excepthook = _main_thread_hook
    threading.excepthook = _thread_hook
    logging.info("🛡️ 全局异常捕获钩子已安装（sys.excepthook + threading.excepthook）")


def main() -> int:
    load_project_env()
    _configure_logging()
    app = QApplication(sys.argv)

    # 安装全局异常捕获，必须在 QApplication 创建之后、exec() 之前
    _install_global_exception_hooks(app)

    # 注入全局现代化样式
    import style
    app.setStyleSheet(style.GLOBAL_APP_STYLE)

    window = MetalMonitorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
