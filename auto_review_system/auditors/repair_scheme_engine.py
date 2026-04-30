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
from auditors.repair_prompts import (
    REPAIR_CRITIC_SYSTEM_PROMPT,
    REPAIR_REVIEW_SYSTEM_PROMPT,
    REPAIR_TOOL_PLAN_SYSTEM_PROMPT,
    build_repair_critic_user_prompt,
    build_repair_review_user_prompt,
    build_repair_tool_plan_user_prompt,
)
from llm.cache import call_stats_since, current_timestamp
from rag_engine.review_experience import (
    CORE_DIMENSIONS,
    assess_scheme_alignment,
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

SOURCE_DOMAIN_TERMS = {
    "EPDM": ("EPDM", "塑胶地面", "塑胶地垫"),
    "硅PU": ("硅PU",),
    "丙烯酸": ("丙烯酸",),
    "环氧": ("环氧",),
    "自流平": ("自流平",),
    "钢化玻璃": ("钢化玻璃", "玻璃更换", "破损玻璃"),
    "大理石": ("大理石", "石材"),
    "植筋": ("植筋", "后加板", "结构隔层", "混凝土结构"),
    "轻质砖": ("轻质砖", "隔墙"),
    "防火门": ("防火门",),
    "C2TE": ("电梯", "瓷砖", "大理石", "石材"),
}

ISSUE_DOMAIN_TERMS = (
    "EPDM", "水沟", "植筋", "反坎", "钢化玻璃", "大理石", "石材", "C2TE", "瓷砖",
    "角铁", "方通", "油漆", "乳胶漆", "腻子", "抹灰", "防火门", "防水",
)
DUPLICATE_HINT_TERMS = ISSUE_DOMAIN_TERMS + (
    "胶水", "配比", "固化", "养护", "基层", "石凳", "倒角", "美纹纸", "收口",
    "防护剂", "六面", "试验记录", "清单", "报价", "工序", "成品保护",
)
COST_OR_MEASURE_HINT = re.compile(r"措施费|报价|白单|清单|对下|结算|计量|工程量")
VALID_AI_REVIEW_MODES = {"off", "once", "adaptive", "quality"}
AI_BUDGET_DEFAULTS = {"off": 0, "once": 1, "adaptive": 2, "quality": 3}
HIGH_RISK_TERMS = ("防水", "渗漏", "植筋", "防火门", "钢化玻璃", "给排水", "弱电", "电梯", "石材", "EPDM")
ALLOWED_REPAIR_TOOLS = {"standards_search", "experience_search", "scheme_snippet", "cost_snippet"}
GENERIC_QUERY_TOKENS = {
    "工程", "施工", "方案", "改造", "维修", "翻新", "明确", "需要", "材料", "工艺",
    "验收", "问题", "当前", "历史", "专家", "审核", "分项", "进行",
}


def _compact(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _contains(text, *keywords):
    lower = str(text or "").lower()
    return any(str(keyword).lower() in lower for keyword in keywords)


def _missing_any(text, keywords):
    return [keyword for keyword in keywords if not _contains(text, keyword)]


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _ai_review_mode():
    raw_mode = os.getenv("REPAIR_AI_REVIEW_MODE", "").strip().lower()
    if raw_mode:
        return raw_mode if raw_mode in VALID_AI_REVIEW_MODES else "adaptive"

    legacy = os.getenv("REPAIR_AI_REVIEW_ENABLED", "").strip().lower()
    if legacy in {"0", "false", "no", "off"}:
        return "off"
    if legacy in {"1", "true", "yes", "on"}:
        return "adaptive"
    return "adaptive"


def _ai_call_budget(mode):
    return max(0, _safe_int_env("REPAIR_AI_CALL_BUDGET", AI_BUDGET_DEFAULTS.get(mode, 2)))


def _tool_query_limit():
    return max(0, _safe_int_env("REPAIR_TOOL_QUERY_LIMIT", 6))


def _tool_result_chars():
    return max(400, _safe_int_env("REPAIR_TOOL_RESULT_CHARS", 2400))


def _thinking_enabled():
    return _env_bool("LLM_THINKING_ENABLED", False)


def _risk_terms_in_text(text):
    return [term for term in HIGH_RISK_TERMS if _contains(text, term)]


def _stone_six_face_applicable(text):
    """Only require six-face stone protection in real paving/finish contexts."""
    if not _contains(text, "大理石", "石材", "花岗岩"):
        return False
    finish_context = re.search(
        r"(电梯|厅|室内|墙面|门槛|台阶|地面铺贴|石材安装)[^。；\n]{0,80}"
        r"(石材|大理石|花岗岩)[^。；\n]{0,80}(铺贴|粘贴|安装|饰面|地板)"
        r"|(?:石材|大理石|花岗岩)[^。；\n]{0,80}(铺贴|粘贴|饰面|六面|防护剂|地面)",
        str(text or ""),
    )
    if not finish_context:
        return False
    if _contains(text, "水沟盖板", "挡土墙") and not _contains(text, "铺贴", "粘贴", "饰面", "电梯"):
        return False
    return True


def _complexity_score(combined_text, audit_sections, local_issues, experience_cards, global_cost_context):
    reasons = []
    if len(combined_text) > 6000:
        reasons.append("方案文本超过6000字")
    if len(audit_sections) >= 5:
        reasons.append("语义段不少于5个")
    if len(local_issues) >= 2:
        reasons.append("本地问题不少于2条")
    if len(experience_cards) >= 2:
        reasons.append("命中历史经验卡不少于2张")
    if str(global_cost_context or "").strip():
        reasons.append("上传了报价/白单/清单上下文")
    risk_terms = _risk_terms_in_text(combined_text)
    if len(risk_terms) >= 2:
        reasons.append(f"命中高风险领域：{'、'.join(risk_terms[:6])}")
    return len(reasons), reasons


def _domain_matches_current_scheme(card, text):
    source = str(card.get("source_opinion", ""))
    current = str(text or "")
    for term, current_aliases in SOURCE_DOMAIN_TERMS.items():
        if term.lower() in source.lower() and not _contains(current, *current_aliases):
            return False
    return True


def _tag_cards(cards, match_scope):
    tagged = []
    for card in cards or []:
        copied = dict(card)
        copied["match_scope"] = match_scope
        tagged.append(copied)
    return tagged


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


def _format_checkpoint_assessments(checkpoint_assessments):
    lines = []
    for item in checkpoint_assessments or []:
        name = _compact(item.get("name", ""))
        status = _compact(item.get("status", ""))
        note = _compact(item.get("note", ""))
        if not name or not status:
            continue
        line = f"- {name}：{status}"
        if note:
            line += f"，{note}"
        lines.append(line)
    return "\n".join(lines)


def _issue(
    work_item,
    dimension,
    finding,
    reason,
    recommendation,
    evidence_type="专家经验",
    evidence_ref="历史审核经验：零星工程专家意见",
    confidence="高",
    checkpoint_assessments=None,
    covered_points=None,
    partial_points=None,
    missing_points=None,
    alignment_status="",
):
    checkpoint_text = _format_checkpoint_assessments(checkpoint_assessments)
    control_block = ""
    if checkpoint_text:
        control_block = (
            f"**控制点判断**：\n{checkpoint_text}\n"
            f"**对齐状态**：{alignment_status or '部分补齐/需复核'}\n"
        )
    result = (
        f"**问题**：{finding}\n"
        f"**背景/原因**：{reason}\n"
        f"{control_block}"
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
        "checkpoint_assessments": checkpoint_assessments or [],
        "covered_points": covered_points or [],
        "partial_points": partial_points or [],
        "missing_points": missing_points or [],
        "alignment_status": alignment_status,
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

    if _stone_six_face_applicable(text):
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
    matches = _tag_cards(match_experience_cards(text, same_project_cards, limit=limit, min_overlap=1), "same_project")
    cross_project_enabled = os.getenv("REPAIR_CROSS_PROJECT_EXPERIENCE", "true").strip().lower() in {"1", "true", "yes"}
    if cross_project_enabled and len(matches) < max(2, limit // 2):
        min_overlap = int(os.getenv("REPAIR_CROSS_PROJECT_MIN_OVERLAP", "2"))
        cross_limit = min(
            max(0, limit - len(matches)),
            int(os.getenv("REPAIR_CROSS_PROJECT_MATCH_LIMIT", "4")),
        )
        cross_pool = [card for card in other_cards if _domain_matches_current_scheme(card, text)]
        matches.extend(
            _tag_cards(
                match_experience_cards(text, cross_pool, limit=cross_limit, min_overlap=min_overlap),
                "cross_project",
            )
        )
    return matches


def _align_experience_cards_to_current_scheme(cards, combined_text):
    aligned_cards = []
    for card in cards or []:
        copied = dict(card)
        runtime_row = dict(copied)
        runtime_row["opinion"] = copied.get("source_opinion", "")
        runtime_row["scheme_evidence"] = [{
            "source_file": "当前审核方案",
            "location": "当前方案全文",
            "text": combined_text,
        }]
        alignment = assess_scheme_alignment(runtime_row)
        if isinstance(alignment.get("evidence_chain"), dict):
            alignment["evidence_chain"] = dict(alignment["evidence_chain"])
            alignment["evidence_chain"]["scheme_evidence"] = []
        copied["source_alignment_status"] = copied.get("alignment_status", "")
        copied["source_scheme_gap"] = copied.get("scheme_gap", "")
        copied.update(alignment)
        copied["alignment_basis"] = "current_scheme"
        aligned_cards.append(copied)
    return aligned_cards


CHECKPOINT_REWRITE_TEMPLATES = {
    "项目名称": "方案首页、施工范围和白单/清单中的项目名称应统一，避免套用旧模板或项目名称错写；提交前应逐项核对工程名称、楼栋、区域和分项名称。",
    "项目名称校核": "方案首页、施工范围和白单/清单中的项目名称应统一，避免套用旧模板或项目名称错写；提交前应逐项核对工程名称、楼栋、区域和分项名称。",
    "维修对象": "施工范围应写清具体维修对象、楼栋楼层、房间/点位、数量和利旧/更新边界，不能只写笼统的维修或改造。",
    "现状问题描述": "方案应补充现状问题描述，说明破损、渗漏、空鼓、锈蚀、老化或功能失效的表现，并与后续处理方法对应。",
    "目标效果": "方案应写明修复后的交付目标和验收口径，例如恢复防水功能、恢复通行、消除空鼓、达到观感一致或设备恢复连续运行。",
    "现场照片或描述": "对零星维修的关键部位应补现场照片或文字描述，说明病害位置、范围、基层状态和周边限制条件，作为工程量、做法和验收复核依据。",
    "原基层/原材料": "涉及翻新、修补或替换时，应写清原基层、原材料或原构造层状态，包括是否空鼓、起砂、粉化、锈蚀、潮湿、开裂或可继续利旧。",
    "病害原因": "方案应分析病害原因或至少列出需现场确认的原因分支，例如水源、裂缝路径、沉降、振动、基层强度不足或材料老化，避免只做表面修补。",
    "处理分支": "当现场条件可能不同，应设置处理分支；例如基层合格/不合格、裂缝宽度不同、管根渗漏/墙面渗漏、利旧/更换分别采用不同做法。",
    "白单/清单对应关系": "方案动作应与白单/清单逐项对应，明确哪些内容计入清单项目，哪些属于措施或附属工序，避免有方案无报价或有清单无做法。",
    "项目特征": "清单或白单项目应补足项目特征，包括部位、材料规格、厚度/尺寸、施工方法、拆除恢复边界和验收口径，使计价与施工做法一致。",
    "工程量口径": "工程量应说明计量口径和复核方法，包括按实计量、扣减规则、展开面积/投影面积、点位数量、长度或台班口径，并与现场范围对应。",
    "措施项目": "涉及围蔽、脚手架、吊车、曲臂车、临时保护、夜间施工或成品保护时，应单独说明措施项目是否计入报价以及适用范围。",
    "材料规格表": "材料应形成可采购、可验收的规格表，写明名称、规格型号、厚度/强度/等级、品牌或同等要求、适用部位和进场验收资料。",
    "设备参数": "设备类分项应写明型号、容量、功率、端口、存储、分辨率、供电、材质、防护等级等关键参数，并说明与原系统或现场条件的匹配关系。",
    "认证标志": "材料或设备涉及安全、消防、强制认证时，应写明进场检查认证标志、铭牌或身份信息，并与合格证、检测报告、型式资料逐项核对。",
    "允许偏差/验收指标": "验收项应写到可量测程度，补充允许偏差、平整度、空鼓率、垂直度、厚度、坡度、通水/通电、观感或功能指标，不能只写“符合要求”。",
    "工艺适用条件": "方案应说明所选工艺的适用条件，包括基层强度、含水率、环境温湿度、振动、潮湿、户外暴露或投入使用时间限制。",
    "基层处理要求": "施工工艺应补基层处理要求，写明铲除、打磨、清理、找平、界面处理、拉毛、除尘、干燥和隐蔽验收条件。",
    "替代材料说明": "如历史意见或现场条件建议改用替代材料，应说明替代原因、适用场景、性能差异、与原系统相容性和对报价/验收的影响。",
    "相容性说明": "多材料叠加时应说明相容性，例如防水层与饰面、石材与胶粘剂、金属防腐与面层、旧基层与新材料之间的粘结和界面处理要求。",
    "节点做法": "方案应补关键节点做法，尤其是管根、墙根、洞口、阴阳角、收边、搭接、变形缝和不同材料交接处的具体处理方法。",
    "收口措施": "涉及边界、洞口、门槛、石材边、玻璃边或防水收头时，应写明收口、密封、打胶、压条、遮蔽和成品污染控制措施。",
    "搭接尺寸": "防水、卷材、附加层、上翻、压边或构造连接应写明搭接宽度、上翻高度、压接尺寸和验收方法。",
    "成品保护": "相邻工序或已完成面层容易被污染破坏时，应写明保护材料、保护范围、保护时点、拆除时点和责任人。",
    "施工流程": "方案应按现场可执行顺序重排施工流程，明确先拆除、后基层验收、再材料施工、最后检测验收和恢复开放。",
    "工序间隔": "多遍涂刷、铺贴、胶粘、防水、油漆和灌浆等工序应写明间隔时间、干燥/固化条件和进入下一道工序的判断标准。",
    "保护措施": "对通行、临边、洞口、设备、住户区域和已完成工程应补保护措施，说明围蔽、遮挡、覆盖、警示和临时恢复要求。",
    "开放使用条件": "方案应写明完工后开放使用条件，例如固化/养护时间、闭水/通水/通电合格、强度达到要求、胶粘剂达到开放时间后方可使用。",
    "合格证/检测报告": "进场材料和设备应列明需要提交的合格证、检测报告、型式检验报告、材质证明或产品说明书，并说明核对对象。",
    "试验记录": "涉及防水、给排水、植筋、电气、弱电或设备运行时，应补闭水、通水、拉拔、绝缘、连续通电、调试或试运行记录。",
    "验收指标": "验收章节应补可执行指标和检查方法，明确检查频次、抽查部位、允许偏差、功能试验、观感标准和记录要求。",
    "影像或记录表": "隐蔽工程、关键节点、材料进场和试验验收应留存照片、视频或记录表，便于后续复核、结算和保修追溯。",
    "临时措施": "涉及临时排水、临时支撑、临时围蔽、临时通行、临时用电或临时拆改时，应写明措施范围、持续时间和恢复方式。",
    "安全防护": "安全防护只在与当前分项直接相关时补充，应明确临边、洞口、高处、用电、切割、吊装或住户通行的现场防护措施，避免泛泛套话。",
    "设备台班": "使用吊车、曲臂车、升降车、水泵、发电机等机械时，应写明台班口径、使用部位、工作内容和与工程量的对应关系。",
    "施工组织约束": "方案应说明影响施工组织的约束条件，例如营业/通行时间、住户影响、噪声限制、备货周期、天气窗口和分段施工安排。",
    "容量计算": "给排水、弱电、电气、设备或承重相关分项应补容量、流量、功率、存储、端口、荷载等计算或复核口径。",
    "功能参数": "功能类设备或系统应写明必须实现的功能参数，例如监控覆盖、录像存储、识别速度、通水能力、启闭功能或联动逻辑。",
    "荷载等级": "井盖、钢骨架、平台、楼梯、后加板或承重构件应明确承载等级、荷载条件、材料规格和验收复核方式。",
    "试运行要求": "弱电、电气、给排水、门禁或设备更换完成后应写明调试和试运行要求，包括连续运行时间、测试项目和合格标准。",
    "节点示意": "复杂节点、交接边界、管线走向、门禁联动、骨架布置或收口做法不易文字表达时，应补节点示意图或现场标注图。",
    "线路图": "弱电、电气、门禁、监控或给排水管线调整应补线路图/系统图，明确起止点、连接关系、端口、编号和利旧范围。",
    "构件尺寸表": "涉及钢构件、玻璃、石材、门窗、柜体、管材或预制件时，应补构件尺寸表，明确长宽厚、壁厚、孔位和数量。",
    "安装位置": "设备、构件、管线、灯具、摄像头、门禁和阀门等应写明安装位置、标高、编号、数量和与现场照片/图纸的对应关系。",
    "统一术语": "同一材料、工序或分项在方案、白单和验收中应使用统一名称，避免地砖/瓷砖、隔热层/保温层、DN/ND等混用。",
    "单位校核": "方案和白单中的单位应统一校核，重点检查 mm/m、平方米/米、个/套、厚度/面积/长度等单位是否与工程量口径匹配。",
    "楼栋部位校核": "楼栋、楼层、房号、区域和施工部位应逐项核对，防止复制模板后部位错误，导致采购、派工或验收对象错位。",
    "模板清理": "提交前应清理与本项目无关的模板内容，删除不适用的保修、移交、品牌、超高、合同界面或大项目管理条款，避免误导审核。",
    "方案原文证据": "迁移历史经验前，应先在当前方案中找到同类分项、现场条件或风险路径的原文证据；找不到证据时只作为人工复核提醒，不应直接判缺陷。",
    "专家复核备注": "对无法从文本直接判断的专家经验，应标注需要工程师复核的触发条件、疑点和需补资料，避免把历史意见机械套用到当前项目。",
    "胶水配比": "EPDM胶粘剂应写明品牌/型号及配比要求，按厂家产品技术资料或设计要求填写具体比例，现场拌合后留存配比记录，严禁随意加水或稀释。",
    "固化/养护时间": "EPDM面层铺装完成后应按材料技术资料明确固化养护时间和开放使用条件，养护期内设置围蔽保护，未达到规定时间不得开放使用。",
    "基层验收": "EPDM铺装前应完成基层验收，检查基层平整、干燥、清洁、无起砂空鼓及松动，验收合格并留存记录后方可进入铺装工序。",
    "水沟交接顺序": "水沟应先完成拆除、修复、盖板安装和功能测试，验收合格后对盖板及边界进行成品保护，再铺装EPDM，交接缝应顺直、密实、无污染破坏。",
    "倒角收口": "石凳翻新遇大倒角部位时，应在倒角下方粘贴美纹纸控制边界，涂刷完成后及时撕除，保证收口顺直、无流挂和污染。",
    "混凝土反坎": "卫生间及其他有水房间新增轻质砖隔墙底部应设置不低于200mm高混凝土反坎，并明确反坎与地面防水层、墙面防水上翻的搭接做法。",
    "抹灰厚度": "墙面抹灰应写明设计厚度、分层施工要求、基层拉毛或界面处理、养护和空鼓检查标准，并与白单/清单厚度口径保持一致。",
    "轻质砂浆": "如采用薄层抹灰或厚度较小的找平做法，应说明采用轻质砂浆或适配薄抹灰体系，并明确材料强度、适用基层和防空鼓开裂措施。",
    "植筋深度/锚固": "植筋方案应按墙、梁、柱、板等不同构件分别写明孔径、孔深、锚固长度、植筋胶型号、清孔方法和拉拔/隐蔽验收要求。",
    "错孔布置": "同一水平线植筋孔位应错开布置，并避让原结构主筋和薄弱部位，防止在原结构上形成连续水平通缝。",
    "结构专业复核": "后加板、隔层或结构受力改变内容应经结构专业复核后实施；如风险较高，应补充替代轻量化方案或专项结构说明。",
    "腻子基层处理": "油漆翻新前应明确旧基层处理方式，包括铲除空鼓/粉化旧腻子、基层清理、局部修补、满刮或局部批刮腻子、打磨和除尘要求。",
    "油漆遍数": "涂饰系统应说明底漆和面漆遍数选择原因；公共空间、活动室等高频使用部位应结合遮盖力、耐擦洗、观感和色差要求复核1底1面是否满足使用要求。",
    "角铁规格": "户外楼梯钢骨架应写明角铁规格、壁厚、材质和使用部位，并明确其作为踏步承重或侧边固定构件的作用。",
    "方通使用部位": "方通/方管应写明规格、壁厚、安装部位和连接方式，区分承重骨架、侧边固定和辅助支撑。",
    "安装间距": "钢骨架、方通或龙骨应写明安装间距和允许偏差，间距应满足塑木地板固定、承载和变形控制要求。",
    "焊接防腐": "焊接完成后应清除焊渣并检查无虚焊、漏焊，钢构件和焊缝部位应按防锈底漆加面漆或同等防腐体系处理，经隐蔽验收后再安装面层。",
    "C2TE性能等级": "电梯或振动区域石材/瓷砖铺贴应明确采用C2TE及以上等级专用瓷砖胶或同等性能胶粘体系，并提供产品合格资料。",
    "专用瓷砖胶/粘结剂": "石材/瓷砖铺贴应写明专用胶粘剂名称、适用基层、涂抹厚度、开放时间和压实要求，避免套用普通水泥砂浆干铺或湿铺。",
    "禁止干铺": "电梯地面、大规格砖或振动区域不得采用干铺法，应采用适配胶粘体系薄层铺贴，并明确基层清理、找平和空鼓检查要求。",
    "六面防护剂": "天然石材进场及铺贴前应检查六面防护处理，重点核查背面和侧边；未做防护或防护无效的石材不得铺贴。",
    "现场滴水检查": "石材防护效果可采用现场滴水抽查，水珠不被迅速吸收且能滚落时方可视为防护有效，并保留抽查记录。",
    "3C标识": "钢化玻璃或夹胶安全玻璃进场时应逐块检查玻璃表面3C/CCC标识，并核对厚度、规格、检测报告和合格证明与方案一致。",
    "防火门型式资料": "防火门进场应核对型式认可证书、型式检验报告、产品合格证和铭牌信息，确认耐火等级、规格、开启方向和五金配置与方案一致。",
    "产品铭牌": "消防、安全或设备类产品应现场检查产品铭牌，核对生产厂家、型号规格、等级、出厂编号和认证信息，防止资料与实物不一致。",
    "填充密实检查": "防火门等门类材料应补现场复核方法，例如敲击门板检查填充是否密实，核对重量、合页、闭门器和锁具配置。",
    "防火门顺序器": "双扇或子母防火门应明确是否配置顺序器，并检查关闭顺序、闭门器、密封条和门缝，确保消防功能可实现。",
    "分项开项": "不同部位、不同做法或不同材料体系应单独开项，或在备注中附图说明对应部位，避免一个清单项覆盖多个施工逻辑。",
    "泄水管": "挡墙、屋面、平台、种植区或易积水部位应核查泄水管/排水管设置，说明位置、管径、坡向、防堵和防水土流失措施。",
    "交换机型号": "弱电监控或门禁系统应写明交换机型号、端口数量、供电方式、带宽和与摄像头/门禁点位的匹配关系。",
    "端口分配": "弱电设备应补端口分配表，明确每个摄像头、门禁、终端或上联链路对应的交换机端口和编号。",
    "连续通电测试": "弱电、电气或门禁改造完成后应进行连续通电运行测试，写明测试时长、测试内容、异常处理和记录留存要求。",
    "电箱/立杆规格": "室外电箱、监控立杆或设备支架应写明材质等级、壁厚、长宽厚、基础固定、防腐防水和安装位置。",
    "外墙裂缝材料体系": "外墙裂缝或渗漏修补应明确材料体系，区分防水抗裂砂浆、丙烯酸/JS/聚氨酯/注浆等适用场景，并说明基层处理和饰面恢复。",
    "路面划线": "停车场或道路翻新涉及划线时，应写明划线材料、颜色、线宽、标识位置、基层清理、干燥开放时间和验收方法。",
    "工期可控性": "工期计划应结合材料备货、现场施工窗口、养护/固化时间、试运行和天气影响复核，不能只给总天数。",
}


def _rewrite_suggestions_for_checkpoints(checkpoint_assessments):
    suggestions = []
    seen = set()
    for item in checkpoint_assessments or []:
        if item.get("status") == "具体覆盖":
            continue
        name = _compact(item.get("name", ""))
        suggestion = CHECKPOINT_REWRITE_TEMPLATES.get(name)
        if suggestion and suggestion not in seen:
            seen.add(suggestion)
            suggestions.append(suggestion)
    return suggestions


def _build_recommendation_from_card(card, base_recommendation):
    checkpoint_suggestions = _rewrite_suggestions_for_checkpoints(card.get("checkpoint_assessments", []))
    partial_points = card.get("partial_points", [])
    missing_points = card.get("missing_points", [])
    focus_points = []
    if partial_points:
        focus_points.append(f"将笼统表述具体化：{'、'.join(partial_points)}")
    if missing_points:
        focus_points.append(f"补充缺失项：{'、'.join(missing_points)}")

    parts = []
    if focus_points:
        parts.append(f"{'；'.join(focus_points)}。")
    if checkpoint_suggestions:
        lines = "\n".join(f"- {suggestion}" for suggestion in checkpoint_suggestions)
        parts.append(f"建议补写到方案：\n{lines}")
    if base_recommendation:
        parts.append(f"补充原则：{base_recommendation}")
    return "\n".join(parts) if parts else base_recommendation


def _experience_issues_from_cards(cards):
    issues = []
    for card in cards:
        if card.get("alignment_status") == "已补齐":
            continue
        source_opinion = card.get("source_opinion", "")
        if card.get("match_scope") == "cross_project":
            source_scope = " ".join(
                str(card.get(key, ""))
                for key in ("source_opinion", "source_project", "project_type", "file_type")
            )
            if COST_OR_MEASURE_HINT.search(source_scope):
                continue
            if not _rewrite_suggestions_for_checkpoints(card.get("checkpoint_assessments", [])):
                continue
        dimension = card.get("dimension") if card.get("dimension") in CORE_DIMENSIONS else "描述完整性"
        work_item = card.get("work_category") or "历史经验匹配"
        extension = "；".join(rule.get("rule", "") for rule in card.get("extension_rules", []) if rule.get("rule"))
        partial_points = card.get("partial_points", [])
        missing_points = card.get("missing_points", [])
        base_recommendation = card.get("fix_template") or extension or "结合当前方案补充材料参数、施工做法、工序顺序和验收指标。"
        recommendation = _build_recommendation_from_card(card, base_recommendation)
        reason_parts = [
            card.get("expert_intent", ""),
            card.get("scheme_gap", ""),
            card.get("reason") or card.get("background") or "该问题来自历史专家审核意见，当前方案出现相似触发场景。",
            card.get("root_cause", ""),
            card.get("risk_if_ignored", ""),
        ]
        _add_issue(
            issues,
            work_item,
            dimension,
            source_opinion or "历史审核经验命中",
            " ".join(part for part in reason_parts if part),
            recommendation,
            evidence_type=card.get("evidence_type", "专家经验"),
            evidence_ref=card.get("evidence_ref", "历史审核经验：零星工程专家意见"),
            confidence=card.get("confidence", "中"),
            checkpoint_assessments=card.get("checkpoint_assessments", []),
            covered_points=card.get("covered_points", []),
            partial_points=partial_points,
            missing_points=missing_points,
            alignment_status=card.get("alignment_status", ""),
        )
    return issues


def _experience_issues(project_name, text):
    cards = _align_experience_cards_to_current_scheme(_matched_experience_cards(project_name, text), text)
    return _experience_issues_from_cards(cards)


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
        if any(_is_semantic_duplicate(existing, issue) for existing in deduped):
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _issue_duplicate_terms(issue):
    text = " ".join(
        str(issue.get(key, ""))
        for key in ("work_item", "finding", "reason", "recommendation")
    )
    return {term for term in DUPLICATE_HINT_TERMS if term.lower() in text.lower()}


def _is_semantic_duplicate(existing, candidate):
    existing_text = re.sub(r"\W+", "", existing.get("finding", ""))
    candidate_text = re.sub(r"\W+", "", candidate.get("finding", ""))
    if existing_text and candidate_text and (
        existing_text in candidate_text or candidate_text in existing_text
    ):
        return True

    existing_terms = _issue_duplicate_terms(existing)
    candidate_terms = _issue_duplicate_terms(candidate)
    overlap = existing_terms & candidate_terms
    if len(overlap) < 2:
        return False

    candidate_origin = str(candidate.get("origin", ""))
    existing_has_controls = bool(existing.get("checkpoint_assessments"))
    if candidate_origin.startswith("ai_") and existing_has_controls:
        return True

    if existing.get("dimension") == candidate.get("dimension") and existing.get("work_item") == candidate.get("work_item"):
        return len(overlap) >= 3
    return len(overlap) >= 4


def _issue_domain_terms(issue):
    text = " ".join(str(issue.get(key, "")) for key in ("work_item", "finding", "recommendation"))
    return {term for term in ISSUE_DOMAIN_TERMS if term.lower() in text.lower()}


def _prefer_control_point_issues(issues):
    detailed_terms = set()
    for issue in issues:
        if issue.get("checkpoint_assessments"):
            detailed_terms.update(_issue_domain_terms(issue))
    if not detailed_terms:
        return issues

    filtered = []
    for issue in issues:
        if not issue.get("checkpoint_assessments") and (_issue_domain_terms(issue) & detailed_terms):
            continue
        filtered.append(issue)
    return filtered


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


def _ai_review_enabled(mode=None, budget=None):
    mode = mode or _ai_review_mode()
    budget = _ai_call_budget(mode) if budget is None else budget
    return mode != "off" and budget > 0


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
    limit = _tool_query_limit()
    return deduped[:limit]


def _build_tool_context(combined_text, local_issues, experience_cards, tool_results=None, tool_plan=None):
    standard_snippets = []
    if not tool_results:
        for query in _standard_tool_queries(local_issues, experience_cards, combined_text):
            try:
                snippet = retrieve_rules(query, n_results=2)
            except Exception as exc:
                snippet = f"[tool_error] {exc}"
            if snippet:
                standard_snippets.append({
                    "tool": "retrieve_rules",
                    "query": query,
                    "result": snippet[:_tool_result_chars()],
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
        "tool_plan": tool_plan or [],
        "tool_results": tool_results or [],
        "tool_policy": "所有规范片段只作为证据候选；历史经验必须结合当前方案触发条件后才能泛化。",
    }


def _json_array_from_text(raw_text):
    if not raw_text or is_llm_runtime_failure(raw_text):
        return None
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
        return None
    return data if isinstance(data, list) else None


def _query_tokens(query):
    tokens = []
    for token in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,8}", str(query or "")):
        if token in GENERIC_QUERY_TOKENS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:8]


def _snippet_around_query(text, query, max_chars=900):
    text = _compact(text)
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    lowered = text.lower()
    positions = [
        lowered.find(token.lower())
        for token in _query_tokens(query)
        if lowered.find(token.lower()) >= 0
    ]
    if not positions:
        return text[:max_chars]
    center = min(positions)
    start = max(0, center - max_chars // 3)
    end = min(len(text), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _parse_tool_plan(raw_text):
    data = _json_array_from_text(raw_text)
    if not data:
        return []
    actions = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        tool = _compact(item.get("tool"))
        query = _compact(item.get("query"))
        reason = _compact(item.get("reason"))
        if tool not in ALLOWED_REPAIR_TOOLS or not query:
            continue
        key = (tool, query)
        if key in seen:
            continue
        seen.add(key)
        actions.append({"tool": tool, "query": query[:160], "reason": reason[:240]})
        if len(actions) >= _tool_query_limit():
            break
    return actions


def _summarize_experience_search(query, combined_text, fallback_cards):
    cards = load_analysis_cards() or []
    matched = match_experience_cards(query, cards, limit=3, min_overlap=1) if cards else []
    if not matched:
        tokens = _query_tokens(query)
        matched = [
            card for card in fallback_cards
            if any(token.lower() in str(card.get("source_opinion", "")).lower() for token in tokens)
        ][:3]
    aligned = _align_experience_cards_to_current_scheme(matched, combined_text) if matched else []
    summaries = []
    for card in aligned[:3]:
        summaries.append({
            "source_project": card.get("source_project", ""),
            "work_category": card.get("work_category", ""),
            "source_opinion": card.get("source_opinion", ""),
            "alignment_status": card.get("alignment_status", ""),
            "missing_points": card.get("missing_points", []),
            "partial_points": card.get("partial_points", []),
            "scheme_gap": card.get("scheme_gap", ""),
            "generalization_rule": card.get("generalization_rule", ""),
        })
    return json.dumps(summaries, ensure_ascii=False)


def _execute_tool_plan(tool_plan, combined_text, global_cost_context, experience_cards):
    results = []
    for action in (tool_plan or [])[:_tool_query_limit()]:
        tool = action.get("tool")
        query = action.get("query", "")
        if tool not in ALLOWED_REPAIR_TOOLS or not query:
            continue
        try:
            if tool == "standards_search":
                result = retrieve_rules(query, n_results=2)
            elif tool == "experience_search":
                result = _summarize_experience_search(query, combined_text, experience_cards)
            elif tool == "scheme_snippet":
                result = _snippet_around_query(combined_text, query, max_chars=1200)
            elif tool == "cost_snippet":
                result = _snippet_around_query(global_cost_context, query, max_chars=1200) or "未上传或未解析到报价/白单/清单上下文。"
            else:
                continue
        except Exception as exc:
            result = f"[tool_error] {exc}"
        results.append({
            "tool": tool,
            "query": query,
            "reason": action.get("reason", ""),
            "result": str(result or "")[:_tool_result_chars()],
        })
    return results


def _issues_from_ai_items(data, origin="ai_final"):
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
        before_count = len(issues)
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
        if len(issues) > before_count:
            issues[-1]["origin"] = origin
    return issues


def _parse_ai_issues(raw_text, origin="ai_final"):
    data = _json_array_from_text(raw_text)
    if data is None:
        return []
    return _issues_from_ai_items(data, origin=origin)


def _ai_tool_plan(project_name, sections, local_issues, experience_cards, tool_budget, global_cost_context):
    if tool_budget <= 0:
        return []
    user_prompt = build_repair_tool_plan_user_prompt(
        project_name,
        sections,
        local_issues,
        experience_cards,
        tool_budget=tool_budget,
        cost_context_available=bool(str(global_cost_context or "").strip()),
    )
    result = call_llm(
        REPAIR_TOOL_PLAN_SYSTEM_PROMPT,
        user_prompt,
        max_retries=1,
        timeout=_safe_int_env("REPAIR_AI_REVIEW_TIMEOUT", 180),
        extra_payload={"max_tokens": min(2048, _safe_int_env("REPAIR_AI_REVIEW_MAX_TOKENS", 4096))},
        caller_label="repair_v2.plan",
    )
    return _parse_tool_plan(result)


def _ai_reasoned_issues(project_name, sections, local_issues, experience_cards, tool_context, tool_plan=None, runtime_context=None):
    user_prompt = build_repair_review_user_prompt(
        project_name,
        sections,
        local_issues,
        experience_cards,
        tool_context,
        tool_plan=tool_plan,
        runtime_context=runtime_context,
    )
    result = call_llm(
        REPAIR_REVIEW_SYSTEM_PROMPT,
        user_prompt,
        max_retries=1,
        timeout=_safe_int_env("REPAIR_AI_REVIEW_TIMEOUT", 180),
        extra_payload={"max_tokens": _safe_int_env("REPAIR_AI_REVIEW_MAX_TOKENS", 4096)},
        caller_label="repair_v2.final",
    )
    return _parse_ai_issues(result, origin="ai_final")


def _ai_critic_issues(project_name, sections, protected_local_issues, ai_issues, tool_context):
    if not ai_issues:
        return ai_issues
    user_prompt = build_repair_critic_user_prompt(project_name, sections, protected_local_issues, ai_issues, tool_context)
    result = call_llm(
        REPAIR_CRITIC_SYSTEM_PROMPT,
        user_prompt,
        max_retries=1,
        timeout=_safe_int_env("REPAIR_AI_REVIEW_TIMEOUT", 180),
        extra_payload={"max_tokens": _safe_int_env("REPAIR_AI_REVIEW_MAX_TOKENS", 4096)},
        caller_label="repair_v2.critic",
    )
    data = _json_array_from_text(result)
    if data is None:
        return ai_issues
    return _issues_from_ai_items(data, origin="ai_critic")


def _runtime_info_report(runtime):
    lines = [
        f"**AI模式**：{runtime.get('ai_mode')}",
        f"**调用预算**：{runtime.get('ai_call_budget')}",
        f"**实际非缓存LLM调用数**：{runtime.get('llm_real_calls', 0)}",
        f"**缓存命中数**：{runtime.get('llm_cache_hits', 0)}",
        f"**工具查询数**：{runtime.get('tool_query_count', 0)}",
        f"**Thinking启用**：{'是' if runtime.get('thinking_enabled') else '否'}",
        f"**复杂度评分**：{runtime.get('complexity_score', 0)}",
        f"**执行阶段**：{' -> '.join(runtime.get('stages', [])) or '本地审核'}",
    ]
    reasons = runtime.get("complexity_reasons") or []
    if reasons:
        lines.append(f"**复杂度原因**：{'；'.join(reasons)}")
    stats = runtime.get("llm_status_counts") or {}
    if stats:
        lines.append(f"**LLM状态统计**：{json.dumps(stats, ensure_ascii=False)}")
    result = "\n".join(lines)
    return {
        "agent": "审核运行信息",
        "heading": "AI与工具调用统计",
        "result": result,
        "dimension": "描述完整性",
        "work_item": "审核运行信息",
        "finding": "AI与工具调用统计",
        "reason": "用于核对本次审核是否启用 AI、工具查询和缓存。",
        "evidence_type": "方案内部逻辑",
        "evidence_ref": "系统运行记录",
        "recommendation": "如需提高质量可使用 quality 模式；如需完全停用大模型可使用 off 模式。",
        "confidence": "高",
        "origin": "runtime_info",
    }


def run_repair_pipeline(chunks_ready_for_agents, project_name, global_cost_context="", progress_callback=None, status_check_callback=None):
    ai_mode = _ai_review_mode()
    ai_budget = _ai_call_budget(ai_mode)
    llm_stats_start = current_timestamp()
    runtime_info = {
        "ai_mode": ai_mode,
        "ai_call_budget": ai_budget,
        "thinking_enabled": _thinking_enabled(),
        "tool_query_count": 0,
        "stages": [],
        "complexity_score": 0,
        "complexity_reasons": [],
    }
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
                and "轻质砖隔墙砌筑" not in line
                and "地面抬高及回填" not in line
                and "卫生间铝扣板吊顶" not in line
                and "我司主要施工内容" not in line
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
    experience_cards = _align_experience_cards_to_current_scheme(
        _matched_experience_cards(project_name, combined_text),
        combined_text,
    )
    experience_issues = _experience_issues_from_cards(experience_cards)

    issues = []
    issues.extend(local_issues)
    issues.extend(experience_issues)
    complexity_score, complexity_reasons = _complexity_score(
        combined_text,
        audit_sections,
        local_issues,
        experience_cards,
        global_cost_context,
    )
    runtime_info["complexity_score"] = complexity_score
    runtime_info["complexity_reasons"] = complexity_reasons

    if _ai_review_enabled(ai_mode, ai_budget):
        remaining_budget = ai_budget
        use_planned_tools = (
            remaining_budget >= 2
            and (
                ai_mode == "quality"
                or (ai_mode == "adaptive" and complexity_score >= 2)
            )
        )
        tool_plan = []
        tool_results = []
        if use_planned_tools:
            if progress_callback:
                progress_callback("🧭 v2零星工程审核：AI 正在规划本次工具查询。", 0.48)
            runtime_info["stages"].append("ai_tool_plan")
            tool_plan = _ai_tool_plan(
                project_name,
                audit_sections,
                issues,
                experience_cards,
                tool_budget=_tool_query_limit(),
                global_cost_context=global_cost_context,
            )
            remaining_budget -= 1
            if tool_plan:
                tool_results = _execute_tool_plan(tool_plan, combined_text, global_cost_context, experience_cards)
                runtime_info["tool_query_count"] = len(tool_results)
            else:
                runtime_info["stages"].append("tool_plan_fallback")

        tool_context = _build_tool_context(
            combined_text,
            issues,
            experience_cards,
            tool_results=tool_results,
            tool_plan=tool_plan,
        )
        if not tool_results:
            runtime_info["tool_query_count"] = len(tool_context.get("standard_snippets", []))

        ai_issues = []
        if remaining_budget > 0:
            if progress_callback:
                progress_callback("🧠 v2零星工程审核：开始 AI 归因泛化和最终判断。", 0.65)
            runtime_info["stages"].append("ai_final")
            ai_issues = _ai_reasoned_issues(
                project_name,
                audit_sections,
                issues,
                experience_cards,
                tool_context,
                tool_plan=tool_plan,
                runtime_context={
                    "ai_mode": ai_mode,
                    "complexity_score": complexity_score,
                    "complexity_reasons": complexity_reasons,
                },
            )
            remaining_budget -= 1

        if ai_mode == "quality" and remaining_budget > 0 and ai_issues:
            if progress_callback:
                progress_callback("🧪 v2零星工程审核：开始 AI 质量复核，压制偏题和无证据结论。", 0.82)
            runtime_info["stages"].append("ai_critic")
            ai_issues = _ai_critic_issues(project_name, audit_sections, issues, ai_issues, tool_context)
            remaining_budget -= 1

        issues.extend(ai_issues)
    else:
        tool_context = {"methodology": {"core_dimensions": list(CORE_DIMENSIONS)}, "standard_snippets": []}
        runtime_info["stages"].append("local_only")

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

    issues = _prefer_control_point_issues(issues)
    issues = _dedupe_issues(issues)
    llm_stats = call_stats_since(llm_stats_start, caller_prefix="repair_v2.")
    runtime_info["llm_real_calls"] = llm_stats.get("real_calls", 0)
    runtime_info["llm_cache_hits"] = llm_stats.get("cache_hits", 0)
    runtime_info["llm_status_counts"] = llm_stats.get("by_status", {})
    if progress_callback:
        progress_callback(f"✅ v2零星工程审核：生成 {len(issues)} 条分项问题。", 0.95)
    grouped = _group_issues(issues)
    grouped["审核运行信息"] = [_runtime_info_report(runtime_info)]
    return grouped
