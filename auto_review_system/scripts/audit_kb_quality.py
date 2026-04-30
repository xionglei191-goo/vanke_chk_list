#!/usr/bin/env python3
"""
Audit and optionally retire low-quality KB rules.

This script does not delete rows. With --apply it marks critical noise as
inactive, records quality_score/flags/notes, exports JSON backup, and rebuilds
Chroma/BM25 through kb_manager.replace_all_rules().
"""
import argparse
import datetime
import fcntl
import json
import os
import sys
from collections import Counter

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(APP_DIR)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, PROJECT_DIR)

import rag_engine.kb_store as kb_store  # noqa: E402
from rag_engine.kb_quality import assess_rule_quality  # noqa: E402

KB_FILE_PATH = os.path.join(APP_DIR, "data", "knowledge_base.json")


def _export_json_backup(rules):
    lock_path = os.path.join(APP_DIR, "data", ".kb_lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(KB_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(rules, f, ensure_ascii=False, indent=4)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _rebuild_indexes(rules):
    from rag_engine.vector_store import build_bm25_index, init_vector_db

    build_bm25_index(rules)
    init_vector_db(force=True)


def audit_rules(rules, retire_threshold=35):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated = []
    flag_counts = Counter()
    retired = []
    warned = []

    for rule in rules:
        copied = dict(rule)
        result = assess_rule_quality(copied)
        copied["quality_score"] = result["score"]
        copied["quality_flags"] = result["flags"]
        copied["quality_notes"] = result["notes"]
        copied["verification_status"] = (
            "quality_retired" if result["critical"] else
            "quality_warning" if result["flags"] else
            "quality_passed"
        )

        for flag in result["flags"]:
            flag_counts[flag] += 1

        if copied.get("status", "active") == "active" and (
            result["critical"] or result["score"] <= retire_threshold
        ):
            copied["status"] = "inactive"
            copied["retired_by_index_source"] = copied.get("retired_by_index_source") or "kb_quality"
            copied["retired_reason"] = (
                "知识库质量清洗停用："
                + (result["notes"] or "低质量规则")
            )
            copied["retired_time"] = now
            retired.append(copied)
        elif copied.get("status", "active") == "active" and result["flags"]:
            warned.append(copied)

        updated.append(copied)

    return updated, retired, warned, flag_counts


def main():
    parser = argparse.ArgumentParser(description="Audit KB rule quality and retire obvious noise.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only. Default unless --apply is set.")
    parser.add_argument("--apply", action="store_true", help="Write quality metadata and retire critical noise.")
    parser.add_argument("--retire-threshold", type=int, default=35, help="Retire active rules at/below this score.")
    parser.add_argument("--sample", type=int, default=12, help="Number of retired samples to print.")
    args = parser.parse_args()

    rules = kb_store.get_all_rules(status_filter=None)
    updated, retired, warned, flag_counts = audit_rules(rules, retire_threshold=args.retire_threshold)

    print(f"rules_total={len(rules)}")
    print(f"active_retire_candidates={len(retired)}")
    print(f"active_warning_only={len(warned)}")
    print("flag_distribution:")
    for flag, count in flag_counts.most_common():
        print(f"  {flag}: {count}")

    if retired:
        print("retire_samples:")
        for rule in retired[: max(0, args.sample)]:
            print(
                f"  {rule['id']} score={rule.get('quality_score')} "
                f"flags={','.join(rule.get('quality_flags') or [])} "
                f"category={rule.get('category')} "
                f"text={(rule.get('content') or '')[:120].replace(chr(10), ' ')}"
            )

    if not args.apply:
        print("dry_run=true; no changes written. Add --apply to persist.")
        return

    kb_store.replace_all_rules(updated)
    _export_json_backup(updated)
    _rebuild_indexes(updated)
    print(f"ok: 已写入 SQLite 主库、导出 JSON 备份并刷新索引，共 {len(updated)} 条。")


if __name__ == "__main__":
    main()
