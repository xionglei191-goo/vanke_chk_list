"""
Prompts for the v2 repair-scheme reviewer.

The prompt is intentionally built around the human review methodology learned
from historical opinions: classify, attribute, query evidence, then generalize.
"""
import json


REPAIR_REVIEW_SYSTEM_PROMPT = """
你是万科零星工程/改造维修方案审核专家。你的任务不是做大而全施工组织设计审查，而是判断班组长写的方案能否指导施工、计价、验收和复核。

你必须在内部使用 reasoning/thinking 完成以下步骤，但最终不要输出思维链：
1. 拆分分项工程 WorkItem。
2. 对每个 WorkItem 按四个维度检查：描述完整性、工艺合理性、分项拆分、逻辑自洽。
3. 主动使用 TOOL_CONTEXT 中的历史经验、规范检索片段和本地规则命中，判断问题是否真的适用于当前方案。
4. 对历史经验只能举一反三，不能照搬不相关项目；只有当前方案出现相似材料、部位、工序或计价口径时才能输出。
5. 输出必须能让班组长直接修改方案：说明问题、背景原因、依据类型、依据出处、修改建议和置信度。

禁止事项：
- 不要输出泛泛安全文明费、品牌违约、超高降效、合同处罚等偏题结论，除非当前材料明确要求。
- 不要因为保修模板文字误判为防水施工。
- 不要只说“需完善”，必须写清楚补什么参数、改什么工序、如何验收。
- 不要输出内部思维链、分析草稿或 Markdown 解释。

只输出 JSON 数组，每个元素字段固定为：
dimension, work_item, finding, reason, evidence_type, evidence_ref, recommendation, confidence
confidence 只能是 高/中/低。
evidence_type 只能是 规范/专家经验/方案内部逻辑。
如果没有新增高价值问题，输出 []。
"""


def build_repair_review_user_prompt(project_name, sections, local_issues, experience_cards, tool_context):
    payload = {
        "project_name": project_name,
        "scheme_sections": [
            {
                "section_type": section.get("section_type", ""),
                "heading": section.get("heading", ""),
                "text": section.get("text", "")[:2500],
            }
            for section in sections
        ],
        "local_rule_findings": [
            {
                "dimension": item.get("dimension"),
                "work_item": item.get("work_item"),
                "finding": item.get("finding"),
                "reason": item.get("reason"),
                "recommendation": item.get("recommendation"),
            }
            for item in local_issues[:20]
        ],
        "matched_experience_cards": [
            {
                "work_category": card.get("work_category"),
                "dimension": card.get("dimension"),
                "source_opinion": card.get("source_opinion"),
                "problem_pattern_label": card.get("problem_pattern_label"),
                "professional_attribution_label": card.get("professional_attribution_label"),
                "engineer_question": card.get("engineer_question"),
                "review_intents": card.get("review_intents"),
                "root_cause": card.get("root_cause"),
                "generalization_rule": card.get("generalization_rule"),
                "review_questions": card.get("review_questions"),
                "required_artifacts": card.get("required_artifacts"),
                "fix_template": card.get("fix_template"),
                "evidence_ref": card.get("evidence_ref"),
                "scheme_evidence": card.get("scheme_evidence", [])[:2],
            }
            for card in experience_cards[:12]
        ],
        "tool_context": tool_context,
    }
    return (
        "请基于以下结构化材料进行一次零星工程审核。先在内部完成分类、归因、证据核验和泛化判断；"
        "最终只输出 JSON 数组。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
