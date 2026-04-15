from __future__ import annotations

import itertools
import shutil
from pathlib import Path

import pytest


class LocalTmpPathFactory:
    """在独立项目目录内提供可写临时目录，绕开系统默认 pytest 临时目录权限限制。"""

    def __init__(self, base: Path):
        self._base = base.resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        self._counter = itertools.count()

    def getbasetemp(self) -> Path:
        return self._base

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        stem = str(basename or "case").strip() or "case"
        if numbered:
            while True:
                idx = next(self._counter)
                candidate = self._base / f"{stem}_{idx}"
                if not candidate.exists():
                    candidate.mkdir(parents=True, exist_ok=False)
                    return candidate
        candidate = self._base / stem
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate


@pytest.fixture(scope="session")
def tmp_path_factory() -> LocalTmpPathFactory:
    sandbox_root = Path(__file__).resolve().parent.parent / ".test_sandbox"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    return LocalTmpPathFactory(sandbox_root)


@pytest.fixture
def tmp_path(tmp_path_factory: LocalTmpPathFactory) -> Path:
    return tmp_path_factory.mktemp("case")


@pytest.fixture(autouse=True)
def sync_notification_worker(request, monkeypatch):
    """测试期间将 NotificationWorker.enqueue 替换为同步立即执行。

    这样 test_notification.py 中对 send_dingtalk / send_pushplus 的 monkeypatch
    依然有效——send_fn 会在调用方线程内立即执行，测试无需等待后台线程。

    test_notification_worker.py 中的测试均使用自己构造的 NotificationWorker
    实例，不走 get_notification_worker()，不受此 fixture 影响（通过
    文件名检测跳过全局替换）。
    """
    # test_notification_worker.py 需要真实后台线程行为，跳过同步化替换
    if "test_notification_worker" in request.fspath.basename:
        yield
        return

    import notification_worker as nw

    def _sync_enqueue(self, task: dict) -> bool:
        """同步立即执行推送任务，模拟后台线程行为供测试验证。"""
        send_fn = task.get("send_fn")
        args = task.get("args", ())
        on_result = task.get("on_result")
        if send_fn is None:
            return True
        try:
            ok, detail = send_fn(*args)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc)
        if on_result is not None:
            try:
                on_result(ok, detail)
            except Exception:  # noqa: BLE001
                pass
        return True

    monkeypatch.setattr(nw.NotificationWorker, "enqueue", _sync_enqueue)
    yield
