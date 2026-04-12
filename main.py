import sys

from PySide6.QtWidgets import QApplication

from app_config import load_project_env
from ui import MetalMonitorWindow


def main() -> int:
    load_project_env()
    app = QApplication(sys.argv)
    window = MetalMonitorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
