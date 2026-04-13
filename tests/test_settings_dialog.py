import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from settings_dialog import _build_ai_test_request


def test_build_ai_test_request_uses_bearer_for_openai_compatible():
    url, headers = _build_ai_test_request("https://api.siliconflow.cn/v1", "demo-key")

    assert url == "https://api.siliconflow.cn/v1/models"
    assert headers["Authorization"] == "Bearer demo-key"
    assert "x-api-key" not in headers


def test_build_ai_test_request_uses_anthropic_headers():
    url, headers = _build_ai_test_request("https://api.anthropic.com/v1", "anthropic-key")

    assert url == "https://api.anthropic.com/v1/models"
    assert headers["x-api-key"] == "anthropic-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers
