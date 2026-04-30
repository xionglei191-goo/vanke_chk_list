#!/usr/bin/env python3
"""
Backfill WBS codes without LLM calls.

Default is dry-run. Use --apply to write SQLite, JSON backup, BM25 and ChromaDB.
"""
import argparse
import os
import sys
from collections import Counter

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(APP_DIR)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, PROJECT_DIR)

from rag_engine.kb_manager import batch_update_rules, get_all_rules  # noqa: E402
from rag_engine.wbs_classifier import classify_rule  # noqa: E402


def build_updates(rules, min_confidence=3, limit=0):
    updates = []
    skipped = 0
    for rule in rules:
        if rule.get("status", "active") != "active":
            continue
        if str(rule.get("wbs_code") or "通用") not in ("", "通用", "AI_AUTO"):
            continue
        code, confidence, reason = classify_rule(rule, min_confidence=min_confidence)
        if code == "通用":
            skipped += 1
            continue
        updates.append({
            "id": rule["id"],
            "level": rule.get("level", 3),
            "wbs_code": code,
            "content": rule.get("content", ""),
            "_confidence": confidence,
            "_reason": reason,
            "_category": rule.get("category", ""),
        })
        if limit and len(updates) >= limit:
            break
    return updates, skipped


def main():
    parser = argparse.ArgumentParser(description="Heuristically backfill active generic WBS rules.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only. This is the default.")
    parser.add_argument("--apply", action="store_true", help="Write the inferred WBS codes.")
    parser.add_argument("--min-confidence", type=int, default=3, help="Minimum classifier confidence.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of updates.")
    parser.add_argument("--sample", type=int, default=20, help="Number of sample updates to print.")
    args = parser.parse_args()

    rules = get_all_rules()
    updates, skipped = build_updates(rules, min_confidence=args.min_confidence, limit=args.limit)
    dist = Counter(item["wbs_code"] for item in updates)

    print(f"rules_total={len(rules)}")
    print(f"candidate_updates={len(updates)}")
    print(f"low_confidence_or_generic_skipped={skipped}")
    print("target_distribution:")
    for code, count in dist.most_common():
        print(f"  {code}: {count}")

    if updates:
        print("samples:")
        for item in updates[: max(0, args.sample)]:
            print(
                f"  {item['id']} -> {item['wbs_code']} "
                f"(confidence={item['_confidence']}, reason={item['_reason']}, category={item['_category']})"
            )

    if not args.apply:
        print("dry_run=true; no changes written. Add --apply to persist.")
        return

    clean_updates = [
        {k: v for k, v in item.items() if not k.startswith("_")}
        for item in updates
    ]
    ok, msg = batch_update_rules(clean_updates, [])
    print(("ok: " if ok else "failed: ") + msg)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
