"""
Prompts for the v2 repair-scheme reviewer.

The prompt is intentionally built around the human review methodology learned
from historical opinions: classify, attribute, query evidence, then generalize.
"""
import json


REPAIR_TOOL_PLAN_SYSTEM_PROMPT = """
你是万科零星工程方案审核的工具规划员。你不直接输出审核意见，只决定需要查询哪些证据。

你必须在内部使用 reasoning/thinking 判断：
1. 当前方案有哪些关键分项工程。
2. 哪些缺陷需要规范、历史经验、方案原文或报价/白单证据支撑。
3. 哪些查询最能提升最终审核质量。

只输出 JSON 数组，每个元素字段固定为：
tool, query, reason

tool 只能取以下值：
- standards_search：查询规范/知识库。
- experience_search：查询历史审核经验卡。
- scheme_snippet：摘取当前方案原文证据。
- cost_snippet：摘取报价/白单/清单证据。

要求：
- 不超过给定工具预算。
- query 必须简短具体，包含分项、材料、工艺或验收关键词。
- 不要输出审核结论、Markdown、解释文字或思维链。
"""


REPAIR_REVIEW_SYSTEM_PROMPT = """
你是万科零星工程/改造维修方案审核专家。你的任务不是做大而全施工组织设计审查，而是判断班组长写的方案能否指导施工、计价、验收和复核。

你必须在内部使用 reasoning/thinking 完成以下步骤，但最终不要输出思维链：
1. 拆分分项工程 WorkItem。
2. 对每个 WorkItem 按四个维度检查：描述完整性、工艺合理性、分项拆分、逻辑自洽。
3. 主动使用 TOOL_CONTEXT 中的历史经验、规范检索片段和本地规则命中，判断问题是否真的适用于当前方案。
4. 对每条历史经验必须先形成“原方案证据 -> 专家追问 -> 具体覆盖/笼统提及/缺失控制点 -> 当前方案是否同类适用”的判断。
5. 对历史经验只能举一反三，不能照搬不相关项目；只有当前方案出现相似材料、部位、工序或计价口径，并且当前方案仍存在对应缺口时才能输出。
6. 如果 matched_experience_cards 中 alignment_status 为“已补齐”，只能把它当作同类检查方法，不得直接复述成当前缺陷。
7. 输出必须能让班组长直接修改方案：说明问题、背景原因、依据类型、依据出处、修改建议和置信度。

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


REPAIR_CRITIC_SYSTEM_PROMPT = """
你是万科零星工程审核结论适用性复核专家。你的任务是复核全部候选问题是否真的适用于当前方案，不按来源盲目保护。

请在内部使用 reasoning/thinking 检查每条候选：
1. 是否有当前方案、工具证据、规范候选或历史经验支撑。
2. 是否机械照搬历史意见，或只是因为出现了泛词就误迁移。
3. 是否偏题到安全文明费、品牌违约、合同处罚、超高降效等泛化话术。
4. 是否能让班组长直接修改方案。
5. 对带控制点判断的候选要谨慎，除非当前方案明显不是同类分项或证据不足，才删除。

只输出 JSON 数组，每个元素字段固定为：
candidate_index, action, reason

action 只能是 keep / drop / revise。
如果 action=revise，可以追加以下修订字段：
dimension, work_item, finding, reason_detail, evidence_type, evidence_ref, recommendation, confidence

每个 candidate_index 最多输出一次。没有输出的候选会按 keep 处理。
不要输出 Markdown、解释文字或思维链。
"""


def _sections_payload(sections):
    return [
        {
            "section_type": section.get("section_type", ""),
            "heading": section.get("heading", ""),
            "text": section.get("text", "")[:2500],
        }
        for section in sections
    ]


def _local_issues_payload(local_issues, limit=20):
    return [
        {
            "dimension": item.get("dimension"),
            "work_item": item.get("work_item"),
            "finding": item.get("finding"),
            "reason": item.get("reason"),
            "recommendation": item.get("recommendation"),
        }
        for item in local_issues[:limit]
    ]


def _experience_cards_payload(experience_cards, limit=12):
    return [
        {
            "work_category": card.get("work_category"),
            "dimension": card.get("dimension"),
            "source_opinion": card.get("source_opinion"),
            "problem_pattern_label": card.get("problem_pattern_label"),
            "professional_attribution_label": card.get("professional_attribution_label"),
            "engineer_question": card.get("engineer_question"),
            "expert_intent": card.get("expert_intent"),
            "alignment_status": card.get("alignment_status"),
            "covered_points": card.get("covered_points"),
            "partial_points": card.get("partial_points"),
            "missing_points": card.get("missing_points"),
            "checkpoint_assessments": card.get("checkpoint_assessments"),
            "scheme_gap": card.get("scheme_gap"),
            "review_intents": card.get("review_intents"),
            "root_cause": card.get("root_cause"),
            "generalization_rule": card.get("generalization_rule"),
            "review_questions": card.get("review_questions"),
            "required_artifacts": card.get("required_artifacts"),
            "fix_template": card.get("fix_template"),
            "evidence_ref": card.get("evidence_ref"),
            "scheme_evidence": card.get("scheme_evidence", [])[:2],
            "evidence_chain": card.get("evidence_chain", {}),
        }
        for card in experience_cards[:limit]
    ]


def build_repair_tool_plan_user_prompt(project_name, sections, local_issues, experience_cards, tool_budget, cost_context_available):
    payload = {
        "project_name": project_name,
        "tool_budget": tool_budget,
        "cost_context_available": cost_context_available,
        "scheme_sections": _sections_payload(sections),
        "local_rule_findings": _local_issues_payload(local_issues, limit=12),
        "matched_experience_cards": _experience_cards_payload(experience_cards, limit=10),
    }
    return (
        "请为本次零星工程审核生成工具查询计划。只输出 JSON 数组。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def build_repair_review_user_prompt(project_name, sections, local_issues, experience_cards, tool_context, tool_plan=None, runtime_context=None):
    payload = {
        "project_name": project_name,
        "runtime_context": runtime_context or {},
        "scheme_sections": _sections_payload(sections),
        "local_rule_findings": _local_issues_payload(local_issues),
        "matched_experience_cards": _experience_cards_payload(experience_cards),
        "tool_plan": tool_plan or [],
        "tool_context": tool_context,
    }
    return (
        "请基于以下结构化材料进行一次零星工程审核。先在内部完成分类、归因、证据核验和泛化判断；"
        "最终只输出 JSON 数组。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def build_repair_critic_user_prompt(project_name, sections, candidate_issues, tool_context):
    payload = {
        "project_name": project_name,
        "scheme_sections": _sections_payload(sections),
        "candidate_findings": [
            {
                "candidate_index": idx,
                "origin": item.get("origin") or "local_or_experience",
                "dimension": item.get("dimension"),
                "work_item": item.get("work_item"),
                "finding": item.get("finding"),
                "reason": item.get("reason"),
                "evidence_type": item.get("evidence_type"),
                "evidence_ref": item.get("evidence_ref"),
                "recommendation": item.get("recommendation"),
                "confidence": item.get("confidence"),
                "alignment_status": item.get("alignment_status"),
                "partial_points": item.get("partial_points"),
                "missing_points": item.get("missing_points"),
                "checkpoint_assessments": item.get("checkpoint_assessments"),
            }
            for idx, item in enumerate(candidate_issues[:30])
        ],
        "tool_context": tool_context,
    }
    return (
        "请复核候选审核问题，删除偏题、无证据或照搬历史意见的问题，必要时修订措辞。"
        "只输出 JSON 数组。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
