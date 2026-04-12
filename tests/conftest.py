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
