"""
万科工程审计 — 审计业务逻辑层
==============================
包含审计专用函数：call_llm, predict_wbs_code, analyze_vision_wbs, llm_rerank_rules,
audit_engineering_scheme。

LLM 通讯基础设施已迁移至 llm/ 模块，本文件通过 re-export 保持向后兼容。
"""
import json
import logging
import os
import re
import time
import hashlib
import inspect

import requests

# ==================== 从 llm/ 导入基础设施并 re-export ====================
from llm.config import (  # noqa: F401 — re-export for backward compat
    API_URL, API_KEY, LLM_MODEL, VISION_MODEL,
    LLM_API_TYPE, LLM_STREAM, LLM_MAX_CALLS_PER_MINUTE, LLM_SSL_VERIFY,
    LLM_REQUEST_TIMEOUT, LLM_VISION_TIMEOUT, LLM_MAX_RETRIES,
)
from llm.client import (  # noqa: F401
    throttle_qps as _throttle_qps,
    extract_llm_content as _extract_llm_content,
    extract_stream_delta as _extract_stream_delta,
    build_chat_payload as _build_chat_payload,
    post_chat_completion as _post_chat_completion,
    _parse_streaming_response as _parse_streaming_response,
    _to_anthropic_payload as _to_anthropic_payload,
)
from llm.cache import (
    build_cache_key,
    failure_ttl_seconds,
    get_cached_text,
    record_call,
    store_cached_text,
)

logger = logging.getLogger(__name__)
LLM_RUNTIME_FAILURE_PREFIX = "[SYSTEM_LLM_ERROR]"


def _format_llm_runtime_failure(message):
    return f"{LLM_RUNTIME_FAILURE_PREFIX} {message}"


def _friendly_llm_failure_message(exc_or_text, *, vision=False):
    raw_text = str(exc_or_text or "").strip()
    lowered = raw_text.lower()
    subject = "图像审查" if vision else "本条审查"

    if isinstance(exc_or_text, requests.exceptions.Timeout) or "timed out" in lowered:
        return f"模型服务响应超时，{subject}未生成，请稍后重试。"
    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        return f"模型服务当前繁忙，{subject}未生成，请稍后重试。"
    if (
        isinstance(exc_or_text, requests.exceptions.ConnectionError)
        or "connection" in lowered
        or "refused" in lowered
        or "reset by peer" in lowered
        or "service unavailable" in lowered
        or "bad gateway" in lowered
        or "gateway timeout" in lowered
    ):
        return f"模型服务暂时不可用，{subject}未生成，请稍后重试。"
    if "empty response" in lowered:
        return f"模型服务返回空响应，{subject}未生成，请稍后重试。"
    return f"模型服务异常，{subject}未生成，请稍后重试。"


def is_llm_runtime_failure(text):
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    return (
        stripped.startswith(LLM_RUNTIME_FAILURE_PREFIX)
        or stripped.startswith("LLM API Error:")
        or stripped.startswith("LLM API Empty Response:")
        or stripped.startswith("[视觉链路异常]")
    )


# ==================== 审计业务函数 ====================

def _caller_label():
    try:
        frame = inspect.currentframe()
        caller = frame.f_back.f_back if frame and frame.f_back else None
        if not caller:
            return ""
        module = inspect.getmodule(caller)
        module_name = module.__name__ if module else ""
        return f"{module_name}.{caller.f_code.co_name}".strip(".")
    except Exception:
        return ""


def _reasoning_extra_payload():
    """Optional provider-specific reasoning/thinking controls."""
    enabled = os.getenv("LLM_THINKING_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return {}
    if LLM_API_TYPE == "anthropic":
        budget = int(os.getenv("LLM_THINKING_BUDGET_TOKENS", "1024"))
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}
    effort = os.getenv("LLM_REASONING_EFFORT", "medium").strip().lower()
    return {"reasoning_effort": effort}


def call_llm(system_prompt, user_text, max_retries=None, timeout=LLM_REQUEST_TIMEOUT, extra_payload=None, caller_label=None):
    """
    调用 LLM 并自动重试（指数退避）。这是全项目最高频使用的 LLM 入口。
    """
    if max_retries is None:
        max_retries = LLM_MAX_RETRIES

    merged_extra = {}
    merged_extra.update(_reasoning_extra_payload())
    if extra_payload:
        merged_extra.update(extra_payload)

    payload = _build_chat_payload(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        max_tokens=4096,
        temperature=0.1,
        extra=merged_extra or None,
    )
    cache_params = {
        "max_tokens": payload.get("max_tokens"),
        "temperature": payload.get("temperature"),
        "stream": payload.get("stream", False),
    }
    if merged_extra:
        cache_params["extra_payload"] = merged_extra

    cache_key = build_cache_key(
        LLM_API_TYPE,
        payload.get("model", LLM_MODEL),
        system_prompt,
        user_text,
        cache_params,
    )
    caller = caller_label or _caller_label()
    cached = get_cached_text(cache_key)
    if cached is not None:
        record_call(cache_key, True, payload.get("model", LLM_MODEL), LLM_API_TYPE, caller, "cache_hit")
        return cached

    for attempt in range(max_retries):
        try:
            result = _post_chat_completion(payload, timeout=timeout)

            if 'choices' in result and len(result['choices']) > 0:
                content = _extract_llm_content(result)
                if content:
                    store_cached_text(cache_key, content, "success", payload.get("model", LLM_MODEL), LLM_API_TYPE)
                    record_call(cache_key, False, payload.get("model", LLM_MODEL), LLM_API_TYPE, caller, "success")
                    return content
                logger.warning("LLM returned empty content: %s", json.dumps(result, ensure_ascii=False)[:1000])
                failure = _format_llm_runtime_failure(_friendly_llm_failure_message("empty response"))
                store_cached_text(
                    cache_key,
                    failure,
                    "failure",
                    payload.get("model", LLM_MODEL),
                    LLM_API_TYPE,
                    ttl_seconds=failure_ttl_seconds(),
                )
                record_call(cache_key, False, payload.get("model", LLM_MODEL), LLM_API_TYPE, caller, "empty")
                return failure
            logger.warning("LLM returned unexpected payload: %s", json.dumps(result, ensure_ascii=False)[:1000])
            failure = _format_llm_runtime_failure("模型服务返回了异常响应格式，本条审查未生成，请稍后重试。")
            store_cached_text(
                cache_key,
                failure,
                "failure",
                payload.get("model", LLM_MODEL),
                LLM_API_TYPE,
                ttl_seconds=failure_ttl_seconds(),
            )
            record_call(cache_key, False, payload.get("model", LLM_MODEL), LLM_API_TYPE, caller, "unexpected")
            return failure

        except Exception as e:
            logger.warning("LLM request failed (%s/%s): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                sleep_time = 2 ** attempt + (0.5 * attempt)
                logger.info("LLM retry backoff %.1fs", sleep_time)
                time.sleep(sleep_time)
            else:
                logger.exception("LLM request exhausted retries")
                failure = _format_llm_runtime_failure(_friendly_llm_failure_message(e))
                store_cached_text(
                    cache_key,
                    failure,
                    "failure",
                    payload.get("model", LLM_MODEL),
                    LLM_API_TYPE,
                    ttl_seconds=failure_ttl_seconds(),
                )
                record_call(cache_key, False, payload.get("model", LLM_MODEL), LLM_API_TYPE, caller, "exception")
                return failure


def predict_wbs_code(text, summary=None):
    """基于 GB50300 骨架，让模型在极短窗口内判定所属的 子分部/分发工程 编码"""
    input_text = summary if summary else text[:300]
    prompt = f"""
    作为万科工程 RAG 路由中枢。请判定以下段落最贴近哪个国标 WBS 编码？
    只能输出对应的数字代码（例如 01-08-01）。如果无法判断，输出"通用"。
    
    高频备选代码词典：
    01-04-01 排桩基坑 / 01-06-01 土方开挖 / 01-08-01 地下卷材防水
    02-01-01 混凝土模板 / 02-01-02 钢筋工程 / 02-01-04 混凝土浇筑 / 02-02-01 砖砌体
    03-01-02 楼地面整体面层 / 03-02-01 一般抹灰 / 03-09-01 玻璃幕墙
    04-03-01 屋面卷材防水 / 04-03-02 屋面涂膜防水
    05-01-01 室内给水管 / 07-04-06 导管敷设
    
    段落：{input_text[:300]}
    """
    res = call_llm(prompt, "提取WBS代码：").strip()
    match = re.search(r'(\d{2}-\d{2}-\d{2})', res)
    if match:
        return match.group(1)
    return "通用"


def analyze_vision_wbs(base64_image_str):
    """
    多模态交叉复核：让大模型看图，推断图中包含哪些 GB50300 建筑分项工程 (WBS)。
    仅仅提供客观识别结论，供专家对比案卷是否图文相符。
    """
    prompt = """
    你是一个建筑工程结构识别辅助AI。
    请观察这张现场施工照片，识别画面中正在进行的 1-3 种最主要的工程活动，并匹配标准的 GB50300 WBS 分部分项名称。
    例如：画面中有脚手架和钢筋，请输出：【02-01-02 钢筋工程】、【钢管架搭设】。
    只需要简练地列出包含的工程分类即可，无需进行质量点评，无需啰嗦多余的话。
    """

    image_hash = hashlib.sha256(str(base64_image_str or "").encode("utf-8")).hexdigest()
    cache_key = build_cache_key(
        LLM_API_TYPE,
        VISION_MODEL,
        "vision_wbs",
        image_hash,
        {"max_tokens": 1024, "temperature": 0.1},
    )
    cached = get_cached_text(cache_key)
    if cached is not None:
        record_call(cache_key, True, VISION_MODEL, LLM_API_TYPE, "vision.analyze_vision_wbs", "cache_hit")
        return cached

    payload = _build_chat_payload(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image_str}"},
                    },
                ],
            }
        ],
        model=VISION_MODEL,
        max_tokens=1024,
        temperature=0.1,
    )

    try:
        result = _post_chat_completion(payload, timeout=LLM_VISION_TIMEOUT)
        if 'choices' in result and len(result['choices']) > 0:
            content = _extract_llm_content(result)
            if content:
                store_cached_text(cache_key, content, "success", VISION_MODEL, LLM_API_TYPE)
                record_call(cache_key, False, VISION_MODEL, LLM_API_TYPE, "vision.analyze_vision_wbs", "success")
                return content
            logger.warning("Vision LLM returned empty content: %s", json.dumps(result, ensure_ascii=False)[:1000])
            failure = f"[视觉链路异常] {_friendly_llm_failure_message('empty response', vision=True)}"
            store_cached_text(cache_key, failure, "failure", VISION_MODEL, LLM_API_TYPE, ttl_seconds=failure_ttl_seconds())
            record_call(cache_key, False, VISION_MODEL, LLM_API_TYPE, "vision.analyze_vision_wbs", "empty")
            return failure
    except Exception as e:
        logger.warning("Vision LLM request failed: %s", e)
        failure = f"[视觉链路异常] {_friendly_llm_failure_message(e, vision=True)}"
        store_cached_text(cache_key, failure, "failure", VISION_MODEL, LLM_API_TYPE, ttl_seconds=failure_ttl_seconds())
        record_call(cache_key, False, VISION_MODEL, LLM_API_TYPE, "vision.analyze_vision_wbs", "exception")
        return failure

    return "未能识别到有效的建筑工程特征。"


def llm_rerank_rules(query, candidate_rules):
    """
    [V7.0] 引入大模型重排 (LLM-as-a-Judge) 防幻觉机制。
    极速判断大批量词频召回上来的规则，是否真的与查询片段存在强相关逻辑。
    """
    if not candidate_rules:
        return []

    system_prompt = """
    你是一个极其严格的检索相关性法官。
    目标：判断用户提供的【工程规范】是否与【工程方案片段】实质性相关。
    如果【工程方案片段】没有提及某些设施（例如电梯、外墙、基坑等），但【工程规范】强行要求了这些，这属于"不相关"的瞎联想（也就是幻觉）。
    
    只能输出一段包含相关性高得分条目的 JSON 数组。不要解释。
    输出格式示例：[0, 2] (代表第0条和第2条相关)
    如果全都不相关，输出：[]
    """

    rules_text = ""
    for i, rule in enumerate(candidate_rules):
        rules_text += f"[{i}] {rule}\n\n"

    user_text = f"【工程方案片段】：\n{query[:1000]}\n\n【候选工程规范】：\n{rules_text}\n\n判断相关索引数组："

    try:
        res = call_llm(system_prompt, user_text)
        match = re.search(r'\[(.*?)\]', res)
        if match:
            idx_str = match.group(1)
            if not idx_str.strip():
                return []
            indices = [int(i.strip()) for i in idx_str.split(',') if i.strip().isdigit()]
            return [candidate_rules[i] for i in indices if i < len(candidate_rules)]
    except Exception as e:
        print(f"Reranker exception: {e}")

    # 兜底：如果报错或格式错，原样返回前2条
    return candidate_rules[:2]


def audit_engineering_scheme(scheme_text, retrieved_rules, project_name="未知工程"):
    """
    Ask LLM to audit the engineering scheme against the retrieved RAG rules with strict relevance.
    """
    if not scheme_text:
        return "未发现有效的方案文本。"

    system_prompt = f"""
    你是一个严谨客观的万科工程审批专家。当前正在审核的项目名称是：【{project_name}】。
    
    请根据以下【审核标准库（带有优先级 Level）】对用户提交的【施工方案】进行合规审查。
    
    【备选审核标准库】：
    {retrieved_rules}
    
    🚨 【核心审查守则 - 必读】 🚨
    1. 你必须先判断【备选审核标准库】中的每一条规则是否与当前的工程范围**存在实际关联**！
    2. 比如当前是"外墙漏水"工程，如果库里刚好检索回了"监控探头/高空抛物/电梯"等毫不相干的规范，你**必须完全无视它们**，千万不要以"未提及摄像头参数"等荒谬的理由判处违规或提出优化建议！
    3. 严禁没病找病！如果方案内容本就不涉及某项规范，不要生搬硬套。
    
    规则优先级说明：
    Level 1（国家规范）：绝对不可违背。
    Level 2（企业标准）：严格管控。
    Level 3（招投合同）：项目特定要求。
    Level 4（历史参考）：建议性。

    请输出如下排版格式，保持回答克制且切中要害：
    ## 🔴 严重违规 (违反与本项目相关的 Level 1/2 规范)
    - 违规项：... 
    (如果没有真实违规，请写：暂无强制性违规。)

    ## 🟡 优化建议 (违反与本项目相关的 Level 3/4 规范，或严重工艺缺陷)
    - 提示项：...
    (如果没有建议，请写：暂无关键优化建议。)

    ## ✅ 审核总结
    (1句话给出总评)
    """

    # 截断超长文本以防 Token 限制
    truncated_text = scheme_text[:12000]

    return call_llm(system_prompt, f"方案原文：\\n{truncated_text}")
