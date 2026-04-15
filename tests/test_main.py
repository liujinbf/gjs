import sys
import threading
from pathlib import Path
from types import SimpleNamespace

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


def test_main_thread_exception_dialog_is_debounced(monkeypatch):
    calls = {"count": 0}

    class FakeMessageBox:
        Icon = SimpleNamespace(Critical="critical")

        def setWindowTitle(self, *_args, **_kwargs):
            return None

        def setIcon(self, *_args, **_kwargs):
            return None

        def setText(self, *_args, **_kwargs):
            return None

        def exec(self):
            calls["count"] += 1
            return 0

    monkeypatch.setattr(main, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(main, "_last_alert_time", 0.0)
    times = iter([100.0, 101.0, 112.0])
    monkeypatch.setattr(main.time, "time", lambda: next(times))

    original_excepthook = sys.excepthook
    original_threading_hook = threading.excepthook
    main._install_global_exception_hooks(object())

    def _emit_runtime_error(message: str):
        try:
            raise RuntimeError(message)
        except RuntimeError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)

    try:
        _emit_runtime_error("first")
        _emit_runtime_error("second")
        _emit_runtime_error("third")
    finally:
        sys.excepthook = original_excepthook
        threading.excepthook = original_threading_hook

    assert calls["count"] == 2
