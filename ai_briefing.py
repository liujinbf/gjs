"""
轻量 AI 研判：基于当前贵金属快照生成简短中文结论。
"""
from __future__ import annotations

import json
from urllib import error, request

from app_config import MetalMonitorConfig
from prompt_templates import AI_BRIEF_SYSTEM_PROMPT, build_metal_brief_prompt


def build_snapshot_prompt(snapshot: dict) -> str:
    return build_metal_brief_prompt(snapshot)


def _post_json(url: str, payload: dict, api_key: str, timeout: int = 30) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=str(url).strip(),
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {str(api_key).strip()}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="ignore")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(str(exc)) from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"模型返回了无法解析的响应：{text[:200]}") from exc


def _post_json_with_headers(url: str, payload: dict, headers: dict[str, str], timeout: int = 30) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=str(url).strip(),
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="ignore")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(str(exc)) from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"模型返回了无法解析的响应：{text[:200]}") from exc


def _is_anthropic_api(api_base: str) -> bool:
    return "anthropic.com" in str(api_base or "").strip().lower()


def _build_chat_completions_url(api_base: str) -> str:
    base = str(api_base or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _extract_openai_content(response: dict) -> str:
    choices = list(response.get("choices", []) or [])
    if not choices:
        raise RuntimeError(f"模型响应中没有 choices：{response}")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = str(message.get("content", "") or "").strip()
    if not content:
        raise RuntimeError("模型返回为空，无法生成研判。")
    return content


def _extract_anthropic_content(response: dict) -> str:
    blocks = list(response.get("content", []) or [])
    texts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if str(block.get("type", "") or "").strip() != "text":
            continue
        text = str(block.get("text", "") or "").strip()
        if text:
            texts.append(text)
    content = "\n".join(texts).strip()
    if not content:
        raise RuntimeError(f"Anthropic 模型返回为空：{response}")
    return content


def request_ai_brief(snapshot: dict, config: MetalMonitorConfig) -> dict:
    api_key = str(config.ai_api_key or "").strip()
    if not api_key:
        raise RuntimeError("当前未配置 AI_API_KEY，无法执行 AI 研判。")

    api_base = str(config.ai_api_base or "https://api.siliconflow.cn/v1").strip().rstrip("/")
    model = str(config.ai_model or "deepseek-ai/DeepSeek-R1").strip()
    prompt = build_snapshot_prompt(snapshot)

    if _is_anthropic_api(api_base):
        payload = {
            "model": model,
            "max_tokens": 800,
            "temperature": 0.2,
            "system": AI_BRIEF_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = _post_json_with_headers(
            f"{api_base}/messages",
            payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        content = _extract_anthropic_content(response)
    else:
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": AI_BRIEF_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        response = _post_json(_build_chat_completions_url(api_base), payload, api_key=api_key)
        content = _extract_openai_content(response)

    return {"content": content, "model": model, "api_base": api_base}
