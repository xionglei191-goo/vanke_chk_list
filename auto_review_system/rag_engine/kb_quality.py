"""
Local quality scoring for knowledge-base rules.

The goal is to remove obvious retrieval noise without calling an LLM and without
deleting history. Critical noise is retired by marking status=inactive.
"""
import re

NORMATIVE_RE = re.compile(
    r"应|不得|必须|严禁|宜|符合|检验|验收|检查|施工|质量|安装|合格|允许偏差|试验|检测|记录|防水|安全|管道|混凝土"
)
PUBLISH_INFO_RE = re.compile(r"版权所有|统一书号|出版社|出版|印刷|定价|开本|网址|网上书店|发行")
ROSTER_RE = re.compile(r"主要起草人员|主要审查人员|参编单位|主编单位|参加单位|编制组")
TOC_RE = re.compile(r"目\s*次|Contents|本规范用词说明|本标准用词说明|引用标准名录|条文说明")
PLACEHOLDER_RE = re.compile(r"×××|XXX|xxxx|主要安全控制要点XXX", re.I)
FRONTMATTER_RE = re.compile(r"前\s*言|公告|主编部门|批准部门|施行日期|中华人民共和国住房和城乡建设部")
DOT_LEADER_RE = re.compile(r"[·•.．。]{6,}|…{2,}")

CRITICAL_FLAGS = {
    "too_short",
    "symbol_toc",
    "publish_info_only",
    "people_roster",
}


def _compact(text):
    return re.sub(r"\s+", "", str(text or ""))


def _symbol_ratio(text):
    compact = _compact(text)
    if not compact:
        return 1.0
    useful = sum(1 for ch in compact if "\u4e00" <= ch <= "\u9fff" or ch.isalnum())
    return 1.0 - useful / max(1, len(compact))


def assess_rule_quality(rule):
    text = str(rule.get("content") or "")
    compact = _compact(text)
    has_normative = bool(NORMATIVE_RE.search(text))
    flags = []
    notes = []
    score = 100

    if len(compact) < 40:
        flags.append("too_short")
        notes.append("有效文本过短")
        score -= 80

    symbol_ratio = _symbol_ratio(text)
    if symbol_ratio > 0.45:
        flags.append("mostly_symbols")
        notes.append(f"符号占比过高({symbol_ratio:.0%})")
        score -= 35

    if TOC_RE.search(text) and (DOT_LEADER_RE.search(text) or symbol_ratio > 0.28) and not has_normative:
        flags.append("symbol_toc")
        notes.append("目录/页码残片")
        score -= 75

    if PLACEHOLDER_RE.search(text):
        flags.append("placeholder_text")
        if has_normative:
            notes.append("含占位符样式文本，但上下文仍包含规范要求")
            score -= 15
        else:
            notes.append("含占位符或明显 OCR 占位残留")
            score -= 75

    if PUBLISH_INFO_RE.search(text) and not has_normative:
        flags.append("publish_info_only")
        notes.append("出版/版权信息，无审查规则价值")
        score -= 70

    if ROSTER_RE.search(text) and not has_normative:
        flags.append("people_roster")
        notes.append("起草/审查人员名单，无审查规则价值")
        score -= 70

    if FRONTMATTER_RE.search(text) and not has_normative:
        flags.append("frontmatter")
        notes.append("前置信息，建议降权或停用")
        score -= 25

    if rule.get("index_source") == "pageindex":
        score += 8
        notes.append("PageIndex 语义节点加分")

    score = max(0, min(100, score))
    flags = sorted(set(flags))
    critical = bool(CRITICAL_FLAGS.intersection(flags)) or score <= 35
    return {
        "score": score,
        "flags": flags,
        "notes": "；".join(notes),
        "critical": critical,
    }
