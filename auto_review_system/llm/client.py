"""
LLM 通讯客户端
==============
负责 HTTP 请求、QPS 限流、流式解析、重试、Anthropic/OpenAI 双协议适配。
从 engineering_auditor.py 中提取的纯基础设施代码，不包含业务审计逻辑。
"""
import json
import os
import time
import threading
import requests

from llm.config import (
    API_URL, API_KEY, LLM_MODEL, VISION_MODEL,
    LLM_API_TYPE, LLM_STREAM, LLM_MAX_CALLS_PER_MINUTE, LLM_SSL_VERIFY,
)

# ==================== QPS 限流 ====================

_throttle_lock = threading.Lock()
_call_timestamps = []


def throttle_qps(max_qps=2):
    """全局 QPS 限流器——兼顾瞬时 QPS 和分钟级配额。"""
    global _call_timestamps
    with _throttle_lock:
        now = time.time()
        _call_timestamps = [t for t in _call_timestamps if now - t < 60.0]
        recent_qps = [t for t in _call_timestamps if now - t < 1.0]
        sleep_time = 0.0
        if max_qps > 0 and len(recent_qps) >= max_qps:
            sleep_time = max(sleep_time, 1.0 - (now - recent_qps[0]))
        if LLM_MAX_CALLS_PER_MINUTE > 0 and len(_call_timestamps) >= LLM_MAX_CALLS_PER_MINUTE:
            sleep_time = max(sleep_time, 60.0 - (now - _call_timestamps[0]))
        if sleep_time > 0:
            time.sleep(sleep_time)
            now = time.time()
            _call_timestamps = [t for t in _call_timestamps if now - t < 60.0]
            recent_qps = [t for t in _call_timestamps if now - t < 1.0]
            sleep_time = 0.0
            if max_qps > 0 and len(recent_qps) >= max_qps:
                sleep_time = 1.0 - (now - recent_qps[0])
            if sleep_time > 0:
                time.sleep(sleep_time)
        _call_timestamps.append(time.time())


# ==================== 响应解析 ====================

def extract_llm_content(result):
    """从 LLM 响应中提取文本内容（兼容 Anthropic Messages 和 OpenAI Chat Completions）。"""
    try:
        if isinstance(result.get("content"), list):
            return "".join(
                str(part.get("text") or "")
                for part in result["content"]
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
        message = result.get('choices', [{}])[0].get('message', {})
        content = message.get('content')
        if isinstance(content, list):
            return "".join(str(part.get("text", "") if isinstance(part, dict) else part) for part in content).strip()
        return str(content or "").strip()
    except Exception:
        return ""


def extract_stream_delta(event):
    """从流式事件中提取增量文本。"""
    if not isinstance(event, dict):
        return "", None, {}

    usage = event.get("usage") or {}
    finish_reason = None
    text_parts = []

    if isinstance(event.get("delta"), str):
        text_parts.append(event["delta"])
    if isinstance(event.get("output_text"), str):
        text_parts.append(event["output_text"])
    if event.get("type") in ("response.output_text.delta", "response.refusal.delta"):
        text_parts.append(str(event.get("delta") or ""))

    for choice in event.get("choices", []) or []:
        finish_reason = choice.get("finish_reason") or finish_reason
        delta = choice.get("delta") or {}
        message = choice.get("message") or {}

        for carrier in (delta, message):
            content = carrier.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        text_parts.append(str(part.get("text") or part.get("content") or ""))
                    else:
                        text_parts.append(str(part))

    return "".join(text_parts), finish_reason, usage


# ==================== Payload 构建 ====================

def build_chat_payload(messages, model=None, max_tokens=4096, temperature=0.1, extra=None):
    """构建 OpenAI Chat Completions 格式的请求体。"""
    payload = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if temperature is not None and LLM_API_TYPE != "anthropic" and "gpt-5" not in str(payload["model"]):
        payload["temperature"] = temperature
    if LLM_STREAM and LLM_API_TYPE != "anthropic":
        payload["stream"] = True
    if extra:
        payload.update(extra)
    return payload


# ==================== Anthropic 适配 ====================

def _anthropic_text_block_from_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "image_url":
                    parts.append("[image omitted: current model adapter is text-only]")
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "")


def _to_anthropic_payload(payload):
    system_parts = []
    messages = []
    for message in payload.get("messages", []):
        role = message.get("role", "user")
        content = _anthropic_text_block_from_content(message.get("content", ""))
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
            continue
        if role not in ("user", "assistant"):
            role = "user"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] = f"{messages[-1]['content']}\n\n{content}"
        else:
            messages.append({"role": role, "content": content})

    if not messages:
        messages = [{"role": "user", "content": ""}]

    anthropic_payload = {
        "model": payload.get("model") or LLM_MODEL,
        "max_tokens": payload.get("max_tokens") or 4096,
        "messages": messages,
    }
    if system_parts:
        anthropic_payload["system"] = "\n\n".join(system_parts)
    return anthropic_payload


def _post_anthropic_message(payload, timeout=90):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "Anthropic-Version": os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
    }
    if not LLM_SSL_VERIFY:
        try:
            requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
        except Exception:
            pass

    throttle_qps(2)
    response = requests.post(
        API_URL,
        headers=headers,
        json=_to_anthropic_payload(payload),
        timeout=timeout,
        verify=LLM_SSL_VERIFY,
    )
    response.raise_for_status()
    raw = response.json()
    content = extract_llm_content(raw)
    usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
    return {
        "object": "chat.completion",
        "provider": "anthropic",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content,
            },
            "finish_reason": raw.get("stop_reason") or "stop",
        }],
        "usage": {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
        },
        "raw_anthropic": raw,
    }


# ==================== 流式解析 ====================

def _parse_streaming_response(response, wall_timeout=None):
    content_parts = []
    finish_reason = None
    usage = {}
    raw_events = []
    start_time = time.monotonic()

    for raw_line in response.iter_lines(decode_unicode=False):
        if wall_timeout and time.monotonic() - start_time > wall_timeout:
            raise TimeoutError(f"LLM streaming response exceeded wall timeout: {wall_timeout}s")
        if not raw_line:
            continue
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace").strip()
        else:
            line = str(raw_line).strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            raw_events.append(line[:500])
            continue

        if len(raw_events) < 5:
            raw_events.append(event)

        delta, event_finish_reason, event_usage = extract_stream_delta(event)
        if delta:
            content_parts.append(delta)
        if event_finish_reason:
            finish_reason = event_finish_reason
        if event_usage:
            usage = event_usage

    return {
        "object": "chat.completion",
        "stream": True,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "".join(content_parts),
            },
            "finish_reason": finish_reason or "stop",
        }],
        "usage": usage,
        "stream_raw_events": raw_events,
    }


# ==================== 统一请求入口 ====================

def post_chat_completion(payload, timeout=90):
    """统一 LLM API 请求入口——自动路由到 Anthropic 或 OpenAI 协议。"""
    if LLM_API_TYPE == "anthropic":
        return _post_anthropic_message(payload, timeout=timeout)

    if not LLM_SSL_VERIFY:
        try:
            requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
        except Exception:
            pass

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }
    throttle_qps(2)
    stream = bool(payload.get("stream"))
    request_timeout = timeout
    if stream:
        read_timeout = int(os.getenv("LLM_STREAM_READ_TIMEOUT", str(min(max(int(timeout), 30), 90))))
        request_timeout = (10, read_timeout)
    response = requests.post(
        API_URL,
        headers=headers,
        json=payload,
        timeout=request_timeout,
        stream=stream,
        verify=LLM_SSL_VERIFY,
    )
    response.raise_for_status()
    if stream:
        wall_timeout = int(os.getenv("LLM_STREAM_WALL_TIMEOUT", str(timeout)))
        return _parse_streaming_response(response, wall_timeout=wall_timeout)
    return response.json()
