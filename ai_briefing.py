"""
轻量 AI 研判：基于当前贵金属快照生成简短中文结论。
"""
from __future__ import annotations

import json
from urllib import error, request

from app_config import MetalMonitorConfig
from knowledge_rulebook import build_rulebook
from prompt_templates import AI_BRIEF_SYSTEM_PROMPT, build_metal_brief_prompt
from backtest_engine import get_historical_win_rate


def build_snapshot_prompt(snapshot: dict, rulebook: dict | None = None) -> str:
    # N-003 修复：操作副本而非原始 snapshot，避免污染 self._last_snapshot
    snapshot_copy = dict(snapshot)
    stats = get_historical_win_rate(days=90)
    total = stats.get("total", 0)
    wins = stats.get("wins", 0)
    rate = stats.get("rate", 0.0)

    if total > 0:
        wr_text = f"【历史系统验证】：过去90天记录的AI决策共{total}次，其中提前命中目标{wins}次，整体胜率约 {rate:.1f}%。"
    else:
        wr_text = "【历史系统验证】：历史信号胜率组件正在回测计算中，暂无有效样本。"

    market_text = str(snapshot_copy.get("market_text", "") or "").strip()
    snapshot_copy["market_text"] = market_text + "\n" + wr_text if market_text else wr_text

    effective_rulebook = dict(rulebook or build_rulebook())
    return build_metal_brief_prompt(snapshot_copy, rulebook=effective_rulebook)


def _post_json(url: str, payload: dict, api_key: str, timeout: int = 90) -> dict:
    """标准 OpenAI-compatible 接口请求（Bearer token 认证）。"""
    return _post_json_with_headers(
        url,
        payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {str(api_key).strip()}",
        },
        timeout=timeout,
    )


def _post_json_with_headers(url: str, payload: dict, headers: dict[str, str], timeout: int = 90) -> dict:
    """N-002 修复：通用 JSON POST 请求，使用 socket.setdefaulttimeout 保护连接阶段超时。
    N-006 修复：合并原来两个 99% 相同的函数，消除代码重复。
    """
    import socket as _socket
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url=str(url).strip(),
        data=data,
        headers=headers,
        method="POST",
    )
    # N-002：socket 全局超时保护连接阶段（DNS 解析 + TCP 握手），防止无限卡住
    prev_timeout = _socket.getdefaulttimeout()
    _socket.setdefaulttimeout(timeout)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="ignore")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(str(exc)) from exc
    finally:
        _socket.setdefaulttimeout(prev_timeout)  # 恢复原有全局超时

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
    rulebook = build_rulebook()
    prompt = build_snapshot_prompt(snapshot, rulebook=rulebook)

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

    return {
        "content": content,
        "model": model,
        "api_base": api_base,
        "rulebook_summary_text": str(rulebook.get("summary_text", "") or "").strip(),
    }
