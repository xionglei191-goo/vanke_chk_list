"""
V2 repair-scheme audit engine.

This engine is designed for small repair / renovation projects submitted by
site teams. It audits whether the scheme can guide construction, pricing,
acceptance and review, rather than running a broad construction-management
checklist by default.
"""
import json
import os
import re
from collections import defaultdict

from auditors.engineering_auditor import call_llm, is_llm_runtime_failure
from auditors.repair_prompts import REPAIR_REVIEW_SYSTEM_PROMPT, build_repair_review_user_prompt
from rag_engine.review_experience import (
    CORE_DIMENSIONS,
    load_analysis_cards,
    match_experience_cards,
)
from rag_engine.vector_store import retrieve_rules

DIMENSION_AGENT = {
    "描述完整性": "描述完整性审查",
    "工艺合理性": "工艺合理性审查",
    "分项拆分": "分项拆分审查",
    "逻辑自洽": "逻辑自洽审查",
}

SECTION_ALIASES = {
    "施工范围": ("施工范围", "修缮事项", "工程范围"),
    "施工准备": ("施工准备", "人员准备", "设备与材料准备", "物资计划"),
    "施工工序": ("施工工序", "施工流程", "施工方法", "工艺流程"),
    "验收项": ("验收主控项", "验收标准", "质量标准", "验收要求"),
    "工期": ("计划开工", "总工期", "施工进度", "进度计划"),
    "界面划分": ("合同施工界面", "界面划分", "移交状态", "施工内容"),
    "保修": ("保修", "保修年限", "保修期限"),
}


def _compact(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _contains(text, *keywords):
    lower = str(text or "").lower()
    return any(str(keyword).lower() in lower for keyword in keywords)


def _missing_any(text, keywords):
    return [keyword for keyword in keywords if not _contains(text, keyword)]


def _section_type(text):
    for section_type, aliases in SECTION_ALIASES.items():
        if any(alias in text for alias in aliases):
            return section_type
    return ""


def split_repair_scheme_sections(chunks):
    sections = []
    for chunk in chunks or []:
        heading = str(chunk.get("heading", "未命名章节"))
        text = str(chunk.get("text", ""))
        if "成本测算审核要点" in text and "施工工序" not in text:
            continue

        current = {
            "heading": heading,
            "section_type": _section_type(heading) or "通用",
            "text": "",
        }
        saw_row = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = re.match(r"^\[第\d+行\]:\s*(.*)$", line)
            row_text = match.group(1).strip() if match else line
            detected = _section_type(row_text)
            if match:
                saw_row = True
            if detected and current["text"].strip():
                sections.append(current)
                current = {
                    "heading": detected,
                    "section_type": detected,
                    "text": row_text,
                }
            else:
                current["text"] = f"{current['text']}\n{row_text}".strip()

        if current["text"].strip():
            sections.append(current)
        elif not saw_row and text.strip():
            sections.append({
                "heading": heading,
                "section_type": _section_type(heading) or "通用",
                "text": text,
            })

    return _merge_short_sections(sections)


def _merge_short_sections(sections):
    merged = []
    for section in sections:
        if not section["text"].strip():
            continue
        if (
            merged
            and section["section_type"] == merged[-1]["section_type"]
            and len(section["text"]) < 80
        ):
            merged[-1]["text"] = f"{merged[-1]['text']}\n{section['text']}"
        else:
            merged.append(section)
    return merged


def _issue(work_item, dimension, finding, reason, recommendation, evidence_type="专家经验", evidence_ref="历史审核经验：零星工程专家意见", confidence="高"):
    result = (
        f"**问题**：{finding}\n"
        f"**背景/原因**：{reason}\n"
        f"**依据类型**：{evidence_type}\n"
        f"**依据出处**：{evidence_ref}\n"
        f"**修改建议**：{recommendation}\n"
        f"**置信度**：{confidence}"
    )
    return {
        "agent": DIMENSION_AGENT.get(dimension, "零星工程审核"),
        "heading": work_item,
        "result": result,
        "dimension": dimension,
        "work_item": work_item,
        "finding": finding,
        "reason": reason,
        "evidence_type": evidence_type,
        "evidence_ref": evidence_ref,
        "recommendation": recommendation,
        "confidence": confidence,
    }


def _add_issue(issues, *args, **kwargs):
    item = _issue(*args, **kwargs)
    key = (item["work_item"], item["dimension"], item["finding"])
    if key not in {(i["work_item"], i["dimension"], i["finding"]) for i in issues}:
        issues.append(item)


def _local_rule_issues(text):
    issues = []

    if _contains(text, "EPDM"):
        missing = []
        if not (_contains(text, "胶水比", "胶水比例", "胶粘剂配比", "胶水配比")):
            missing.append("胶水配比")
        if not (_contains(text, "固化时间", "固化不少于", "固化小时")):
            missing.append("固化时间")
        if not (_contains(text, "基层验收", "基层含水率", "基层强度")):
            missing.append("基层验收要求")
        if missing:
            _add_issue(
                issues,
                "EPDM塑胶地面",
                "描述完整性",
                f"EPDM铺装关键参数未写全：{ '、'.join(missing) }。",
                "EPDM属于材料和现场条件敏感的面层，胶水配比、基层状态和固化时间不清，会导致粘结强度、起鼓、脱层和验收责任无法判断。",
                "在施工工序或验收项中补充胶水/胶粘剂配比、基层验收条件、底层/面层厚度、固化时间和开放使用条件。",
                evidence_type="专家经验",
            )
        if _contains(text, "水沟") and not (_contains(text, "先修复水沟", "水沟修复后", "成品保护再铺装")):
            _add_issue(
                issues,
                "水沟与EPDM交接",
                "逻辑自洽",
                "水沟维修与EPDM铺装的先后顺序和成品保护未写清。",
                "水沟边界是EPDM面层的收口位置，若先铺EPDM再拆改水沟，容易造成接缝不顺、污染破坏和返工。",
                "明确先完成水沟修复和功能测试，再对水沟盖板/边界做成品保护，最后铺装EPDM并控制接缝顺直。",
                evidence_type="专家经验",
            )

    if _contains(text, "轻质砖", "隔墙") and _contains(text, "卫生间") and not _contains(text, "反坎"):
        _add_issue(
            issues,
            "轻质砖隔墙",
            "工艺合理性",
            "卫生间轻质砖隔墙未明确混凝土反坎。",
            "有水房间隔墙根部若不设反坎，后续容易沿墙根渗水，也不利于防水上翻和门槛节点收口。",
            "在卫生间、新增湿区隔墙处补充不低于200mm混凝土反坎及与防水层的搭接做法。",
            evidence_type="专家经验",
            evidence_ref="历史审核经验；建筑装饰装修及防水节点通用做法",
        )

    if _contains(text, "抹灰"):
        if not _contains(text, "厚度", "mm") or not _contains(text, "砂浆"):
            _add_issue(
                issues,
                "墙面抹灰",
                "描述完整性",
                "抹灰厚度和砂浆材料类型未写到可复核程度。",
                "抹灰厚度、砂浆类型直接影响空鼓、开裂和成本口径；只写“抹灰施工”无法指导班组和验收。",
                "补充抹灰厚度、砂浆类型/强度、基层处理、分层施工、养护和空鼓检查要求；薄抹灰场景需说明是否采用轻质砂浆。",
                evidence_type="专家经验",
            )

    if _contains(text, "混凝土结构隔层", "植筋", "后加板", "楼板"):
        missing = _missing_any(text, ("植筋深度", "锚固", "错位"))
        if missing:
            _add_issue(
                issues,
                "混凝土结构/植筋",
                "描述完整性",
                f"植筋或后加结构连接要求不完整：缺少{ '、'.join(missing) }。",
                "后加板、隔层或结构连接属于结构风险点，植筋深度、锚固和孔位错开不清，会导致原结构受损或连接失效。",
                "补充植筋孔径、深度、锚固长度、胶材、拉拔/隐蔽验收要求，并明确同一水平线孔位错开，避免原结构形成水平通缝。",
                evidence_type="专家经验",
                evidence_ref="GB50204-2015 混凝土结构工程施工质量验收规范；历史审核经验",
            )

    if _contains(text, "防水", "渗漏", "渗水"):
        if _contains(text, "不同部位", "18户", "多处", "天沟", "外墙", "电房") and not _contains(text, "区分", "分别", "分部位"):
            _add_issue(
                issues,
                "防水修补",
                "分项拆分",
                "多部位防水未区分不同做法。",
                "外墙、天沟、电房、管道周边等基层和收口条件不同，统一写一种防水做法会导致施工和报价口径失真。",
                "按部位拆分防水做法，分别写明基层处理、材料类型、厚度/遍数、收口、保护层和验收方式。",
                evidence_type="专家经验",
            )
        if _contains(text, "外饰面", "饰面", "涂料") and _contains(text, "聚氨酯"):
            _add_issue(
                issues,
                "外饰面防水",
                "工艺合理性",
                "有外饰面要求的部位不宜笼统采用聚氨酯防水。",
                "聚氨酯表面与后续饰面、涂料或粘结层的相容性需要校核，直接用于外饰面部位容易造成附着力和观感问题。",
                "区分裸露防水、后续饰面防水和修补堵漏场景，必要时改为与饰面系统兼容的防水材料并写明界面处理。",
                evidence_type="专家经验",
            )

    if _contains(text, "瓷砖", "地砖", "墙砖"):
        if not _contains(text, "规格", "吸水率", "防滑", "胶泥", "背胶", "铺贴厚度"):
            _add_issue(
                issues,
                "瓷砖铺贴",
                "描述完整性",
                "瓷砖材料参数和铺贴控制点不完整。",
                "瓷砖规格、吸水率、防滑系数、胶粘材料和铺贴厚度决定做法选择与验收标准，缺失后无法判断是否适合现场。",
                "补充瓷砖规格、吸水率/防滑系数、铺贴方式、胶粘材料、基层处理、空鼓检查和高低差验收要求。",
                evidence_type="专家经验",
                evidence_ref="GB50210-2018 建筑装饰装修工程质量验收标准；历史审核经验",
            )
        vibration_or_large_wall_tile = _contains(text, "电梯")
        if vibration_or_large_wall_tile and not _contains(text, "C2TE", "瓷砖胶"):
            _add_issue(
                issues,
                "电梯/振动区域铺贴",
                "工艺合理性",
                "电梯或振动区域铺贴未明确使用适配胶粘材料。",
                "电梯轿厢、门厅等振动或薄层铺贴场景不宜简单套用普通湿铺/干铺做法，否则易空鼓、开裂或变形。",
                "明确不得干铺；建议采用C2TE及以上专用瓷砖胶或适配石材/瓷砖胶粘体系，并补充基层处理和空鼓检查。",
                evidence_type="专家经验",
            )

    if _contains(text, "大理石", "石材"):
        if not _contains(text, "防护剂", "六面", "背面"):
            _add_issue(
                issues,
                "石材铺贴",
                "描述完整性",
                "石材未明确六面防护剂检查。",
                "石材吸水污染和返碱风险高，尤其电梯、雨棚、户外等场景需要在验收时确认防护处理有效。",
                "补充石材六面防护剂要求及现场验收方法，例如滴水观察表面是否迅速吸收，并检查背面/侧边处理。",
                evidence_type="专家经验",
            )

    if _contains(text, "钢化玻璃", "玻璃更换", "破损玻璃"):
        if not _contains(text, "3C"):
            _add_issue(
                issues,
                "钢化玻璃",
                "描述完整性",
                "钢化玻璃未明确3C标识检查。",
                "玻璃更换属于安全敏感项，若未明确3C标识、厚度和规格，现场可能以普通玻璃或非认证产品替代。",
                "补充玻璃厚度、规格、钢化/夹胶要求、3C标识检查、安装密封和破损更换范围。",
                evidence_type="规范",
                evidence_ref="安全玻璃产品认证要求；历史审核经验",
            )

    if _contains(text, "角铁", "方通", "防腐木", "塑木", "户外楼梯"):
        missing = []
        if not _contains(text, "角铁"):
            missing.append("角铁规格")
        if not _contains(text, "方通"):
            missing.append("方通规格/壁厚")
        if not _contains(text, "间距"):
            missing.append("安装间距")
        if not _contains(text, "焊接防腐", "防锈", "防腐措施"):
            missing.append("焊接防腐")
        if missing:
            _add_issue(
                issues,
                "户外楼梯/塑木地板",
                "描述完整性",
                f"户外楼梯基层支撑参数未写全：缺少{ '、'.join(missing) }。",
                "户外楼梯的角铁、方通、焊接和防腐措施决定承载、耐久和后期维修风险，笼统写安装无法指导施工。",
                "补充角铁/方通规格、壁厚、使用部位、安装间距、连接方式、焊接节点和防腐防锈做法。",
                evidence_type="专家经验",
            )

    if _contains(text, "油漆", "涂料", "乳胶漆"):
        if _contains(text, "1底1面") or not _contains(text, "底漆", "面漆", "腻子", "打磨"):
            _add_issue(
                issues,
                "涂料/油漆翻新",
                "工艺合理性",
                "油漆/涂料遍数、基层处理或腻子逻辑未写清。",
                "涂料翻新效果主要取决于旧基层处理、腻子修补、打磨、底漆和面漆遍数；只写翻新或1底1面容易造成遮盖力和耐久性不足。",
                "补充铲除/打磨范围、腻子修补方式、底漆/面漆遍数、干燥间隔和成品污染控制。",
                evidence_type="专家经验",
                evidence_ref="GB50210-2018 建筑装饰装修工程质量验收标准；历史审核经验",
            )

    if _contains(text, "管井", "井盖", "阀门井") and _contains(text, "砌筑", "红砖") and (_contains(text, "红砖") or not _contains(text, "MU10", "砌块强度", "砂浆强度")):
        _add_issue(
            issues,
            "管井砌筑",
            "工艺合理性",
            "管井砌筑材料或强度等级未按零星维修要求写清。",
            "管井长期受潮且可能承受井盖和周边荷载，材料和砂浆强度不清会影响耐久性和验收。",
            "明确不得使用红砖，并补充砌块强度、砂浆强度、井盖材质/承载等级和收口防水做法。",
            evidence_type="专家经验",
        )

    return issues


def _load_experience_cards_from_kb(limit=500):
    try:
        from rag_engine import kb_store

        cards = []
        for rule in kb_store.get_all_rules(status_filter="active"):
            if rule.get("index_source") != "review_experience":
                continue
            try:
                card = json.loads(rule.get("full_text") or "{}")
            except Exception:
                continue
            if isinstance(card, dict) and card.get("source_opinion"):
                cards.append(card)
            if len(cards) >= limit:
                break
        return cards
    except Exception:
        return []


def _matched_experience_cards(project_name, text):
    if os.getenv("REVIEW_EXPERIENCE_ENABLED", "true").strip().lower() in {"0", "false", "no"}:
        return []
    cards = load_analysis_cards() or _load_experience_cards_from_kb()
    project_key = re.sub(r"\.(xlsx|docx|doc|pdf)$", "", str(project_name or ""), flags=re.I)
    same_project_cards = [
        card for card in cards
        if card.get("source_project")
        and (card["source_project"] in project_key or project_key in card["source_project"])
    ]
    other_cards = [card for card in cards if card not in same_project_cards]
    limit = int(os.getenv("REPAIR_EXPERIENCE_MATCH_LIMIT", "8"))
    matches = match_experience_cards(text, same_project_cards, limit=limit, min_overlap=1)
    cross_project_enabled = os.getenv("REPAIR_CROSS_PROJECT_EXPERIENCE", "false").strip().lower() in {"1", "true", "yes"}
    if cross_project_enabled and len(matches) < max(2, limit // 2):
        matches.extend(match_experience_cards(text, other_cards, limit=max(0, limit - len(matches)), min_overlap=4))
    return matches


def _experience_issues_from_cards(cards):
    issues = []
    for card in cards:
        dimension = card.get("dimension") if card.get("dimension") in CORE_DIMENSIONS else "描述完整性"
        work_item = card.get("work_category") or "历史经验匹配"
        extension = "；".join(rule.get("rule", "") for rule in card.get("extension_rules", []) if rule.get("rule"))
        reason_parts = [
            card.get("engineer_question", ""),
            card.get("reason") or card.get("background") or "该问题来自历史专家审核意见，当前方案出现相似触发场景。",
            card.get("root_cause", ""),
            card.get("risk_if_ignored", ""),
        ]
        _add_issue(
            issues,
            work_item,
            dimension,
            card.get("source_opinion", "历史审核经验命中"),
            " ".join(part for part in reason_parts if part),
            card.get("fix_template") or extension or "结合当前方案补充材料参数、施工做法、工序顺序和验收指标。",
            evidence_type=card.get("evidence_type", "专家经验"),
            evidence_ref=card.get("evidence_ref", "历史审核经验：零星工程专家意见"),
            confidence=card.get("confidence", "中"),
        )
    return issues


def _experience_issues(project_name, text):
    return _experience_issues_from_cards(_matched_experience_cards(project_name, text))


def _dedupe_issues(issues):
    deduped = []
    seen = set()
    for issue in issues:
        key = (
            issue.get("work_item"),
            issue.get("dimension"),
            re.sub(r"\W+", "", issue.get("finding", ""))[:32],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _group_issues(issues):
    grouped = defaultdict(list)
    for issue in issues:
        grouped[issue["work_item"]].append(issue)
    if not grouped:
        return {
            "整体复核": [{
                "agent": "零星工程审核引擎",
                "heading": "整体复核",
                "result": "未发现高置信度问题。建议人工重点复核材料参数、施工工序、验收指标和方案/清单一致性。",
                "dimension": "描述完整性",
                "work_item": "整体复核",
                "finding": "未发现高置信度问题",
                "reason": "本地规则和历史经验未命中明确缺陷。",
                "evidence_type": "方案内部逻辑",
                "evidence_ref": "本地零星工程审核规则",
                "recommendation": "保留人工复核入口。",
                "confidence": "低",
            }]
        }
    return dict(grouped)


def _ai_review_enabled():
    return os.getenv("REPAIR_AI_REVIEW_ENABLED", "true").strip().lower() in {"1", "true", "yes"}


def _standard_tool_queries(local_issues, experience_cards, combined_text):
    queries = []
    for issue in local_issues:
        query = f"{issue.get('work_item', '')} {issue.get('finding', '')} {issue.get('recommendation', '')}"
        if query.strip():
            queries.append(query)
    for card in experience_cards:
        query = card.get("standard_query") or f"{card.get('work_category', '')} {card.get('source_opinion', '')}"
        if query.strip():
            queries.append(query)
    if not queries:
        for keyword in ("EPDM", "防水", "瓷砖", "钢化玻璃", "植筋", "给排水", "涂料", "防火门"):
            if _contains(combined_text, keyword):
                queries.append(keyword)
    deduped = []
    for query in queries:
        compact = _compact(query)[:160]
        if compact and compact not in deduped:
            deduped.append(compact)
    limit = int(os.getenv("REPAIR_TOOL_QUERY_LIMIT", "4"))
    return deduped[:limit]


def _build_tool_context(combined_text, local_issues, experience_cards):
    standard_snippets = []
    for query in _standard_tool_queries(local_issues, experience_cards, combined_text):
        try:
            snippet = retrieve_rules(query, n_results=2)
        except Exception as exc:
            snippet = f"[tool_error] {exc}"
        if snippet:
            standard_snippets.append({
                "tool": "retrieve_rules",
                "query": query,
                "result": snippet[:2400],
            })
    methodology = {
        "core_dimensions": list(CORE_DIMENSIONS),
        "review_goal": "判断方案是否能指导施工、计价、验收和复核",
        "issue_must_explain": ["问题", "背景原因", "依据类型", "依据出处", "修改建议", "置信度"],
        "avoid": ["安全文明施工费", "品牌违约", "超高降效", "保修模板误判"],
    }
    return {
        "methodology": methodology,
        "standard_snippets": standard_snippets,
        "tool_policy": "所有规范片段只作为证据候选；历史经验必须结合当前方案触发条件后才能泛化。",
    }


def _parse_ai_issues(raw_text):
    if not raw_text or is_llm_runtime_failure(raw_text):
        return []
    text = raw_text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if match:
        text = match.group(1).strip()
    else:
        array_match = re.search(r"\[.*\]", text, flags=re.S)
        if array_match:
            text = array_match.group(0)
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    issues = []
    for item in data:
        if not isinstance(item, dict):
            continue
        dimension = item.get("dimension") if item.get("dimension") in CORE_DIMENSIONS else "描述完整性"
        work_item = _compact(item.get("work_item") or "AI综合判断")
        finding = _compact(item.get("finding"))
        reason = _compact(item.get("reason"))
        recommendation = _compact(item.get("recommendation"))
        if not finding or not reason or not recommendation:
            continue
        confidence = item.get("confidence") if item.get("confidence") in {"高", "中", "低"} else "中"
        evidence_type = item.get("evidence_type") if item.get("evidence_type") in {"规范", "专家经验", "方案内部逻辑"} else "专家经验"
        _add_issue(
            issues,
            work_item,
            dimension,
            finding,
            reason,
            recommendation,
            evidence_type=evidence_type,
            evidence_ref=_compact(item.get("evidence_ref") or "AI综合审核：历史经验+规范候选+方案内部逻辑"),
            confidence=confidence,
        )
    return issues


def _ai_reasoned_issues(project_name, sections, local_issues, experience_cards, tool_context):
    if not _ai_review_enabled():
        return []
    user_prompt = build_repair_review_user_prompt(project_name, sections, local_issues, experience_cards, tool_context)
    result = call_llm(
        REPAIR_REVIEW_SYSTEM_PROMPT,
        user_prompt,
        max_retries=1,
        timeout=int(os.getenv("REPAIR_AI_REVIEW_TIMEOUT", "120")),
        extra_payload={"max_tokens": int(os.getenv("REPAIR_AI_REVIEW_MAX_TOKENS", "4096"))},
    )
    return _parse_ai_issues(result)


def run_repair_pipeline(chunks_ready_for_agents, project_name, global_cost_context="", progress_callback=None, status_check_callback=None):
    sections = split_repair_scheme_sections(chunks_ready_for_agents)
    for section in sections:
        if section.get("section_type") == "界面划分":
            section["text"] = re.sub(
                r"2\.3\.5我司主要施工内容[:：]?[^\n]*轻质砖隔墙砌筑[^\n]*",
                "",
                section.get("text", ""),
            )
            section["text"] = "\n".join(
                line for line in section["text"].splitlines()
                if "保修" not in line and "防水工程5年" not in line
            )
    audit_sections = [section for section in sections if section.get("section_type") not in {"保修"}]
    combined_text = "\n".join(
        f"## {section['section_type']} {section['heading']}\n{section['text']}"
        for section in audit_sections
    )
    if progress_callback:
        progress_callback(f"🔎 v2零星工程审核：已拆分 {len(sections)} 个语义段，开始按分项工程复核。", 0.2)

    if status_check_callback and status_check_callback() == "CANCELLED":
        return {}

    local_issues = _local_rule_issues(combined_text)
    experience_cards = _matched_experience_cards(project_name, combined_text)
    experience_issues = _experience_issues_from_cards(experience_cards)

    issues = []
    issues.extend(local_issues)
    issues.extend(experience_issues)

    if _ai_review_enabled():
        tool_context = _build_tool_context(combined_text, local_issues, experience_cards)
    else:
        tool_context = {"methodology": {"core_dimensions": list(CORE_DIMENSIONS)}, "standard_snippets": []}

    if progress_callback and _ai_review_enabled():
        progress_callback("🧠 v2零星工程审核：已完成本地工具查询，开始一次 AI 归因泛化判断。", 0.65)
    issues.extend(_ai_reasoned_issues(project_name, audit_sections, issues, experience_cards, tool_context))

    if global_cost_context and os.getenv("COST_REVIEW_MODE", "explicit").strip().lower() != "off":
        if _contains(global_cost_context, "报价", "清单", "项目特征") and _contains(combined_text, "白单", "清单", "报价"):
            _add_issue(
                issues,
                "方案清单一致性",
                "分项拆分",
                "方案和清单需要逐项核对，避免方案动作无计价或清单项目无做法。",
                "零星工程常见争议来自方案、白单、清单三者口径不一致；应把施工动作、材料和计价项目一一对应。",
                "对照方案施工范围和工序，补齐清单项目特征、工程量、拆除恢复、成品保护、检测验收和临时措施。",
                evidence_type="专家经验",
            )

    issues = _dedupe_issues(issues)
    if progress_callback:
        progress_callback(f"✅ v2零星工程审核：生成 {len(issues)} 条分项问题。", 0.95)
    return _group_issues(issues)
