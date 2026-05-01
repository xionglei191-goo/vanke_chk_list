import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "auto_review_system"))

from auditors import v3_ai_review
from auditors.v3_ai_review import (
    _normalize_issue,
    load_review_playbook,
    run_v3_pipeline,
    run_v3_tools,
)
from llm.cache import build_cache_key, get_cached_text, store_cached_text
from llm.client import _to_anthropic_payload
from rag_engine.review_workflow import (
    approved_lessons_markdown,
    build_review_markdown,
    delete_review_state,
    list_corrections,
    load_review_state,
    review_correction,
    save_correction,
    save_issue_review,
)
from utils.exporter import markdown_to_docx
from build_repair_playbook import build_playbook


def run_tests():
    print("🚀 开始 v3 AI 主审核心测试...\n")

    print("🟢 测试 1：经验手册加载")
    playbook = load_review_playbook(max_chars=12000)
    assert "零星工程" in playbook
    assert "历史经验" in playbook
    assert len(playbook) <= 12000
    print("✅ 经验手册加载测试通过！\n")

    print("🟢 测试 2：无方案证据问题强制转需复核")
    issue = _normalize_issue({
        "work_item": "EPDM塑胶地面",
        "dimension": "描述完整性",
        "finding": "胶水配比未明确",
        "scheme_evidence": [],
        "reason": "缺少参数",
        "evidence_type": "专家经验",
        "recommendation": "补充配比",
        "confidence": "高",
        "needs_followup": False,
    }, "unit")
    assert issue["needs_followup"] is True
    assert issue["finding"].startswith("需复核/需补资料")
    print("✅ 证据约束测试通过！\n")

    print("🟢 测试 3：本地工具限流与截断")
    old_limit = os.environ.get("V3_TOOL_QUERY_LIMIT")
    old_chars = os.environ.get("V3_TOOL_RESULT_CHARS")
    os.environ["V3_TOOL_QUERY_LIMIT"] = "1"
    os.environ["V3_TOOL_RESULT_CHARS"] = "60"
    try:
        tool_results = run_v3_tools(
            [
                {"tool": "scheme_snippet", "query": "EPDM 胶水 固化", "reason": "查方案"},
                {"tool": "scheme_snippet", "query": "水沟", "reason": "应被限流"},
            ],
            "施工工序 | EPDM铺设，配料准备，固化养护，但未写固化时间和胶水配比。\n水沟维修在EPDM后面。",
            "",
        )
        assert len(tool_results) == 1
        assert tool_results[0]["tool"] == "scheme_snippet"
        assert all(len(item) <= 60 for item in tool_results[0]["results"])
    finally:
        if old_limit is None:
            os.environ.pop("V3_TOOL_QUERY_LIMIT", None)
        else:
            os.environ["V3_TOOL_QUERY_LIMIT"] = old_limit
        if old_chars is None:
            os.environ.pop("V3_TOOL_RESULT_CHARS", None)
        else:
            os.environ["V3_TOOL_RESULT_CHARS"] = old_chars
    print("✅ 本地工具测试通过！\n")

    print("🟢 测试 4：v3 三阶段 mock LLM 预算")
    original_call_llm = v3_ai_review.call_llm
    saved_env = {name: os.environ.get(name) for name in ("V3_AI_CALL_BUDGET", "V3_TOOL_QUERY_LIMIT", "LLM_THINKING_ENABLED")}
    calls = []

    def fake_call_llm(system_prompt, user_text, max_retries=None, timeout=90, extra_payload=None, caller_label=None):
        calls.append(caller_label)
        if caller_label == "v3.initial_review":
            return """
            {
              "issues": [
                {
                  "work_item": "EPDM塑胶地面",
                  "dimension": "描述完整性",
                  "finding": "胶水配比和固化时间未明确",
                  "scheme_evidence": ["施工工序仅写配料准备、固化养护"],
                  "reason": "参数缺失会影响粘结和开放使用",
                  "evidence_type": "专家经验",
                  "evidence_ref": "经验手册",
                  "recommendation": "补充胶水配比、固化时间和开放条件",
                  "confidence": "高",
                  "needs_followup": false
                }
              ],
              "tool_queries": [
                {"tool": "scheme_snippet", "query": "EPDM 胶水 固化", "reason": "核对方案证据"}
              ]
            }
            """
        if caller_label == "v3.final_review":
            return """
            [
              {
                "work_item": "EPDM塑胶地面",
                "dimension": "描述完整性",
                "finding": "EPDM胶水配比和固化时间未明确",
                "scheme_evidence": ["施工工序 | EPDM铺设：配料准备 -> 固化养护"],
                "reason": "当前方案只有动作，没有具体配比、养护时间和开放条件。",
                "evidence_type": "专家经验",
                "evidence_ref": "经验手册+当前方案",
                "recommendation": "补充胶粘剂型号/配比、固化养护时间和开放使用条件。",
                "confidence": "高",
                "needs_followup": false
              }
            ]
            """
        if caller_label == "v3.critic":
            return """
            [
              {
                "work_item": "EPDM塑胶地面",
                "dimension": "描述完整性",
                "finding": "EPDM胶水配比和固化时间未明确",
                "scheme_evidence": ["施工工序 | EPDM铺设：配料准备 -> 固化养护"],
                "reason": "当前方案只有动作，没有具体配比、养护时间和开放条件。",
                "evidence_type": "专家经验",
                "evidence_ref": "经验手册+当前方案",
                "recommendation": "补充胶粘剂型号/配比、固化养护时间和开放使用条件。",
                "confidence": "高",
                "needs_followup": false
              }
            ]
            """
        return "[]"

    try:
        v3_ai_review.call_llm = fake_call_llm
        os.environ["V3_AI_CALL_BUDGET"] = "3"
        os.environ["V3_TOOL_QUERY_LIMIT"] = "2"
        os.environ["LLM_THINKING_ENABLED"] = "true"
        reports = run_v3_pipeline([
            {"heading": "施工方案", "text": "施工工序 | EPDM铺设：配料准备 -> 固化养护。"}
        ], "v3 mock 项目")
        assert calls == ["v3.initial_review", "v3.final_review", "v3.critic"]
        assert "EPDM塑胶地面" in reports
        assert "审核运行信息" in reports
        assert "调用预算**：3" in reports["审核运行信息"][0]["result"]
        assert "方案证据" in reports["EPDM塑胶地面"][0]["result"]
    finally:
        v3_ai_review.call_llm = original_call_llm
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    print("✅ 三阶段预算测试通过！\n")

    print("🟢 测试 5：AI JSON 解析失败不崩溃")
    def fake_bad_json(system_prompt, user_text, max_retries=None, timeout=90, extra_payload=None, caller_label=None):
        return "不是JSON"

    v3_ai_review.call_llm = fake_bad_json
    try:
        reports = run_v3_pipeline([{"heading": "坏输出", "text": "施工方案"}], "坏输出样例")
        flat = "\n".join(item["result"] for items in reports.values() for item in items)
        assert "初审 JSON 解析失败" in flat
        assert "审核运行信息" in reports
    finally:
        v3_ai_review.call_llm = original_call_llm
    print("✅ 失败兜底测试通过！\n")

    print("🟢 测试 6：终审失败时保留初审问题")
    calls_final_fail = []

    def fake_final_bad_json(system_prompt, user_text, max_retries=None, timeout=90, extra_payload=None, caller_label=None):
        calls_final_fail.append(caller_label)
        if caller_label == "v3.initial_review":
            return """
            {
              "issues": [
                {
                  "work_item": "石凳翻新",
                  "dimension": "工艺合理性",
                  "finding": "石凳地坪漆收口和遮蔽保护未明确",
                  "scheme_evidence": ["工序：石凳表面清理、修补、打磨、第一遍地坪漆、第二遍地坪漆"],
                  "reason": "石材刷涂料需要控制附着力和污染。",
                  "evidence_type": "专家经验",
                  "evidence_ref": "经验手册",
                  "recommendation": "补充界面剂、遮蔽保护和收口验收要求。",
                  "confidence": "中",
                  "needs_followup": false
                }
              ],
              "tool_queries": []
            }
            """
        return "不是JSON"

    v3_ai_review.call_llm = fake_final_bad_json
    saved_budget = os.environ.get("V3_AI_CALL_BUDGET")
    os.environ["V3_AI_CALL_BUDGET"] = "3"
    try:
        reports = run_v3_pipeline([{"heading": "石凳", "text": "工序：石凳表面清理、修补、打磨、第一遍地坪漆、第二遍地坪漆"}], "终审失败样例")
        assert "石凳翻新" in reports
        assert "final_parse_failed_keep_initial" in reports["审核运行信息"][0]["result"]
        assert calls_final_fail == ["v3.initial_review", "v3.final_review"]
    finally:
        v3_ai_review.call_llm = original_call_llm
        if saved_budget is None:
            os.environ.pop("V3_AI_CALL_BUDGET", None)
        else:
            os.environ["V3_AI_CALL_BUDGET"] = saved_budget
    print("✅ 终审失败保留初审测试通过！\n")

    print("🟢 测试 7：LLM 缓存与 Anthropic payload")
    key = build_cache_key("unit", "model", "system", "user", {"a": 1})
    store_cached_text(key, "cached-ok", "success", "model", "unit", ttl_seconds=60)
    assert get_cached_text(key) == "cached-ok"
    payload = _to_anthropic_payload({
        "model": "m",
        "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}],
        "max_tokens": 100,
    })
    assert payload["system"] == "sys"
    assert payload["messages"][0]["role"] == "user"
    print("✅ 缓存与 payload 测试通过！\n")

    print("🟢 测试 8：逐条复审与纠偏审批")
    task_id = "TASK_UNIT_REVIEW"
    delete_review_state(task_id)
    reports = {
        "EPDM面层": [{
            "agent": "描述完整性审核",
            "work_item": "EPDM面层",
            "dimension": "描述完整性",
            "finding": "胶水配比未明确",
            "scheme_evidence": ["方案仅写配料准备"],
            "reason": "参数缺失",
            "evidence_type": "专家经验",
            "evidence_ref": "经验手册",
            "recommendation": "补充配比",
            "confidence": "高",
            "needs_followup": False,
            "result": "**问题**：胶水配比未明确\n**修改建议**：补充配比",
        }],
        "审核运行信息": [{"origin": "runtime_info", "result": "runtime"}],
    }
    try:
        issues, runtime = load_review_state(task_id, "单元测试项目", reports)
        assert len(issues) == 1
        assert len(runtime) == 1
        issue_id = issues[0]["issue_id"]
        save_issue_review(task_id, issue_id, "accepted", issues[0]["original_result"], reviewer="reviewer_a")
        issues, _ = load_review_state(task_id, "单元测试项目", reports)
        assert issues[0]["status"] == "accepted"
        export_md = build_review_markdown("单元测试项目", issues)
        assert "EPDM面层" in export_md
        assert "胶水配比未明确" in export_md

        corr_id = save_correction(
            task_id=task_id,
            issue_id=issue_id,
            project_name="单元测试项目",
            work_item="EPDM面层",
            dimension="描述完整性",
            ai_finding="胶水配比未明确",
            scheme_evidence=["方案仅写配料准备"],
            human_comment="AI判断正确，但应补充胶水类型和开放条件。",
            correction_type="经验补充",
            lesson_summary="EPDM只写配料准备时，应追问胶水类型、配比和开放条件。",
            status="submitted",
            submitted_by="reviewer_a",
        )
        assert any(item["correction_id"] == corr_id for item in list_corrections(task_id=task_id, issue_id=issue_id))
        review_correction(corr_id, "approved", reviewer="reviewer_b", review_comment="可沉淀")
        lessons = approved_lessons_markdown(limit=50)
        assert "EPDM只写配料准备" in lessons
        playbook = build_playbook("# 测试\n", approved_feedback=lessons)
        assert "已审批纠偏经验" in playbook
    finally:
        delete_review_state(task_id)
    print("✅ 逐条复审与纠偏审批测试通过！\n")

    print("🟢 测试 9：人工复审导出 Word")
    buff = markdown_to_docx("# 标题\n\n## 分项\n\n**问题**：测试", "v3测试")
    assert buff.getbuffer().nbytes > 0
    print("✅ Word 导出测试通过！\n")


if __name__ == "__main__":
    run_tests()
