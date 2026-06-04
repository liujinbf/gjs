"""
轻量 AI 研判：基于当前贵金属快照生成简短中文结论。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib import error, request

from app_config import MetalMonitorConfig, PROJECT_DIR
from knowledge_rulebook import build_rulebook
from prompt_templates import (
    AI_BRIEF_SYSTEM_PROMPT,
    AI_SCALP_SYSTEM_PROMPT,
    AI_BULL_PERSPECTIVE_SYSTEM_PROMPT,
    AI_BEAR_PERSPECTIVE_SYSTEM_PROMPT,
    AI_DEBATE_ARBITRATOR_SYSTEM_PROMPT,
    build_metal_brief_prompt,
    build_scalp_brief_prompt,
    build_bull_perspective_prompt,
    build_bear_perspective_prompt,
    build_arbitrator_prompt,
)
from backtest_engine import extract_signal_meta, get_historical_win_rate
from signal_protocol import SIGNAL_SCHEMA_VERSION, build_empty_signal_meta, normalize_signal_meta, validate_signal_meta

try:
    from json_repair import loads as _json_repair_loads
except ImportError:
    _json_repair_loads = None

logger = logging.getLogger(__name__)

JSON_CODE_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
RUNTIME_DIR = PROJECT_DIR / ".runtime"
AI_RESPONSE_AUDIT_FILE = RUNTIME_DIR / "ai_response_audit.jsonl"
MAX_AI_RESPONSE_AUDIT_LINES = 300
MAX_AI_RESPONSE_TEXT_CHARS = 8000

AI_FAILURE_REASON_TEXT = {
    "missing_key": "AI 密钥未配置",
    "insufficient_balance": "AI 账户余额不足",
    "unauthorized": "AI 密钥无效或已过期",
    "forbidden": "AI 密钥权限不足",
    "rate_limited": "AI 请求频率超限",
    "model_not_found": "AI 模型或接口地址不存在",
    "timeout": "AI 请求超时",
    "parse_error": "AI 响应解析失败",
    "empty_response": "AI 返回内容为空",
    "network_error": "AI 网络连接失败",
    "unknown": "AI 请求失败",
}


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

    effective_rulebook = dict(
        rulebook
        or build_rulebook(current_regime_tag=str(snapshot_copy.get("regime_tag", "") or "").strip())
    )
    items = list(snapshot_copy.get("items", []) or [])
    has_scalp = False
    for item in items:
        val = False
        if isinstance(item, dict):
            val = item.get("scalp_ready", False)
        elif hasattr(item, "scalp_ready"):
            val = getattr(item, "scalp_ready", False)
        elif hasattr(item, "to_dict"):
            val = item.to_dict().get("scalp_ready", False)
        if bool(val):
            has_scalp = True
            break

    if has_scalp:
        return build_scalp_brief_prompt(snapshot_copy, rulebook=effective_rulebook)
    return build_metal_brief_prompt(snapshot_copy, rulebook=effective_rulebook)


def classify_ai_failure_reason(error_text: object) -> dict:
    """把底层异常转成用户可理解、可统计的 AI 失败原因。"""
    raw = str(error_text or "").strip()
    lowered = raw.lower()
    reason_key = "unknown"
    if not raw:
        reason_key = "unknown"
    elif "api_key" in lowered or "api key" in lowered or "密钥未配置" in raw:
        reason_key = "missing_key"
    elif "insufficient balance" in lowered or "http 402" in lowered or "余额不足" in raw:
        reason_key = "insufficient_balance"
    elif "http 401" in lowered or "unauthorized" in lowered or "invalid api key" in lowered:
        reason_key = "unauthorized"
    elif "http 403" in lowered or "forbidden" in lowered or "permission" in lowered:
        reason_key = "forbidden"
    elif "http 429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        reason_key = "rate_limited"
    elif "http 404" in lowered or "model_not_found" in lowered or "model not found" in lowered:
        reason_key = "model_not_found"
    elif "timed out" in lowered or "timeout" in lowered or "超时" in raw:
        reason_key = "timeout"
    elif "无法解析" in raw or "未返回合法 json" in raw or ("json" in lowered and "parse" in lowered):
        reason_key = "parse_error"
    elif "模型返回为空" in raw or ("empty" in lowered and "response" in lowered):
        reason_key = "empty_response"
    elif "urlopen error" in lowered or "connection" in lowered or "network" in lowered:
        reason_key = "network_error"
    return {
        "fallback_reason_key": reason_key,
        "fallback_reason_text": AI_FAILURE_REASON_TEXT.get(reason_key, AI_FAILURE_REASON_TEXT["unknown"]),
        "fallback_reason_detail": raw,
    }


def _apply_fallback_reason(result: dict, reason: object) -> dict:
    payload = dict(result or {})
    reason_payload = classify_ai_failure_reason(reason)
    payload.update(reason_payload)
    detail = str(reason_payload.get("fallback_reason_detail", "") or "").strip()
    text = str(reason_payload.get("fallback_reason_text", "") or "").strip()
    payload["fallback_reason"] = detail or text
    content = str(payload.get("content", "") or "")
    if content and "AI 离线" in content:
        content = content.replace("AI 离线", f"AI不可用：{text}", 1)
    if content and "AI 研判当前不可用" in content:
        content = content.replace("AI 研判当前不可用", f"AI 研判当前不可用（{text}）", 1)
    payload["content"] = content
    return payload


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
    """通用 JSON POST 请求。

    只使用 urlopen 自身的 timeout 参数，避免污染全局 socket 默认超时。
    """
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


def _atomic_write_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_file = target.with_name(f"{target.name}.tmp")
    temp_file.write_text(text, encoding="utf-8")
    temp_file.replace(target)


def _append_ai_response_audit(entry: dict, audit_file: Path | None = None) -> int:
    target = Path(audit_file) if audit_file else AI_RESPONSE_AUDIT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(entry or {})
    payload.setdefault("occurred_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    raw_content = str(payload.get("raw_content", "") or "")
    payload["raw_content_length"] = len(raw_content)
    if len(raw_content) > MAX_AI_RESPONSE_TEXT_CHARS:
        payload["raw_content"] = raw_content[:MAX_AI_RESPONSE_TEXT_CHARS]
        payload["raw_content_truncated"] = True
    else:
        payload["raw_content_truncated"] = False

    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    try:
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) > MAX_AI_RESPONSE_AUDIT_LINES:
            _atomic_write_text(target, "\n".join(lines[-MAX_AI_RESPONSE_AUDIT_LINES:]) + "\n")
    except OSError:
        pass
    return 1


def _try_load_json_dict(raw_text: str) -> dict | None:
    candidate = str(raw_text or "").strip()
    if not candidate:
        return None
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    if _json_repair_loads is not None:
        try:
            data = _json_repair_loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def _iter_json_object_candidates(text: str):
    raw_text = str(text or "")
    for match in JSON_CODE_BLOCK_PATTERN.finditer(raw_text):
        candidate = str(match.group(1) or "").strip()
        if candidate:
            yield candidate

    start = -1
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(raw_text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = raw_text[start:index + 1].strip()
                if candidate:
                    yield candidate
                start = -1


def _load_json_dict(text: str) -> dict | None:
    raw_text = str(text or "").strip()
    if not raw_text:
        return None
    direct = _try_load_json_dict(raw_text)
    if isinstance(direct, dict):
        return direct

    seen = {raw_text}
    for candidate in _iter_json_object_candidates(raw_text):
        if candidate in seen:
            continue
        seen.add(candidate)
        parsed = _try_load_json_dict(candidate)
        if isinstance(parsed, dict):
            return parsed
    return None

def _normalize_brief_result(content_text: str) -> dict:
    raw_text = str(content_text or "").strip()
    payload = _load_json_dict(raw_text)
    if isinstance(payload, dict):
        summary_text = str(
            payload.get("summary_text", "")
            or payload.get("content", "")
            or payload.get("analysis_text", "")
            or ""
        ).strip()
        signal_meta = payload.get("signal_meta")
        if not isinstance(signal_meta, dict):
            signal_meta = payload.get("tracker_meta")
        if not summary_text:
            # 兼容模型只返回纯 signal_meta 的异常情况
            summary_text = "当前结论：模型已返回结构化结果，但未提供正文摘要。"
        normalized_signal = normalize_signal_meta(signal_meta)
        signal_valid, signal_reason = validate_signal_meta(normalized_signal)
        return {
            "content": summary_text,
            "signal_meta": normalized_signal,
            "signal_schema_version": SIGNAL_SCHEMA_VERSION,
            "signal_meta_valid": signal_valid,
            "signal_meta_reason": signal_reason,
            "used_structured_payload": True,
        }

    legacy_meta = extract_signal_meta(raw_text)
    normalized_signal = normalize_signal_meta(legacy_meta)
    signal_valid, signal_reason = validate_signal_meta(normalized_signal)
    return {
        "content": raw_text,
        "signal_meta": normalized_signal,
        "signal_schema_version": SIGNAL_SCHEMA_VERSION,
        "signal_meta_valid": signal_valid,
        "signal_meta_reason": signal_reason,
        "used_structured_payload": False,
    }


def _build_json_retry_message(raw_content: str) -> str:
    excerpt = str(raw_content or "").strip().replace("\r", " ").replace("\n", " ")
    excerpt = excerpt[:280]
    return (
        "你上一条回复不是可直接解析的合法 JSON。"
        "请不要输出解释、Markdown、代码块或多余前后缀，"
        "只返回一个 JSON 对象，结构必须严格为 "
        '{"summary_text":"...","signal_meta":{"symbol":"...","action":"long/short/neutral","price":0,"sl":0,"tp":0}}。'
        f"上一条错误输出摘录：{excerpt}"
    )


def _normalize_or_raise_structured(content_text: str) -> dict:
    normalized = _normalize_brief_result(content_text)
    if not bool(normalized.get("used_structured_payload", False)):
        raise RuntimeError("模型未返回合法 JSON 对象")
    return normalized


def _finalize_ai_parse_result(
    normalized: dict,
    *,
    raw_content: str,
    provider: str,
    api_base: str,
    model: str,
    parse_mode: str,
    parse_error: str = "",
) -> dict:
    result = dict(normalized or {})
    signal_meta = dict(result.get("signal_meta", {}) or {})
    raw_text = str(raw_content or "")
    audit_entry = {
        "provider": str(provider or "").strip(),
        "api_base": str(api_base or "").strip(),
        "model": str(model or "").strip(),
        "parse_mode": str(parse_mode or "").strip(),
        "parse_ok": bool(result.get("used_structured_payload", False)),
        "parse_error": str(parse_error or "").strip(),
        "signal_action": str(signal_meta.get("action", "neutral") or "neutral").strip().lower(),
        "signal_meta_valid": bool(result.get("signal_meta_valid", False)),
        "signal_meta_reason": str(result.get("signal_meta_reason", "") or "").strip(),
        "raw_content": raw_text,
    }
    try:
        _append_ai_response_audit(audit_entry)
        logged = True
    except Exception as exc:  # noqa: BLE001
        logger.debug("AI 原始响应审计写入失败：%s", exc)
        logged = False

    result.update(
        {
            "ai_parse_mode": str(parse_mode or "").strip(),
            "ai_raw_response_logged": logged,
            "ai_raw_response_length": len(raw_text),
            "ai_raw_response_excerpt": raw_text[:500],
        }
    )
    return result


def _request_openai_brief_result(api_base: str, payload: dict, api_key: str) -> dict:
    url = _build_chat_completions_url(api_base)
    model = str(payload.get("model", "") or "").strip()
    structured_payload = dict(payload)
    structured_payload["response_format"] = {"type": "json_object"}
    try:
        response = _post_json(url, structured_payload, api_key=api_key)
        content = _extract_openai_content(response)
        try:
            normalized = _normalize_or_raise_structured(content)
            return _finalize_ai_parse_result(
                normalized,
                raw_content=content,
                provider="openai-compatible",
                api_base=api_base,
                model=model,
                parse_mode="json_mode",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenAI-compatible JSON 结构校验失败，尝试一次自愈重试：%s", exc)
            retry_payload = dict(structured_payload)
            retry_messages = list(structured_payload.get("messages", []) or [])
            retry_messages.extend(
                [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": _build_json_retry_message(content)},
                ]
            )
            retry_payload["messages"] = retry_messages
            retry_response = _post_json(url, retry_payload, api_key=api_key)
            retry_content = _extract_openai_content(retry_response)
            normalized = _normalize_or_raise_structured(retry_content)
            return _finalize_ai_parse_result(
                normalized,
                raw_content=retry_content,
                provider="openai-compatible",
                api_base=api_base,
                model=model,
                parse_mode="json_retry",
                parse_error=str(exc),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAI-compatible JSON mode 失败，回退普通文本模式：%s", exc)
        response = _post_json(url, payload, api_key=api_key)
        content = _extract_openai_content(response)
        normalized = _normalize_brief_result(content)
        return _finalize_ai_parse_result(
            normalized,
            raw_content=content,
            provider="openai-compatible",
            api_base=api_base,
            model=model,
            parse_mode="text_fallback",
            parse_error=str(exc),
        )


def _request_anthropic_brief_result(api_base: str, payload: dict, api_key: str) -> dict:
    url = f"{api_base}/messages"
    model = str(payload.get("model", "") or "").strip()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    response = _post_json_with_headers(url, payload, headers=headers)
    content = _extract_anthropic_content(response)
    try:
        normalized = _normalize_or_raise_structured(content)
        return _finalize_ai_parse_result(
            normalized,
            raw_content=content,
            provider="anthropic",
            api_base=api_base,
            model=model,
            parse_mode="json_mode",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Anthropic JSON 结构校验失败，尝试一次自愈重试：%s", exc)
        retry_payload = dict(payload)
        retry_messages = list(payload.get("messages", []) or [])
        retry_messages.extend(
            [
                {"role": "assistant", "content": content},
                {"role": "user", "content": _build_json_retry_message(content)},
            ]
        )
        retry_payload["messages"] = retry_messages
        retry_response = _post_json_with_headers(url, retry_payload, headers=headers)
        retry_content = _extract_anthropic_content(retry_response)
        normalized = _normalize_or_raise_structured(retry_content)
        return _finalize_ai_parse_result(
            normalized,
            raw_content=retry_content,
            provider="anthropic",
            api_base=api_base,
            model=model,
            parse_mode="json_retry",
            parse_error=str(exc),
        )


def _request_text_only(system_prompt: str, user_prompt: str, config: "MetalMonitorConfig") -> str:
    """
    单次 LLM 调用，返回纯文本（不做 JSON 解析）。
    用于辩论模式的看多/看空两个视角阶段。
    """
    api_key = str(config.ai_api_key or "").strip()
    api_base = str(config.ai_api_base or "https://api.siliconflow.cn/v1").strip().rstrip("/")
    model = str(config.ai_model or "deepseek-ai/DeepSeek-R1").strip()

    if _is_anthropic_api(api_base):
        url = f"{api_base}/messages"
        payload = {
            "model": model,
            "max_tokens": 500,
            "temperature": 0.3,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        response = _post_json_with_headers(url, payload, headers=headers)
        return _extract_anthropic_content(response)
    else:
        url = _build_chat_completions_url(api_base)
        payload = {
            "model": model,
            "temperature": 0.3,
            "max_tokens": 500,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = _post_json(url, payload, api_key=api_key)
        return _extract_openai_content(response)


def _request_debate_brief(
    snapshot: dict,
    config: "MetalMonitorConfig",
    rulebook: dict | None = None,
) -> dict:
    """
    三阶段辩论研判：
      阶段一：看多分析师找利多证据（纯文本，省 token）
      阶段二：看空/谨慎分析师找利空/观望证据（纯文本）
      阶段三：仲裁者综合双方论据 + 完整快照 → 最终研判（JSON 输出）

    只在非 scalp 中长线研判时调用，任何阶段异常均由调用方捕获并降级到标准单轨模式。
    """
    api_base = str(config.ai_api_base or "https://api.siliconflow.cn/v1").strip().rstrip("/")
    model = str(config.ai_model or "deepseek-ai/DeepSeek-R1").strip()
    api_key = str(config.ai_api_key or "").strip()

    logger.info("辩论研判 阶段一：看多视角分析")
    bull_text = _request_text_only(
        AI_BULL_PERSPECTIVE_SYSTEM_PROMPT,
        build_bull_perspective_prompt(snapshot),
        config,
    )

    logger.info("辩论研判 阶段二：看空/谨慎视角分析")
    bear_text = _request_text_only(
        AI_BEAR_PERSPECTIVE_SYSTEM_PROMPT,
        build_bear_perspective_prompt(snapshot),
        config,
    )

    logger.info("辩论研判 阶段三：仲裁者综合输出最终研判")
    arbitrator_prompt = build_arbitrator_prompt(snapshot, bull_text, bear_text, rulebook=rulebook)

    if _is_anthropic_api(api_base):
        payload = {
            "model": model,
            "max_tokens": 1000,
            "temperature": 0.2,
            "system": AI_DEBATE_ARBITRATOR_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": arbitrator_prompt}],
        }
        normalized = _request_anthropic_brief_result(api_base, payload, api_key=api_key)
    else:
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": AI_DEBATE_ARBITRATOR_SYSTEM_PROMPT},
                {"role": "user", "content": arbitrator_prompt},
            ],
        }
        normalized = _request_openai_brief_result(api_base, payload, api_key=api_key)

    return {
        "content": normalized["content"],
        "signal_meta": normalized["signal_meta"],
        "signal_schema_version": normalized["signal_schema_version"],
        "signal_meta_valid": normalized["signal_meta_valid"],
        "signal_meta_reason": normalized["signal_meta_reason"],
        "used_structured_payload": bool(normalized.get("used_structured_payload", False)),
        "ai_parse_mode": str(normalized.get("ai_parse_mode", "") or "").strip(),
        "ai_raw_response_logged": bool(normalized.get("ai_raw_response_logged", False)),
        "ai_raw_response_length": int(normalized.get("ai_raw_response_length", 0) or 0),
        "ai_raw_response_excerpt": str(normalized.get("ai_raw_response_excerpt", "") or ""),
        "model": model,
        "api_base": api_base,
        "rulebook_summary_text": str((rulebook or {}).get("summary_text", "") or "").strip(),
        # 辩论模式元数据
        "debate_mode": True,
        "debate_bull_excerpt": str(bull_text or "")[:300],
        "debate_bear_excerpt": str(bear_text or "")[:300],
    }


def request_ai_brief(
    snapshot: dict,
    config: MetalMonitorConfig,
    allow_fallback: bool = True,
) -> dict:
    """
    向 AI 接口请求研判简报。

    allow_fallback=True（默认）时，以下情况自动切换为规则引擎降级简报：
      - AI API Key 未配置
      - 网络超时、HTTP 错误、响应解析失败

    allow_fallback=False 时，上述情况直接抛出异常（适合手动触发时给用户明确报错）。
    """
    api_key = str(config.ai_api_key or "").strip()
    if not api_key:
        if allow_fallback:
            logger.warning("AI API Key 未配置，启用规则引擎降级模式")
            result = _rule_engine_fallback(snapshot)
            return _apply_fallback_reason(result, "AI API Key 未配置")
        raise RuntimeError("当前未配置 AI_API_KEY，无法执行 AI 研判。")

    api_base = str(config.ai_api_base or "https://api.siliconflow.cn/v1").strip().rstrip("/")
    model = str(config.ai_model or "deepseek-ai/DeepSeek-R1").strip()
    rulebook = build_rulebook(current_regime_tag=str(snapshot.get("regime_tag", "") or "").strip())
    prompt = build_snapshot_prompt(snapshot, rulebook=rulebook)

    items = list(snapshot.get("items", []) or [])
    has_scalp = False
    for item in items:
        val = False
        if isinstance(item, dict):
            val = item.get("scalp_ready", False)
        elif hasattr(item, "scalp_ready"):
            val = getattr(item, "scalp_ready", False)
        elif hasattr(item, "to_dict"):
            val = item.to_dict().get("scalp_ready", False)
        if bool(val):
            has_scalp = True
            break

    system_prompt = AI_SCALP_SYSTEM_PROMPT if has_scalp else AI_BRIEF_SYSTEM_PROMPT

    # ── 辩论模式（仅对非 scalp 中长线研判启用）────────────────────────────────
    # 短线(scalp)时效要求极高，保持原有单轨，不走辩论；
    # 非 scalp 研判自动启用三阶段辩论，任何阶段失败则静默降级到标准单轨。
    if not has_scalp:
        try:
            return _request_debate_brief(snapshot, config, rulebook=rulebook)
        except Exception as debate_exc:  # noqa: BLE001
            logger.warning(
                "辩论研判模式异常，自动降级到标准单轨模式：%s", debate_exc
            )
            # 降级后继续执行下方标准单轨逻辑
    # ── 标准单轨模式（scalp 或辩论降级后的备用路径）──────────────────────────
    try:
        if _is_anthropic_api(api_base):
            payload = {
                "model": model,
                "max_tokens": 800,
                "temperature": 0.2,
                "system": system_prompt,
                "messages": [{"role": "user", "content": prompt}],
            }
            normalized = _request_anthropic_brief_result(api_base, payload, api_key=api_key)
        else:
            payload = {
                "model": model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            }
            normalized = _request_openai_brief_result(api_base, payload, api_key=api_key)

        return {
            "content": normalized["content"],
            "signal_meta": normalized["signal_meta"],
            "signal_schema_version": normalized["signal_schema_version"],
            "signal_meta_valid": normalized["signal_meta_valid"],
            "signal_meta_reason": normalized["signal_meta_reason"],
            "used_structured_payload": bool(normalized.get("used_structured_payload", False)),
            "ai_parse_mode": str(normalized.get("ai_parse_mode", "") or "").strip(),
            "ai_raw_response_logged": bool(normalized.get("ai_raw_response_logged", False)),
            "ai_raw_response_length": int(normalized.get("ai_raw_response_length", 0) or 0),
            "ai_raw_response_excerpt": str(normalized.get("ai_raw_response_excerpt", "") or ""),
            "model": model,
            "api_base": api_base,
            "rulebook_summary_text": str(rulebook.get("summary_text", "") or "").strip(),
        }

    except Exception as exc:  # noqa: BLE001
        if allow_fallback:
            reason = str(exc)
            logger.warning(f"AI 研判失败，启用规则引擎降级模式：{reason}")
            result = _rule_engine_fallback(snapshot)
            return _apply_fallback_reason(result, reason)
        raise


def _rule_engine_fallback(snapshot: dict) -> dict:
    """内部辅助：调用规则引擎生成降级简报，捕获所有异常保证永不崩溃。"""
    try:
        from rule_engine_brief import generate_rule_engine_brief
        return generate_rule_engine_brief(snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"规则引擎降级也失败：{exc}")
        return {
            "content": (
                "[🔴 系统降级失败] AI 研判和规则引擎均不可用。\n"
                "请检查：① AI API Key 是否配置正确；② 网络连接是否正常。\n"
            ),
            "signal_meta": build_empty_signal_meta(),
            "signal_schema_version": SIGNAL_SCHEMA_VERSION,
            "signal_meta_valid": True,
            "signal_meta_reason": "应急降级，禁止自动执行",
            "model": "emergency-fallback",
            "api_base": "local",
            "rulebook_summary_text": "",
            "is_fallback": True,
            "fallback_reason": str(exc),
            **classify_ai_failure_reason(str(exc)),
        }
