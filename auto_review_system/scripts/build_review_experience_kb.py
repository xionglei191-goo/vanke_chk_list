#!/usr/bin/env python3
"""
Analyze raw expert review material and optionally publish structured experience
cards into the knowledge base.

Detailed outputs are written to auto_review_system/data/analysis, which is
ignored by git because it contains business material.
"""
import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_DIR = os.path.dirname(APP_DIR)
for path in (APP_DIR, PROJECT_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from rag_engine import kb_manager, kb_store  # noqa: E402
from rag_engine.review_experience import (  # noqa: E402
    ANALYSIS_DIR,
    DEFAULT_MATERIAL_DIR,
    DEFAULT_OPINION_FILE,
    build_experience_cards,
    card_to_kb_rule,
    enrich_rows_with_scheme_evidence,
    load_opinion_rows,
    summarize_rows,
    write_analysis_outputs,
)


def _retire_raw_table_rules(rules):
    retired = 0
    for rule in rules:
        if (
            rule.get("source_file") == "城市公司检查结果"
            or rule.get("category") == "城市公司检查结果"
            or "【城市公司检查结果 - Excel表格结构拆解" in str(rule.get("content") or "")
        ):
            if rule.get("status") != "inactive":
                retired += 1
            rule["status"] = "inactive"
            rule["retired_reason"] = "历史审核意见整表灌入，已由结构化 review_experience 经验卡替代"
            rule["retired_by_index_source"] = "review_experience"
    return retired


def apply_cards_to_kb(cards):
    current_rules = kb_store.get_all_rules(status_filter=None)
    retired_count = _retire_raw_table_rules(current_rules)
    current_rules = [rule for rule in current_rules if rule.get("index_source") != "review_experience"]
    experience_rules = [card_to_kb_rule(card) for card in cards]
    merged = current_rules + experience_rules
    ok, message = kb_manager.replace_all_rules(merged, rebuild_vector=True)
    if not ok:
        raise RuntimeError(message)
    return {
        "retired_raw_table_rules": retired_count,
        "experience_rules": len(experience_rules),
        "message": message,
    }


def main():
    parser = argparse.ArgumentParser(description="Build structured review-experience cards from raw materials.")
    parser.add_argument("--opinion-file", default=DEFAULT_OPINION_FILE)
    parser.add_argument("--material-dir", default=DEFAULT_MATERIAL_DIR)
    parser.add_argument("--output-dir", default=ANALYSIS_DIR)
    parser.add_argument("--scope", choices=["scheme-priority", "scheme-only", "all"], default="scheme-priority")
    parser.add_argument("--apply", action="store_true", help="Publish cards into SQLite/JSON/Chroma knowledge base.")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only; never write the knowledge base.")
    parser.add_argument("--max-items", type=int, default=0, help="Limit rows for debugging.")
    args = parser.parse_args()

    rows = load_opinion_rows(args.opinion_file, args.material_dir)
    if args.max_items:
        rows = rows[: args.max_items]
    rows = enrich_rows_with_scheme_evidence(rows, args.material_dir)
    cards = build_experience_cards(rows, scope=args.scope)
    outputs = write_analysis_outputs(rows, cards, output_dir=args.output_dir)
    summary = summarize_rows(rows)

    print("Raw material analysis complete")
    print(f"- opinion_items: {summary['total_items']}")
    print(f"- projects: {summary['projects']}")
    print(f"- experience_cards: {len(cards)}")
    print(f"- report: {outputs['report_path']}")
    print(f"- cards: {outputs['cards_path']}")
    print(f"- benchmarks: {outputs['benchmark_path']}")
    print(f"- methodology: {outputs['methodology_path']}")
    print(f"- deep_cases: {outputs['deep_cases_path']}")
    print("- dimensions:")
    for key, value in summary["dimensions"].most_common():
        print(f"  {key}: {value}")
    print("- work_categories:")
    for key, value in summary["work_categories"].most_common():
        print(f"  {key}: {value}")
    print("- problem_patterns:")
    for key, value in summary["problem_patterns"].most_common():
        print(f"  {key}: {value}")
    print("- professional_attributions:")
    for key, value in summary["professional_attributions"].most_common():
        print(f"  {key}: {value}")

    if args.apply and not args.dry_run:
        result = apply_cards_to_kb(cards)
        print("Knowledge base updated")
        print(f"- retired_raw_table_rules: {result['retired_raw_table_rules']}")
        print(f"- experience_rules: {result['experience_rules']}")
        print(f"- {result['message']}")
    else:
        print("Knowledge base not modified. Use --apply to publish cards.")


if __name__ == "__main__":
    main()
