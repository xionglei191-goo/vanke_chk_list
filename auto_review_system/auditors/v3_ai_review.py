"""V3 AI-first reviewer for small repair / renovation schemes."""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

from auditors.engineering_auditor import call_llm
from auditors.v3_prompts import (
    V3_CRITIC_SYSTEM_PROMPT,
    V3_FINAL_SYSTEM_PROMPT,
    V3_INITIAL_SYSTEM_PROMPT,
    build_critic_user_prompt,
    build_final_user_prompt,
    build_initial_user_prompt,
)
from llm.cache import call_stats_since, current_timestamp
from utils.paths import APP_DIR, PROJECT_DIR

CORE_DIMENSIONS = {"描述完整性", "工艺合理性", "分项拆分", "逻辑自洽"}
DEFAULT_PLAYBOOK = os.path.join(APP_DIR, "data", "analysis", "repair_review_playbook.md")
DEFAULT_DEEP_REPORT = os.path.join(APP_DIR, "data", "analysis", "deep_alignment_benchmark_report.md")
DEFAULT_UNRESOLVED = os.path.join(APP_DIR, "data", "analysis", "unresolved_review_sources.md")

STATIC_STANDARD_HINTS = [
    "建筑装饰装修工程质量验收应关注材料合格证明、基层处理、粘结质量、空鼓、平整度、接缝和观感质量。",
    "混凝土结构、植筋、后加构件应明确锚固深度、孔径、植筋胶、拉拔/隐蔽验收和结构专业复核。",
    "防水及渗漏维修应明确基层状态、渗漏原因、节点收口、闭水/淋水试验、保护层和相邻饰面相容性。",
    "玻璃、防火门等安全敏感材料应核查规格厚度、认证标识、检测报告、安装节点和现场可执行复核方法。",
    "地坪、EPDM、环氧、自流平等面层应核查基层验收、材料配比、厚度、固化养护、开放使用和成品保护。",
    "给排水管网、管井、水沟应核查管径坡度、井型构造、流槽/沉泥、功能测试、回填和路面恢复。",
]


def _safe_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _resolve_path(path_text: str) -> str:
    raw = str(path_text or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw
    for base in (PROJECT_DIR, APP_DIR):
        candidate = os.path.join(base, raw)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(PROJECT_DIR, raw)


def load_review_playbook(max_chars: int = 18000) -> str:
    path = _resolve_path(os.getenv("V3_PLAYBOOK_PATH", DEFAULT_PLAYBOOK))
    if os.path.exists(path):
        text = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            return text[:max_chars]
    if os.path.exists(DEFAULT_DEEP_REPORT):
        text = Path(DEFAULT_DEEP_REPORT).read_text(encoding="utf-8", errors="ignore")
        lines = [
            "# 零星工程审核经验教训手册（自动兜底版）",
            "",
            "## 核心原则",
            "- 方案审核不是大而全合规审查，而是判断班组方案能否指导施工、计价、验收和复核。",
            "- 历史意见只提供专家追问方式，不能机械复述到当前方案。",
            "- 所有结论必须回到当前方案证据；无证据只能列为需复核/需补资料。",
            "",
            "## 从历史深度对照提炼的追问",
        ]
        picked = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- 专家真实意图：") or stripped.startswith("- 泛化规则：") or stripped.startswith("- 忽略风险："):
                if stripped not in picked:
                    picked.append(stripped)
            if len(picked) >= 80:
                break
        lines.extend(picked)
        return "\n".join(lines)[:max_chars]
    return "\n".join([
        "# 零星工程审核经验教训手册（最小兜底版）",
        "- 审核目标：判断方案能否指导施工、计价、验收和复核。",
        "- 必须检查工程描述完整性、工艺合理性、分项拆分合理性、逻辑自洽。",
        "- 历史经验只能启发追问，不能替代当前方案证据。",
    ])


def _compact(text: object, limit: int = 4000) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit]


def _scheme_text(chunks_ready_for_agents) -> str:
    parts = []
    for idx, chunk in enumerate(chunks_ready_for_agents or [], 1):
        if not isinstance(chunk, dict):
            continue
        heading = _compact(chunk.get("heading") or f"资料片段{idx}", 200)
        text = str(chunk.get("text") or "").strip()
        if text:
            parts.append(f"## {heading}\n{text}")
    return "\n\n".join(parts).strip()


def _json_from_text(raw_text: str):
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except Exception:
            pass
    start_positions = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
    if not start_positions:
        return None
    start = min(start_positions)
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def _normalize_issue(item: dict, origin: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    dimension = _compact(item.get("dimension"), 40)
    if dimension not in CORE_DIMENSIONS:
        dimension = "描述完整性"
    evidence = item.get("scheme_evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence] if evidence.strip() else []
    evidence = [_compact(x, 800) for x in evidence if str(x or "").strip()][:5]
    needs_followup = bool(item.get("needs_followup"))
    if not evidence:
        needs_followup = True
    finding = _compact(item.get("finding"), 500) or "需复核/需补资料：AI 未返回明确问题"
    if needs_followup and not finding.startswith("需复核") and not finding.startswith("需补"):
        finding = f"需复核/需补资料：{finding}"
    evidence_type = _compact(item.get("evidence_type"), 40) or ("需补充资料" if needs_followup else "方案内部逻辑")
    if needs_followup and evidence_type not in {"需补充资料", "专家经验", "方案内部逻辑", "规范"}:
        evidence_type = "需补充资料"
    confidence = _compact(item.get("confidence"), 10)
    if confidence not in {"高", "中", "低"}:
        confidence = "低" if needs_followup else "中"
    return {
        "work_item": _compact(item.get("work_item"), 80) or "整体复核",
        "dimension": dimension,
        "finding": finding,
        "scheme_evidence": evidence,
        "reason": _compact(item.get("reason"), 1200),
        "evidence_type": evidence_type,
        "evidence_ref": _compact(item.get("evidence_ref"), 500) or ("当前方案证据不足，需补充资料" if needs_followup else "当前方案内部逻辑"),
        "recommendation": _compact(item.get("recommendation"), 1200) or "请结合当前方案补充可执行做法、参数、工序和验收标准。",
        "confidence": confidence,
        "needs_followup": needs_followup,
        "origin": origin,
    }


def _normalize_issues(data, origin: str) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("issues", [])
    if not isinstance(data, list):
        return []
    issues = []
    for item in data:
        normalized = _normalize_issue(item, origin)
        if normalized:
            issues.append(normalized)
    return issues


def _terms(query: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", str(query or ""))
    stop = {"方案", "施工", "审核", "问题", "明确", "检查", "需要", "是否", "当前", "材料", "工程"}
    terms = []
    for term in raw_terms:
        if term in stop or len(term) > 24:
            continue
        if term not in terms:
            terms.append(term)
    return terms[:12]


def _split_snippet_units(text: str) -> list[str]:
    units = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) <= 900:
            units.append(stripped)
            continue
        parts = re.split(r"(?<=[。；;])", stripped)
        units.extend(part.strip() for part in parts if part.strip())
    return units


def _search_text(text: str, query: str, limit: int = 3, result_chars: int = 3000) -> list[str]:
    terms = _terms(query)
    units = _split_snippet_units(text)
    scored = []
    for idx, unit in enumerate(units):
        score = sum(unit.lower().count(term.lower()) for term in terms)
        if score:
            scored.append((score, idx, unit))
    if not scored and units:
        scored = [(0, idx, unit) for idx, unit in enumerate(units[:limit])]
    scored.sort(key=lambda item: (-item[0], item[1]))
    snippets = []
    for _, _, unit in scored[:limit]:
        snippets.append(unit[:result_chars])
    return snippets


def _standard_search(query: str, result_chars: int) -> list[str]:
    vector_db = os.path.join(APP_DIR, "vector_db_storage", "chroma.sqlite3")
    if os.path.exists(vector_db) and os.path.getsize(vector_db) > 0:
        try:
            from rag_engine.vector_store import retrieve_rules
            result = retrieve_rules(query, n_results=3)
            if result and "未检索到高度匹配" not in result:
                return [_compact(result, result_chars)]
        except Exception:
            pass
    terms = _terms(query)
    scored = []
    for idx, hint in enumerate(STATIC_STANDARD_HINTS):
        score = sum(hint.lower().count(term.lower()) for term in terms)
        scored.append((score, idx, hint))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2][:result_chars] for item in scored[:3]]


def _unresolved_search(query: str, result_chars: int) -> list[str]:
    if not os.path.exists(DEFAULT_UNRESOLVED):
        return []
    text = Path(DEFAULT_UNRESOLVED).read_text(encoding="utf-8", errors="ignore")
    return _search_text(text, query, limit=3, result_chars=result_chars)


def _initial_tool_queries(initial_payload, initial_issues) -> list[dict]:
    raw = []
    if isinstance(initial_payload, dict) and isinstance(initial_payload.get("tool_queries"), list):
        raw.extend(item for item in initial_payload["tool_queries"] if isinstance(item, dict))
    if raw:
        return raw
    for issue in initial_issues:
        query = f"{issue.get('work_item', '')} {issue.get('finding', '')} {issue.get('recommendation', '')}"
        raw.extend([
            {"tool": "scheme_snippet", "query": query, "reason": "核对当前方案证据"},
            {"tool": "standards_search", "query": query, "reason": "查找可能规范依据"},
        ])
    return raw


def run_v3_tools(tool_queries, scheme_text, cost_context) -> list[dict]:
    limit = _safe_int_env("V3_TOOL_QUERY_LIMIT", 8)
    result_chars = _safe_int_env("V3_TOOL_RESULT_CHARS", 3000)
    allowed = {"scheme_snippet", "cost_snippet", "成本_snippet", "standards_search", "unresolved_search"}
    results = []
    for item in (tool_queries or [])[:limit]:
        tool = str(item.get("tool") or "").strip()
        query = _compact(item.get("query"), 300)
        if tool not in allowed or not query:
            continue
        if tool == "scheme_snippet":
            output = _search_text(scheme_text, query, result_chars=result_chars)
        elif tool in {"cost_snippet", "成本_snippet"}:
            output = _search_text(cost_context, query, result_chars=result_chars) if cost_context else []
        elif tool == "standards_search":
            output = _standard_search(query, result_chars)
        else:
            output = _unresolved_search(query, result_chars)
        results.append({
            "tool": "cost_snippet" if tool == "成本_snippet" else tool,
            "query": query,
            "reason": _compact(item.get("reason"), 300),
            "results": output,
        })
    return results


def _issue_result(issue: dict) -> str:
    evidence_lines = issue.get("scheme_evidence") or []
    evidence_text = "\n".join(f"- {line}" for line in evidence_lines) if evidence_lines else "- 未提供直接证据，需人工复核/补资料"
    follow = "是" if issue.get("needs_followup") else "否"
    return (
        f"**问题**：{issue.get('finding')}\n"
        f"**方案证据**：\n{evidence_text}\n"
        f"**背景/原因**：{issue.get('reason')}\n"
        f"**依据类型**：{issue.get('evidence_type')}\n"
        f"**依据出处**：{issue.get('evidence_ref')}\n"
        f"**修改建议**：{issue.get('recommendation')}\n"
        f"**需补资料/复核**：{follow}\n"
        f"**置信度**：{issue.get('confidence')}"
    )


def _group_reports(issues: list[dict]) -> dict:
    grouped = defaultdict(list)
    if not issues:
        issues = [{
            "work_item": "整体复核",
            "dimension": "描述完整性",
            "finding": "需复核/需补资料：AI 未发现明确问题或输出为空",
            "scheme_evidence": [],
            "reason": "本次审核没有形成可直接采纳的机器结论，请人工复核方案关键参数、工序、分项和逻辑。",
            "evidence_type": "需补充资料",
            "evidence_ref": "v3 AI 主审空结果兜底",
            "recommendation": "请人工复核后补充审核意见。",
            "confidence": "低",
            "needs_followup": True,
            "origin": "fallback",
        }]
    for issue in issues:
        work_item = issue.get("work_item") or "整体复核"
        grouped[work_item].append({
            "agent": f"{issue.get('dimension', '描述完整性')}审核",
            "heading": work_item,
            "work_item": work_item,
            "dimension": issue.get("dimension"),
            "finding": issue.get("finding"),
            "scheme_evidence": issue.get("scheme_evidence", []),
            "reason": issue.get("reason"),
            "evidence_type": issue.get("evidence_type"),
            "evidence_ref": issue.get("evidence_ref"),
            "recommendation": issue.get("recommendation"),
            "confidence": issue.get("confidence"),
            "needs_followup": issue.get("needs_followup", False),
            "origin": issue.get("origin", "v3"),
            "result": _issue_result(issue),
        })
    return dict(grouped)


def _runtime_report(runtime: dict) -> dict:
    result = "\n".join([
        f"**审核引擎**：v3_ai_review",
        f"**调用预算**：{runtime.get('budget', 0)}",
        f"**实际非缓存LLM调用数**：{runtime.get('llm_real_calls', 0)}",
        f"**缓存命中数**：{runtime.get('llm_cache_hits', 0)}",
        f"**工具查询数**：{runtime.get('tool_query_count', 0)}",
        f"**Thinking启用**：{'是' if runtime.get('thinking_enabled') else '否'}",
        f"**执行阶段**：{' -> '.join(runtime.get('stages', []))}",
        f"**LLM状态统计**：{json.dumps(runtime.get('llm_status', {}), ensure_ascii=False)}",
    ])
    return {
        "agent": "审核运行信息",
        "heading": "审核运行信息",
        "work_item": "审核运行信息",
        "dimension": "描述完整性",
        "result": result,
        "origin": "runtime_info",
    }


def _failure_reports(project_name: str, reason: str, runtime: dict) -> dict:
    issue = {
        "work_item": "整体复核",
        "dimension": "描述完整性",
        "finding": f"需复核/需补资料：{reason}",
        "scheme_evidence": [],
        "reason": "v3 AI 主审未能生成可解析的结构化结论。为避免任务崩溃，系统保留本卷宗供人工复审。",
        "evidence_type": "需补充资料",
        "evidence_ref": "v3 AI 主审异常兜底",
        "recommendation": f"请人工复核项目《{project_name}》，必要时重新发起审核。",
        "confidence": "低",
        "needs_followup": True,
        "origin": "v3_failure",
    }
    grouped = _group_reports([issue])
    grouped["审核运行信息"] = [_runtime_report(runtime)]
    return grouped


def run_v3_pipeline(
    chunks_ready_for_agents,
    project_name,
    global_cost_context="",
    progress_callback=None,
    status_check_callback=None,
):
    budget = max(1, _safe_int_env("V3_AI_CALL_BUDGET", 3))
    runtime = {
        "budget": budget,
        "stages": [],
        "tool_query_count": 0,
        "thinking_enabled": os.getenv("LLM_THINKING_ENABLED", "true").strip().lower() in {"1", "true", "yes"},
    }
    stats_start = current_timestamp()
    scheme_text = _scheme_text(chunks_ready_for_agents)
    playbook = load_review_playbook()
    timeout = _safe_int_env("V3_REVIEW_TIMEOUT", 240)
    max_tokens = _safe_int_env("V3_REVIEW_MAX_TOKENS", 4096)

    if progress_callback:
        progress_callback("🧠 v3 AI主审：完整阅读方案并生成初审问题。", 0.25)
    runtime["stages"].append("initial_review")
    initial_raw = call_llm(
        V3_INITIAL_SYSTEM_PROMPT,
        build_initial_user_prompt(project_name, scheme_text, global_cost_context, playbook),
        max_retries=1,
        timeout=timeout,
        extra_payload={"max_tokens": max_tokens},
        caller_label="v3.initial_review",
    )
    initial_payload = _json_from_text(initial_raw)
    if initial_payload is None:
        runtime["stages"].append("initial_parse_failed")
        llm_stats = call_stats_since(stats_start, caller_prefix="v3.")
        runtime["llm_real_calls"] = llm_stats.get("real_calls", 0)
        runtime["llm_cache_hits"] = llm_stats.get("cache_hits", 0)
        runtime["llm_status"] = llm_stats.get("by_status", {})
        return _failure_reports(project_name, "初审 JSON 解析失败", runtime)
    initial_issues = _normalize_issues(initial_payload, "v3_initial")

    if progress_callback:
        progress_callback("🔎 v3 AI主审：执行本地工具复核。", 0.5)
    tool_queries = _initial_tool_queries(initial_payload, initial_issues)
    tool_results = run_v3_tools(tool_queries, scheme_text, global_cost_context)
    runtime["tool_query_count"] = len(tool_results)
    runtime["stages"].append("local_tools")

    issues = initial_issues
    allow_critic = budget >= 3
    if budget >= 2:
        if progress_callback:
            progress_callback("🧾 v3 AI主审：结合工具证据生成终审意见。", 0.7)
        runtime["stages"].append("final_review")
        final_raw = call_llm(
            V3_FINAL_SYSTEM_PROMPT,
            build_final_user_prompt(project_name, scheme_text, global_cost_context, playbook, initial_issues, tool_results),
            max_retries=1,
            timeout=timeout,
            extra_payload={"max_tokens": max_tokens},
            caller_label="v3.final_review",
        )
        final_payload = _json_from_text(final_raw)
        if final_payload is None:
            if initial_issues:
                runtime["stages"].append("final_parse_failed_keep_initial")
                allow_critic = False
            else:
                runtime["stages"].append("final_parse_failed")
                llm_stats = call_stats_since(stats_start, caller_prefix="v3.")
                runtime["llm_real_calls"] = llm_stats.get("real_calls", 0)
                runtime["llm_cache_hits"] = llm_stats.get("cache_hits", 0)
                runtime["llm_status"] = llm_stats.get("by_status", {})
                return _failure_reports(project_name, "终审 JSON 解析失败", runtime)
        else:
            issues = _normalize_issues(final_payload, "v3_final")

    if allow_critic:
        if progress_callback:
            progress_callback("🧪 v3 AI主审：质量复核，删除偏题和无证据结论。", 0.86)
        runtime["stages"].append("critic")
        critic_raw = call_llm(
            V3_CRITIC_SYSTEM_PROMPT,
            build_critic_user_prompt(project_name, scheme_text, issues, tool_results),
            max_retries=1,
            timeout=timeout,
            extra_payload={"max_tokens": max_tokens},
            caller_label="v3.critic",
        )
        critic_payload = _json_from_text(critic_raw)
        if critic_payload is None:
            runtime["stages"].append("critic_parse_failed_keep_final")
        else:
            issues = _normalize_issues(critic_payload, "v3_critic")

    grouped = _group_reports(issues)
    llm_stats = call_stats_since(stats_start, caller_prefix="v3.")
    runtime["llm_real_calls"] = llm_stats.get("real_calls", 0)
    runtime["llm_cache_hits"] = llm_stats.get("cache_hits", 0)
    runtime["llm_status"] = llm_stats.get("by_status", {})
    grouped["审核运行信息"] = [_runtime_report(runtime)]
    if progress_callback:
        progress_callback(f"✅ v3 AI主审：生成 {sum(len(v) for k, v in grouped.items() if k != '审核运行信息')} 条意见。", 0.95)
    return grouped
