#!/usr/bin/env python3
"""LLM proxy diagnostics for PageIndex and review agents."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(PROJECT_DIR))


def _load_dotenv_if_available():
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_DIR / ".env")
        load_dotenv(APP_DIR / ".env")
    except Exception:
        pass


def _candidate_models(extra_models=None):
    if extra_models:
        candidates = []
        for model in extra_models:
            if model and model not in candidates:
                candidates.append(model)
        return candidates

    candidates = []
    for value in [
        os.getenv("PAGEINDEX_MODEL"),
        os.getenv("LLM_MODEL"),
        "qwen3.5-plus",
        "gpt-5.4",
        "gpt-5.2",
        "gpt-5.1",
        "gpt-5",
        "openai/claude-sonnet-4-6",
        "claude-sonnet-4-6",
    ]:
        if value and value not in candidates:
            candidates.append(value)
    if extra_models:
        for model in extra_models:
            if model and model not in candidates:
                candidates.append(model)
    return candidates


def _fetch_proxy_models():
    import requests
    from auditors.engineering_auditor import API_KEY, API_URL, LLM_API_TYPE

    if LLM_API_TYPE == "anthropic":
        return _candidate_models()

    models_url = API_URL.rsplit("/chat/completions", 1)[0] + "/models"
    response = requests.get(models_url, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    models = []
    for item in payload.get("data", []):
        model_id = item.get("id")
        endpoints = item.get("supported_endpoint_types") or []
        if model_id and (not endpoints or "openai" in endpoints):
            models.append(model_id)
    return models


def _configure_litellm_env():
    from auditors.engineering_auditor import API_KEY, API_URL, LLM_API_TYPE

    if LLM_API_TYPE == "anthropic":
        os.environ.setdefault("ANTHROPIC_API_KEY", API_KEY)
        os.environ.setdefault("ANTHROPIC_API_BASE", API_URL.rsplit("/messages", 1)[0])
        return

    os.environ.setdefault("OPENAI_API_KEY", API_KEY)
    os.environ.setdefault("OPENAI_API_BASE", API_URL.rsplit("/chat/completions", 1)[0])


def _preview_raw(raw, limit=800):
    text = json.dumps(raw, ensure_ascii=False)
    return text[:limit] + ("..." if len(text) > limit else "")


def _direct_check(model, prompt, timeout, show_raw=False):
    from auditors.engineering_auditor import (
        _build_chat_payload,
        _extract_llm_content,
        LLM_API_TYPE,
        _post_chat_completion,
    )

    payload = _build_chat_payload(
        [{"role": "user", "content": prompt}],
        model=model,
        max_tokens=256,
        temperature=0.1,
    )
    try:
        raw = _post_chat_completion(payload, timeout=timeout)
        status_code = 200
    except Exception as exc:
        return {
            "via": "direct",
            "provider": LLM_API_TYPE,
            "model": model,
            "status_code": None,
            "ok": False,
            "content_preview": "",
            "finish_reason": None,
            "completion_tokens": None,
            "reasoning_tokens": None,
            "raw_preview": f"{type(exc).__name__}: {exc}",
        }

    content = _extract_llm_content(raw) if isinstance(raw, dict) else ""
    choice = raw.get("choices", [{}])[0] if isinstance(raw, dict) else {}
    usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
    return {
        "via": "direct",
        "provider": LLM_API_TYPE,
        "model": model,
        "status_code": status_code,
        "ok": bool(content),
        "content_preview": content[:120],
        "finish_reason": choice.get("finish_reason"),
        "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "raw_preview": _preview_raw(raw) if show_raw or not content else "",
    }


def _is_rate_limited(result):
    return result.get("status_code") == 429 or "请求数限制" in str(result.get("raw_preview") or "")


def _print_result(item):
    status = "OK" if item["ok"] else "EMPTY"
    print(
        f"[{status}] via={item['via']} provider={item.get('provider', '')} model={item['model']} "
        f"http={item['status_code']} finish={item['finish_reason']} "
        f"completion={item['completion_tokens']} reasoning={item['reasoning_tokens']} "
        f"content={item['content_preview']!r}",
        flush=True,
    )
    if item["raw_preview"]:
        print(f"  raw: {item['raw_preview']}", flush=True)


def _litellm_check(model, prompt, timeout, show_raw=False):
    from auditors.engineering_auditor import LLM_API_TYPE

    if LLM_API_TYPE == "anthropic":
        return {
            "via": "litellm",
            "provider": LLM_API_TYPE,
            "model": model,
            "status_code": None,
            "ok": False,
            "content_preview": "",
            "finish_reason": None,
            "completion_tokens": None,
            "reasoning_tokens": None,
            "raw_preview": "当前 Anthropic 自定义 /v1/messages 通路由项目 direct adapter 调用；litellm 诊断已跳过。",
        }

    _configure_litellm_env()

    import litellm

    litellm.drop_params = True
    try:
        stream = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=256,
            timeout=timeout,
            stream=True,
        )
        content_parts = []
        raw_events = []
        finish_reason = None
        for chunk in stream:
            raw = chunk.model_dump() if hasattr(chunk, "model_dump") else {}
            if len(raw_events) < 5:
                raw_events.append(raw)
            choice = raw.get("choices", [{}])[0] if isinstance(raw, dict) else {}
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str):
                content_parts.append(content)
        content = "".join(content_parts).strip()
        raw = {"stream_events": raw_events}
        return {
            "via": "litellm",
            "provider": LLM_API_TYPE,
            "model": model,
            "status_code": 200,
            "ok": bool(content),
            "content_preview": content[:120],
            "finish_reason": finish_reason,
            "completion_tokens": None,
            "reasoning_tokens": None,
            "raw_preview": _preview_raw(raw) if show_raw or not content else "",
        }
    except Exception as exc:
        return {
            "via": "litellm",
            "provider": LLM_API_TYPE,
            "model": model,
            "status_code": None,
            "ok": False,
            "content_preview": "",
            "finish_reason": None,
            "completion_tokens": None,
            "reasoning_tokens": None,
            "raw_preview": f"{type(exc).__name__}: {exc}",
        }


def main():
    parser = argparse.ArgumentParser(description="检测 LLM 代理是否返回有效文本")
    parser.add_argument("--models", default="", help="逗号分隔的模型名；留空使用默认候选")
    parser.add_argument("--all-models", action="store_true", help="从代理 /models 自动拉取并扫描所有 openai 兼容模型")
    parser.add_argument("--max-models", type=int, default=0, help="最多扫描前 N 个模型，0 表示不限制")
    parser.add_argument("--stop-on-ok", action="store_true", help="找到首个可返回文本的模型后立即停止")
    parser.add_argument("--stop-on-rate-limit", action=argparse.BooleanOptionalAction, default=True, help="遇到 429 限流后立即停止")
    parser.add_argument("--sleep-between", type=float, default=0.0, help="每次请求后的等待秒数；全量扫描默认 4.2 秒")
    parser.add_argument("--via", choices=["direct", "litellm", "both"], default="both")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--show-raw", action="store_true", help="显示原始响应预览")
    parser.add_argument("--prompt", default='请严格输出 JSON：{"status":"ok","text":"总则"}，不要解释。')
    args = parser.parse_args()

    _load_dotenv_if_available()
    from auditors.engineering_auditor import LLM_API_TYPE

    if LLM_API_TYPE == "anthropic" and args.via == "both":
        args.via = "direct"
    extra_models = [m.strip() for m in args.models.split(",") if m.strip()]
    models = _fetch_proxy_models() if args.all_models else _candidate_models(extra_models)
    if args.max_models > 0:
        models = models[:args.max_models]

    checks = []
    found_ok = False
    effective_sleep = args.sleep_between if args.sleep_between > 0 else (4.2 if args.all_models else 0.0)

    def record(result):
        nonlocal found_ok
        checks.append(result)
        _print_result(result)
        found_ok = found_ok or result["ok"]
        if args.stop_on_ok and found_ok:
            return "stop"
        if args.stop_on_rate_limit and _is_rate_limited(result):
            return "stop"
        if effective_sleep > 0:
            time.sleep(effective_sleep)
        return "continue"

    for model in models:
        if args.via in ("direct", "both"):
            result = _direct_check(model, args.prompt, args.timeout, args.show_raw)
            if record(result) == "stop":
                break
        if args.via in ("litellm", "both"):
            result = _litellm_check(model, args.prompt, args.timeout, args.show_raw)
            if record(result) == "stop":
                break

    if not any(item["ok"] for item in checks):
        raise SystemExit("未发现可返回有效文本的模型/通路。请检查代理、模型名或 API 密钥。")


if __name__ == "__main__":
    main()
