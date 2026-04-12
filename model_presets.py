"""
贵金属独立项目的大模型预设。

说明：
1. 这里直接复制并收口老项目里的主流模型配置
2. 独立项目后续只依赖本文件，不再回看老项目设置页
"""

MODEL_PRESETS = {
    "DeepSeek 官方平台 (DeepSeek-V3)": {
        "url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "link": "https://platform.deepseek.com/api_keys",
    },
    "硅基流动满血版 (DeepSeek-V3)": {
        "url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "link": "https://cloud.siliconflow.cn/i/V5qemSqG",
    },
    "硅基流动推理版 (DeepSeek-R1)": {
        "url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-R1",
        "link": "https://cloud.siliconflow.cn/i/V5qemSqG",
    },
    "阿里通义千问 (Qwen-Max)": {
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "link": "https://dashscope.console.aliyun.com/api-key",
    },
    "月之暗面 (Kimi-Moonshot)": {
        "url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-auto",
        "link": "https://platform.moonshot.cn/console/api-keys",
    },
    "Google (Gemini-2.5-Flash)": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
        "link": "https://aistudio.google.com/app/apikey",
    },
    "OpenAI (GPT-4o)": {
        "url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "link": "https://platform.openai.com/api_keys",
    },
    "Anthropic (Claude-3.5)": {
        "url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet-20241022",
        "link": "https://console.anthropic.com/settings/keys",
    },
    "【自定义配置】": {
        "url": "",
        "model": "",
        "link": "",
    },
}


def find_preset_name(api_base: str, model: str) -> str:
    target_base = str(api_base or "").strip().rstrip("/")
    target_model = str(model or "").strip()
    for name, data in MODEL_PRESETS.items():
        if target_base == str(data.get("url", "") or "").strip().rstrip("/") and target_model == str(data.get("model", "") or "").strip():
            return name
    return "【自定义配置】"
