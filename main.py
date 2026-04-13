import sys

from PySide6.QtWidgets import QApplication

from app_config import load_project_env
from ui import MetalMonitorWindow


def main() -> int:
    load_project_env()
    app = QApplication(sys.argv)
    
    # 注入全局现代化样式
    import style
    app.setStyleSheet(style.GLOBAL_APP_STYLE)
    
    window = MetalMonitorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
