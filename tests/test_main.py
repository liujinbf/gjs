import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main


def test_can_show_exception_dialog_returns_true_in_main_thread():
    assert main._can_show_exception_dialog(app=object()) is True


def test_can_show_exception_dialog_returns_false_in_worker_thread():
    result = {}

    def _runner():
        result["value"] = main._can_show_exception_dialog(app=object())

    worker = threading.Thread(target=_runner, name="test-worker")
    worker.start()
    worker.join(timeout=5)

    assert result["value"] is False
