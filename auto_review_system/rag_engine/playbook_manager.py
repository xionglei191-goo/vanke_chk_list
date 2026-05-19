"""Build and maintain the runtime v3 review playbook."""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = APP_DIR / "data" / "analysis" / "deep_alignment_benchmark_report.md"
DEFAULT_OUTPUT = APP_DIR / "data" / "analysis" / "repair_review_playbook.md"


def _unique(items, limit):
    seen = set()
    result = []
    for item in items:
        item = re.sub(r"\s+", " ", item).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def build_playbook(source_text, approved_feedback=""):
    intents = []
    rules = []
    risks = []
    checkpoints = []
    examples = []
    current_example = []
    for line in source_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            if current_example:
                examples.append("\n".join(current_example[:14]))
            current_example = [stripped]
            continue
        if current_example and (
            stripped.startswith("- 维度/类别：")
            or stripped.startswith("- 对齐状态：")
            or stripped.startswith("- 专家真实意图：")
            or stripped.startswith("- 缺口原因：")
            or stripped.startswith("- 泛化规则：")
        ):
            current_example.append(stripped)
        if stripped.startswith("- 专家真实意图："):
            intents.append(stripped.removeprefix("- 专家真实意图："))
        elif stripped.startswith("- 泛化规则："):
            rules.append(stripped.removeprefix("- 泛化规则："))
        elif stripped.startswith("- 忽略风险："):
            risks.append(stripped.removeprefix("- 忽略风险："))
        elif re.match(r"- [^：]{2,30}：(?:未覆盖|笼统提及|具体覆盖)", stripped):
            checkpoints.append(stripped[2:])
    if current_example:
        examples.append("\n".join(current_example[:14]))

    lines = [
        "# v3零星工程审核经验教训手册",
        "",
        "## 审核目的",
        "- 判断班组长编写的小方案能否指导施工、计价、验收和复核。",
        "- 不做大而全施工组织设计审查，不输出安全文明费、品牌违约、超高降效等偏题套话。",
        "- 历史经验只作为专家追问方式，不能机械照搬到当前方案。",
        "- 每条结论必须回到当前方案证据；证据不足只能列为需复核/需补资料。",
        "",
        "## 四个核心维度",
        "- 描述完整性：材料、规格、参数、现场条件、验收指标是否闭合。",
        "- 工艺合理性：材料系统、基层、环境、功能和耐久是否匹配。",
        "- 分项拆分：拆除、修复、恢复、隐蔽、检测、保护、报价口径是否拆清。",
        "- 逻辑自洽：工序、养护、交接、尺寸、数量、材料名词和验收口径是否冲突。",
        "- 细部可施工性：边、角、缝、洞口、管根、倒角、新旧交接、遮蔽保护和相邻分项界面是否写到班组能照着做。",
        "",
        "## 必查追问清单",
        "- 看到面层翻新，不只查主材，还要追问基层验收、开放使用、边界收口、污染控制和成品保护。",
        "- 看到石凳、台阶、挡墙、墙角、洞口、管根、门窗边、水沟边等细部，必须追问倒角、收边、接缝顺直、遮蔽保护和验收方法。",
        "- 看到相邻分项交接，必须追问先后顺序、保护措施、是否会返工、是否会污染或破坏已完工程。",
        "- 看到报价/白单中的分项，而方案正文没有对应施工做法，应列为分项拆分或方案清单一致性问题。",
        "",
        "## 专家常用追问",
    ]
    lines.extend(f"- {item}" for item in _unique(intents, 24))
    lines += ["", "## 可迁移审核原则"]
    lines.extend(f"- {item}" for item in _unique(rules, 32))
    lines += ["", "## 忽略后的典型风险"]
    lines.extend(f"- {item}" for item in _unique(risks, 20))
    lines += ["", "## 控制点判断示例"]
    lines.extend(f"- {item}" for item in _unique(checkpoints, 32))
    lines += ["", "## Few-shot 案例摘录"]
    for example in _unique(examples, 8):
        lines += ["", example]
    if approved_feedback:
        lines += ["", approved_feedback.strip()]
    return "\n".join(lines).strip() + "\n"


def read_playbook(path=None) -> str:
    target = Path(path or DEFAULT_OUTPUT)
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8", errors="ignore")


def write_playbook(text: str, path=None) -> dict:
    target = Path(path or DEFAULT_OUTPUT)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(text or "").strip() + "\n", encoding="utf-8")
    return playbook_info(target)


def playbook_info(path=None) -> dict:
    target = Path(path or DEFAULT_OUTPUT)
    if not target.exists():
        return {
            "path": str(target),
            "exists": False,
            "size": 0,
            "chars": 0,
            "mtime": "",
            "sections": 0,
        }
    text = target.read_text(encoding="utf-8", errors="ignore")
    return {
        "path": str(target),
        "exists": True,
        "size": target.stat().st_size,
        "chars": len(text),
        "mtime": _dt.datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "sections": len(re.findall(r"^##\s+", text, flags=re.MULTILINE)),
    }


def rebuild_playbook(source_path=None, output_path=None, approved_feedback="") -> dict:
    source = Path(source_path or DEFAULT_SOURCE)
    output = Path(output_path or DEFAULT_OUTPUT)
    if not source.exists():
        return {
            "ok": False,
            "error": f"source not found: {source}",
            "source_path": str(source),
            "output_path": str(output),
        }
    source_text = source.read_text(encoding="utf-8", errors="ignore")
    playbook = build_playbook(source_text, approved_feedback=approved_feedback)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(playbook, encoding="utf-8")
    info = playbook_info(output)
    info.update({
        "ok": True,
        "source_path": str(source),
        "output_path": str(output),
        "approved_feedback_chars": len(approved_feedback or ""),
    })
    return info
