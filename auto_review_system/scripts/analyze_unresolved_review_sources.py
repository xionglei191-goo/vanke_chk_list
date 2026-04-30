#!/usr/bin/env python3
"""
Analyze review opinions whose source-scheme alignment is still unresolved.

Generated outputs are written under auto_review_system/data/analysis, which is
ignored because it contains project names and review opinions.
"""
import argparse
import csv
import datetime as dt
import difflib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_DIR = os.path.dirname(APP_DIR)
for path in (APP_DIR, PROJECT_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from rag_engine.review_experience import (  # noqa: E402
    ANALYSIS_DIR,
    DEFAULT_MATERIAL_DIR,
    DEFAULT_OPINION_FILE,
    enrich_rows_with_scheme_evidence,
    load_opinion_rows,
)

UNRESOLVED_STATUS = "无法判断"
GENERIC_NAME_WORDS = {
    "广州", "万科", "工程", "报价", "清单", "施工", "方案", "项目", "维修", "改造",
    "翻新", "零星", "花园", "小区", "专项", "二类", "一类",
}


def _clean(value):
    return str(value or "").strip()


def _normalized_name(value):
    text = _clean(value)
    text = re.sub(r"F\d{2}", "", text, flags=re.I)
    text = re.sub(r"\d{6,8}", "", text)
    text = re.sub(r"[（(].*?[）)]", "", text)
    for word in GENERIC_NAME_WORDS:
        text = text.replace(word, "")
    text = re.sub(r"[\s_+\-—、，,：:（）()]+", "", text)
    return text


def _match_quality(row):
    if row.get("source_match_quality_label"):
        return {
            "label": row.get("source_match_quality_label"),
            "score": row.get("source_match_quality_score", 0.0),
        }
    if row.get("source_match_type") == "manifest":
        return {"label": "人工映射", "score": 1.0}
    matched_file = row.get("matched_file")
    if not matched_file:
        return {"label": "未匹配", "score": 0.0}
    project = _normalized_name(row.get("project_name", ""))
    matched = _normalized_name(Path(matched_file).stem)
    if not project or not matched:
        return {"label": "需人工确认", "score": 0.0}
    score = difflib.SequenceMatcher(None, project, matched).ratio()
    if project in matched or matched in project:
        label = "高"
    elif score >= 0.68:
        label = "中"
    else:
        label = "低，疑似错配"
    return {"label": label, "score": round(score, 3)}


def _reason(row):
    if not row.get("matched_file"):
        return "未匹配到本地原始材料文件"
    if not row.get("scheme_evidence"):
        if _match_quality(row)["label"] == "低，疑似错配":
            return "低质量模糊匹配，疑似错配"
        return "已匹配文件但未定位到意见触发片段"
    if not row.get("checkpoint_assessments"):
        return "意见缺少可机械拆解的控制点"
    return "证据不足，需要人工回看原方案"


def _needed_source(row):
    reason = _reason(row)
    if reason == "未匹配到本地原始材料文件":
        if row.get("project_type") == "报价/白单":
            return "请提供该项目的报价/白单及对应施工方案，优先提供专家审核时使用的原始版本。"
        return "请提供该项目专家审核时使用的原始方案/附件，或确认文件名与项目名的对应关系。"
    if reason == "已匹配文件但未定位到意见触发片段":
        return "本地已有相近文件，但未找到触发片段；请确认是否还有旧版方案、报价白单、附件或文件名口径不同的材料。"
    if reason == "低质量模糊匹配，疑似错配":
        return "当前候选文件只因名称相近被模糊匹配，且未找到触发片段；请提供该意见真正对应的原始方案、报价白单或附件。"
    if reason == "意见缺少可机械拆解的控制点":
        return "请补充该条意见的上下文或专家批注，便于拆成可复用控制点。"
    return "请人工回看原方案触发位置，并补充对应页/章节。"


def _counter_dict(counter):
    return {key: value for key, value in counter.most_common()}


def _read_existing_manifest_paths(manifest_file):
    manifest_file = str(manifest_file or "").strip()
    if not manifest_file:
        return {}
    path = Path(os.path.expanduser(manifest_file))
    if not path.is_absolute():
        path = Path(PROJECT_DIR) / path
    if not path.exists():
        return {}

    existing = {}
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for project_name, value in payload.items():
                if isinstance(value, dict):
                    value = value.get("user_supplied_path") or value.get("source_path") or value.get("path")
                if project_name and value:
                    existing[_clean(project_name)] = _clean(value)
        elif isinstance(payload, list):
            for row in payload:
                if not isinstance(row, dict):
                    continue
                project_name = _clean(row.get("project_name"))
                value = _clean(row.get("user_supplied_path") or row.get("source_path") or row.get("path"))
                if project_name and value:
                    existing[project_name] = value
        return existing

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            project_name = _clean(row.get("project_name"))
            value = _clean(row.get("user_supplied_path") or row.get("source_path") or row.get("path"))
            if project_name and value:
                existing[project_name] = value
    return existing


def build_report(rows):
    total_counter = Counter(row.get("alignment_status", "") for row in rows)
    unresolved = [row for row in rows if row.get("alignment_status") == UNRESOLVED_STATUS]
    project_totals = Counter(row.get("project_name", "") for row in rows)

    projects = defaultdict(list)
    for row in unresolved:
        projects[row.get("project_name", "")].append(row)

    project_rows = []
    for project_name, items in sorted(projects.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        reason_counts = Counter(_reason(item) for item in items)
        matched_files = sorted({_clean(item.get("matched_file")) for item in items if item.get("matched_file")})
        project_rows.append({
            "project_name": project_name,
            "unresolved_count": len(items),
            "total_opinion_items": project_totals[project_name],
            "project_types": _counter_dict(Counter(item.get("project_type", "") for item in items)),
            "matched_files": matched_files,
            "match_quality": _counter_dict(Counter(_match_quality(item)["label"] for item in items)),
            "reason_counts": _counter_dict(reason_counts),
            "needed_source": _needed_source(items[0]),
            "items": [
                {
                    "row_index": item.get("row_index"),
                    "item_index": item.get("item_index"),
                    "engineer": item.get("engineer", ""),
                    "opinion": item.get("opinion", ""),
                    "project_type": item.get("project_type", ""),
                    "matched_file": item.get("matched_file", ""),
                    "matched_file_path": item.get("matched_file_path", ""),
                    "source_match_type": item.get("source_match_type", ""),
                    "match_quality": _match_quality(item),
                    "file_type": item.get("file_type", ""),
                    "work_category": item.get("work_category", ""),
                    "dimension": item.get("dimension", ""),
                    "professional_attribution": item.get("professional_attribution_label", ""),
                    "reason": _reason(item),
                    "needed_source": _needed_source(item),
                    "scheme_gap": item.get("scheme_gap", ""),
                    "required_artifacts": item.get("required_artifacts", []),
                    "review_questions": item.get("review_questions", []),
                }
                for item in items
            ],
        })

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "total_opinion_items": len(rows),
        "alignment_statuses": _counter_dict(total_counter),
        "unresolved_count": len(unresolved),
        "unresolved_ratio": round(len(unresolved) / max(1, len(rows)), 4),
        "summary": {
            "by_reason": _counter_dict(Counter(_reason(row) for row in unresolved)),
            "by_project_type": _counter_dict(Counter(row.get("project_type", "") for row in unresolved)),
            "by_file_type": _counter_dict(Counter(row.get("file_type", "") or "未匹配" for row in unresolved)),
            "by_match_quality": _counter_dict(Counter(_match_quality(row)["label"] for row in unresolved)),
            "by_dimension": _counter_dict(Counter(row.get("dimension", "") for row in unresolved)),
            "by_work_category": _counter_dict(Counter(row.get("work_category", "") for row in unresolved)),
            "by_professional_attribution": _counter_dict(Counter(row.get("professional_attribution_label", "") for row in unresolved)),
        },
        "project_count": len(project_rows),
        "projects": project_rows,
    }


def render_markdown(report):
    lines = [
        "# 无法判断审核意见来源分析",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 原子审核意见：{report['total_opinion_items']}",
        f"- 无法判断：{report['unresolved_count']} ({report['unresolved_ratio']:.1%})",
        f"- 涉及项目：{report['project_count']}",
        "",
        "## 缺口类型",
    ]
    for reason, count in report["summary"]["by_reason"].items():
        lines.append(f"- {reason}: {count}")

    lines += ["", "## 资料类型分布"]
    for project_type, count in report["summary"]["by_project_type"].items():
        lines.append(f"- {project_type}: {count}")

    lines += ["", "## 文件匹配质量"]
    for quality, count in report["summary"]["by_match_quality"].items():
        lines.append(f"- {quality}: {count}")

    lines += ["", "## 工程类别分布"]
    for category, count in report["summary"]["by_work_category"].items():
        lines.append(f"- {category}: {count}")

    lines += [
        "",
        "## 需要补原始方案的项目清单",
        "",
        "| 项目 | 无法判断/总意见 | 资料类型 | 当前匹配文件 | 匹配质量 | 主要缺口 | 需要补充 |",
        "| --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for project in report["projects"]:
        project_types = "；".join(f"{k}:{v}" for k, v in project["project_types"].items())
        matched_files = "<br>".join(project["matched_files"]) if project["matched_files"] else "未匹配"
        qualities = "；".join(f"{k}:{v}" for k, v in project["match_quality"].items())
        reasons = "；".join(f"{k}:{v}" for k, v in project["reason_counts"].items())
        lines.append(
            f"| {project['project_name']} | {project['unresolved_count']}/{project['total_opinion_items']} | "
            f"{project_types} | {matched_files} | {qualities} | {reasons} | {project['needed_source']} |"
        )

    lines += ["", "## 逐条明细"]
    for project in report["projects"]:
        lines += [
            "",
            f"### {project['project_name']}",
            f"- 无法判断/总意见：{project['unresolved_count']}/{project['total_opinion_items']}",
            f"- 当前匹配文件：{'、'.join(project['matched_files']) if project['matched_files'] else '未匹配'}",
            f"- 匹配质量：{json.dumps(project['match_quality'], ensure_ascii=False)}",
            f"- 需要补充：{project['needed_source']}",
            "",
        ]
        for item in project["items"]:
            lines += [
                f"- 行{item['row_index']}.{item['item_index']}｜{item['project_type']}｜{item['dimension']}｜{item['work_category']}",
                f"  - 审核意见：{item['opinion']}",
                f"  - 缺口：{item['reason']}；{item['scheme_gap']}",
            ]
    return "\n".join(lines) + "\n"


def write_csv_manifest(report, path, existing_paths=None):
    existing_paths = existing_paths or {}
    fieldnames = [
        "project_name",
        "unresolved_count",
        "total_opinion_items",
        "project_types",
        "matched_files",
        "matched_file_paths",
        "match_quality",
        "reason_counts",
        "needed_source",
        "user_supplied_path",
        "notes",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for project in report["projects"]:
            writer.writerow({
                "project_name": project["project_name"],
                "unresolved_count": project["unresolved_count"],
                "total_opinion_items": project["total_opinion_items"],
                "project_types": json.dumps(project["project_types"], ensure_ascii=False),
                "matched_files": "；".join(project["matched_files"]),
                "matched_file_paths": "；".join(
                    sorted({
                        _clean(item.get("matched_file_path"))
                        for item in project["items"]
                        if item.get("matched_file_path")
                    })
                ),
                "match_quality": json.dumps(project["match_quality"], ensure_ascii=False),
                "reason_counts": json.dumps(project["reason_counts"], ensure_ascii=False),
                "needed_source": project["needed_source"],
                "user_supplied_path": existing_paths.get(project["project_name"], ""),
                "notes": "",
            })


def main():
    parser = argparse.ArgumentParser(description="Analyze unresolved raw review-source alignment.")
    parser.add_argument("--opinion-file", default=DEFAULT_OPINION_FILE)
    parser.add_argument("--material-dir", default=DEFAULT_MATERIAL_DIR)
    parser.add_argument("--output-dir", default=ANALYSIS_DIR)
    parser.add_argument(
        "--source-manifest",
        default="",
        help="Optional CSV/JSON mapping project_name to user_supplied_path.",
    )
    args = parser.parse_args()

    existing_paths = _read_existing_manifest_paths(args.source_manifest)
    rows = load_opinion_rows(args.opinion_file, args.material_dir, source_manifest=args.source_manifest or None)
    rows = enrich_rows_with_scheme_evidence(rows, args.material_dir)
    report = build_report(rows)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "unresolved_review_sources.json"
    md_path = output_dir / "unresolved_review_sources.md"
    csv_path = output_dir / "unresolved_review_source_manifest.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    write_csv_manifest(report, csv_path, existing_paths=existing_paths)

    print("Unresolved source analysis complete")
    print(f"- unresolved: {report['unresolved_count']} / {report['total_opinion_items']}")
    print(f"- projects: {report['project_count']}")
    print(f"- report: {md_path}")
    print(f"- json: {json_path}")
    print(f"- manifest: {csv_path}")
    print("- reasons:")
    for reason, count in report["summary"]["by_reason"].items():
        print(f"  {reason}: {count}")


if __name__ == "__main__":
    main()
