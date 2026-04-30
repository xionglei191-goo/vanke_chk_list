#!/usr/bin/env python3
"""
Run deterministic v2 repair-audit benchmark cases against raw material samples.

Outputs are written under auto_review_system/data/analysis, which is ignored
because it can contain business project names and historical review detail.
"""
import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_DIR = os.path.dirname(APP_DIR)
for path in (APP_DIR, PROJECT_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from auditors.repair_scheme_engine import run_repair_pipeline  # noqa: E402
from rag_engine.review_experience import (  # noqa: E402
    ANALYSIS_DIR,
    DEFAULT_MATERIAL_DIR,
    _material_text_lines,
)


CASE_SPECS = [
    {
        "case_label": "地坪/EPDM样本",
        "hints": ["EPDM", "塑胶", "水沟", "固化"],
        "expected_keywords": ["EPDM", "胶水配比", "固化", "基层验收", "水沟", "成品保护"],
        "suppressed_keywords": [],
        "forbidden_keywords": ["安全文明施工费", "品牌违约", "超高降效"],
    },
    {
        "case_label": "宿舍/结构改造样本",
        "hints": ["反坎", "抹灰", "植筋", "错孔", "结构隔层"],
        "expected_keywords": ["反坎", "抹灰", "轻质砂浆", "植筋", "错孔", "腻子"],
        "suppressed_keywords": [],
        "forbidden_keywords": ["安全文明施工费", "品牌违约", "超高降效"],
    },
    {
        "case_label": "户外/电梯/活动室综合改造样本",
        "hints": ["电梯", "大理石", "钢化玻璃", "活动室", "油漆", "角铁", "方通"],
        "expected_keywords": ["C2TE", "干铺", "六面防护", "3C", "油漆"],
        "suppressed_keywords": ["角铁", "方通"],
        "forbidden_keywords": ["安全文明施工费", "品牌违约", "超高降效"],
    },
]
DEFAULT_CASES_FILE = Path(ANALYSIS_DIR) / "repair_benchmark_cases.local.json"


def _case_chunk(file_path):
    lines = _material_text_lines(file_path)
    text = "\n".join(f"{line['location']} | {line['text']}" for line in lines)
    return [{"heading": Path(file_path).stem, "text": text}]


def _flatten_reports(reports):
    rows = []
    for work_item, items in reports.items():
        for item in items:
            copied = dict(item)
            copied["group"] = work_item
            rows.append(copied)
    return rows


def _file_text(file_path):
    try:
        lines = _material_text_lines(file_path)
    except Exception as exc:
        return "", str(exc)
    return "\n".join(f"{line['location']} | {line['text']}" for line in lines), ""


def _score_file(file_path, spec, text):
    haystack = f"{Path(file_path).stem}\n{text}"
    return sum(haystack.count(keyword) for keyword in spec["hints"])


def discover_benchmark_cases(material_dir):
    material_path = Path(material_dir)
    files = sorted(
        path for path in material_path.glob("*.xlsx")
        if "审核意见" not in path.name and not path.name.startswith("~$")
    )
    text_cache = {}
    cases = []
    used_files = set()
    for spec in CASE_SPECS:
        best_path = None
        best_score = 0
        best_text = ""
        best_error = ""
        for file_path in files:
            if file_path in used_files:
                continue
            if file_path not in text_cache:
                text_cache[file_path] = _file_text(file_path)
            text, error = text_cache[file_path]
            if error:
                continue
            score = _score_file(file_path, spec, text)
            if score > best_score:
                best_path = file_path
                best_score = score
                best_text = text
                best_error = error
        if best_path and not best_error:
            used_files.add(best_path)
            cases.append({
                "case_label": spec["case_label"],
                "project_name": best_path.stem,
                "file_name": best_path.name,
                "expected_keywords": spec["expected_keywords"],
                "suppressed_keywords": spec.get("suppressed_keywords", []),
                "forbidden_keywords": spec.get("forbidden_keywords", []),
                "discovery_score": best_score,
                "text_preview": best_text[:200],
            })
    return cases


def load_benchmark_cases(material_dir, cases_file=None):
    case_path = Path(cases_file) if cases_file else DEFAULT_CASES_FILE
    if case_path.exists():
        return json.loads(case_path.read_text(encoding="utf-8"))
    return discover_benchmark_cases(material_dir)


def _run_case(case, material_dir, with_ai=False):
    old_ai = os.environ.get("REPAIR_AI_REVIEW_ENABLED")
    old_experience = os.environ.get("REVIEW_EXPERIENCE_ENABLED")
    if not with_ai:
        os.environ["REPAIR_AI_REVIEW_ENABLED"] = "false"
    os.environ["REVIEW_EXPERIENCE_ENABLED"] = "true"
    try:
        file_path = Path(material_dir) / case["file_name"]
        if not file_path.exists():
            return {
                "case_label": case.get("case_label", ""),
                "project_name": case["project_name"],
                "file_name": case["file_name"],
                "error": f"file_not_found: {file_path}",
            }
        reports = run_repair_pipeline(_case_chunk(file_path), case["project_name"])
        issues = _flatten_reports(reports)
        output_text = "\n\n".join(issue.get("result", "") for issue in issues)
        expected_hits = [keyword for keyword in case["expected_keywords"] if keyword in output_text]
        expected_missing = [keyword for keyword in case["expected_keywords"] if keyword not in output_text]
        suppressed_hits = [keyword for keyword in case.get("suppressed_keywords", []) if keyword in output_text]
        forbidden_hits = [keyword for keyword in case["forbidden_keywords"] if keyword in output_text]
        control_point_issues = [issue for issue in issues if issue.get("checkpoint_assessments")]
        return {
            "case_label": case.get("case_label", ""),
            "project_name": case["project_name"],
            "file_name": case["file_name"],
            "issue_count": len(issues),
            "control_point_issue_count": len(control_point_issues),
            "expected_hits": expected_hits,
            "expected_missing": expected_missing,
            "suppressed_hits": suppressed_hits,
            "forbidden_hits": forbidden_hits,
            "issues": issues,
        }
    finally:
        if old_ai is None:
            os.environ.pop("REPAIR_AI_REVIEW_ENABLED", None)
        else:
            os.environ["REPAIR_AI_REVIEW_ENABLED"] = old_ai
        if old_experience is None:
            os.environ.pop("REVIEW_EXPERIENCE_ENABLED", None)
        else:
            os.environ["REVIEW_EXPERIENCE_ENABLED"] = old_experience


def render_markdown(results):
    lines = [
        "# v2零星工程审核基准报告",
        "",
        "说明：本报告默认关闭 AI，仅验证本地规则、历史经验泛化和控制点输出，避免产生 API 调用费用。",
        "",
    ]
    for result in results:
        lines += [
            f"## {result.get('case_label') or result.get('project_name')}",
            f"- 项目：{result.get('project_name')}",
            f"- 文件：{result.get('file_name')}",
        ]
        if result.get("error"):
            lines.append(f"- 错误：{result['error']}")
            lines.append("")
            continue
        lines += [
            f"- 问题数：{result.get('issue_count', 0)}",
            f"- 带控制点判断的问题数：{result.get('control_point_issue_count', 0)}",
            f"- 期望命中：{'、'.join(result.get('expected_hits', [])) or '无'}",
            f"- 期望缺失：{'、'.join(result.get('expected_missing', [])) or '无'}",
            f"- 已补齐应抑制但仍输出：{'、'.join(result.get('suppressed_hits', [])) or '无'}",
            f"- 禁止项命中：{'、'.join(result.get('forbidden_hits', [])) or '无'}",
            "",
            "### 输出摘录",
        ]
        for issue in result.get("issues", [])[:8]:
            first_line = issue.get("result", "").splitlines()[0] if issue.get("result") else ""
            control_mark = "（含控制点判断）" if issue.get("checkpoint_assessments") else ""
            lines.append(f"- {issue.get('work_item', '')}｜{issue.get('dimension', '')}{control_mark}：{first_line}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Run deterministic v2 repair-audit benchmark cases.")
    parser.add_argument("--material-dir", default=DEFAULT_MATERIAL_DIR)
    parser.add_argument("--output-dir", default=ANALYSIS_DIR)
    parser.add_argument("--cases-file", default="", help="Optional local JSON benchmark case list.")
    parser.add_argument("--with-ai", action="store_true", help="Allow the optional single v2 AI review call.")
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 even when benchmark expectations fail.")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    cases = load_benchmark_cases(args.material_dir, args.cases_file or None)
    if not cases:
        print("No benchmark cases found. Put raw samples under 原始材料/ or provide --cases-file.")
        if args.no_fail:
            return
        raise SystemExit(1)

    results = [_run_case(case, args.material_dir, with_ai=args.with_ai) for case in cases]

    json_path = Path(args.output_dir) / "repair_v2_benchmark_results.json"
    md_path = Path(args.output_dir) / "repair_v2_benchmark_report.md"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(results), encoding="utf-8")

    print("Repair benchmark complete")
    print(f"- results: {json_path}")
    print(f"- report: {md_path}")
    for result in results:
        status = "ERROR" if result.get("error") else "OK"
        print(
            f"- {status} {result.get('project_name')}: "
            f"issues={result.get('issue_count', 0)} "
            f"control={result.get('control_point_issue_count', 0)} "
            f"missing={len(result.get('expected_missing', []))} "
            f"suppressed={len(result.get('suppressed_hits', []))} "
            f"forbidden={len(result.get('forbidden_hits', []))}"
        )
    failed = [
        result for result in results
        if result.get("error")
        or result.get("expected_missing")
        or result.get("suppressed_hits")
        or result.get("forbidden_hits")
    ]
    if failed and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
