import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from model_presets import MODEL_PRESETS, find_preset_name


def test_find_preset_name_matches_legacy_siliconflow_r1():
    name = find_preset_name("https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-R1")
    assert name == "硅基流动推理版 (DeepSeek-R1)"


def test_model_presets_include_common_platforms():
    assert "DeepSeek 官方平台 (DeepSeek-V3)" in MODEL_PRESETS
    assert "硅基流动满血版 (DeepSeek-V3)" in MODEL_PRESETS
    assert "硅基流动推理版 (DeepSeek-R1)" in MODEL_PRESETS
    assert "【自定义配置】" in MODEL_PRESETS
