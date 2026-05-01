#!/usr/bin/env python3
"""Run a v3 smoke review on a local raw-material sample."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent
PROJECT_DIR = APP_DIR.parent
for path in (str(APP_DIR), str(PROJECT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from auditors.v3_ai_review import run_v3_pipeline  # noqa: E402
from parsers.excel_parser import parse_excel_as_scheme_chunks  # noqa: E402
from parsers.word_parser import parse_word_doc_structured  # noqa: E402

DEFAULT_SAMPLE = PROJECT_DIR / "原始材料" / "方案评审" / "广州幸福誉花园J9-J14前游乐场塑胶地面翻新工程施工方案.xlsx"


def _chunks_for(path):
    ext = path.suffix.lower()
    if ext == ".xlsx":
        return parse_excel_as_scheme_chunks(str(path))
    if ext == ".docx":
        return parse_word_doc_structured(str(path))
    raise SystemExit(f"unsupported sample type: {path}")


def main():
    sample = Path(os.getenv("V3_SMOKE_SAMPLE", str(DEFAULT_SAMPLE)))
    if not sample.exists():
        raise SystemExit(f"sample not found: {sample}")
    chunks = _chunks_for(sample)
    reports = run_v3_pipeline(chunks, sample.stem)
    output_dir = APP_DIR / "data" / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"v3_smoke_{ts}.json"
    md_path = output_dir / f"v3_smoke_{ts}.md"
    json_path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# v3 smoke review: {sample.stem}", ""]
    for group, items in reports.items():
        lines += [f"## {group}", ""]
        for item in items:
            lines += [f"### {item.get('agent', '')}", "", item.get("result", ""), ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"json: {json_path}")
    print(f"md: {md_path}")
    for group, items in reports.items():
        print(f"- {group}: {len(items)}")


if __name__ == "__main__":
    main()
