#!/usr/bin/env python3
"""
V8.0 知识库滑动窗口重建脚本。

默认只做 dry-run 统计，不写入文件；确认预览无误后再用 --apply。
"""

import argparse
import copy
import datetime
import os
import shutil
import sys
import uuid
from collections import Counter, OrderedDict

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(APP_DIR)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, PROJECT_DIR)

from rag_engine.kb_manager import (  # noqa: E402
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    build_overlap_chunks,
    get_all_rules,
    replace_all_rules,
)

KB_FILE_PATH = os.path.join(APP_DIR, "data", "knowledge_base.json")


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_rules():
    data = get_all_rules()
    if not isinstance(data, list):
        raise ValueError("知识库读取结果必须是 list")
    return data


def _group_rules(rules):
    grouped = OrderedDict()
    for order, rule in enumerate(rules):
        source_file = str(rule.get("source_file") or rule.get("category") or "默认规范")
        grouped.setdefault(source_file, []).append((order, rule))

    sorted_groups = OrderedDict()
    for source_file, items in grouped.items():
        sorted_groups[source_file] = sorted(
            items,
            key=lambda item: (_safe_int(item[1].get("seq_index"), item[0]), item[0]),
        )
    return sorted_groups


def _most_common(values, default):
    values = [v for v in values if v not in (None, "")]
    if not values:
        return default
    return Counter(values).most_common(1)[0][0]


def _template_for_group(source_file, sorted_items):
    rules = [rule for _, rule in sorted_items]
    template = copy.deepcopy(rules[0])
    template["category"] = _most_common([r.get("category") for r in rules], source_file)
    template["source_file"] = source_file
    template["wbs_code"] = _most_common([r.get("wbs_code") for r in rules], "通用")
    template["level"] = _safe_int(_most_common([r.get("level") for r in rules], 3), 3)
    template["tags"] = _most_common([tuple(r.get("tags", [])) for r in rules if isinstance(r.get("tags", []), list)], ())
    if isinstance(template["tags"], tuple):
        template["tags"] = list(template["tags"])
    template["status"] = _most_common([r.get("status") for r in rules], "active")
    template["publish_date"] = _most_common([r.get("publish_date") for r in rules], "2000-01-01")
    template["lifecycle_phase"] = _most_common([r.get("lifecycle_phase") for r in rules], "施工")
    template["is_washed"] = False
    template["condensed_content"] = ""
    return template


def _rule_chunks(sorted_items):
    chunks = []
    for _, rule in sorted_items:
        content = str(rule.get("content") or "").strip()
        if not content:
            continue
        heading = str(rule.get("category") or rule.get("source_file") or "规范切片")
        chunks.append({"heading": heading, "text": content})
    return chunks


def rebuild_rules(rules, chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=DEFAULT_CHUNK_OVERLAP):
    rebuilt = []
    stats = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S (V8 overlap rebuild)")

    for source_file, sorted_items in _group_rules(rules).items():
        chunks = _rule_chunks(sorted_items)
        windows = build_overlap_chunks(chunks, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if not windows:
            continue

        template = _template_for_group(source_file, sorted_items)
        for seq_index, window in enumerate(windows):
            content = f"【{source_file} - {window['heading']}】{window['text']}"
            rid = f"KBV8_{uuid.uuid5(uuid.NAMESPACE_URL, f'{source_file}:{seq_index}:{content[:120]}').hex[:8].upper()}"
            new_rule = copy.deepcopy(template)
            new_rule.update({
                "id": rid,
                "source_file": source_file,
                "seq_index": seq_index,
                "content": content,
                "is_washed": False,
                "condensed_content": "",
                "ingest_time": now,
            })
            rebuilt.append(new_rule)

        stats.append({
            "source_file": source_file,
            "old_count": len(sorted_items),
            "new_count": len(windows),
        })

    return rebuilt, stats


def _write_backup(backup_path):
    if backup_path is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(APP_DIR, "data", f"knowledge_base.json.v8_overlap_{stamp}.bak")
    shutil.copy(KB_FILE_PATH, backup_path)
    return backup_path


def _rebuild_vector_store():
    from rag_engine.vector_store import init_vector_db  # noqa: WPS433

    init_vector_db(force=True)


def main():
    parser = argparse.ArgumentParser(description="V8.0 知识库滑动窗口重建工具")
    parser.add_argument("--apply", action="store_true", help="实际写回 SQLite 主库、导出 JSON 备份，并强制刷新 ChromaDB")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="窗口最大字符数")
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP, help="前后重叠字符数")
    parser.add_argument("--backup-path", default=None, help="自定义备份路径；仅 --apply 时生效")
    parser.add_argument("--no-vector-rebuild", action="store_true", help="仅写 SQLite/JSON，不刷新 ChromaDB")
    parser.add_argument("--sample", type=int, default=8, help="展示前 N 个来源的重建统计")
    args = parser.parse_args()

    rules = _load_rules()
    rebuilt, stats = rebuild_rules(rules, chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap)

    print("[V8.0 Overlap Rebuild] 知识库滑动窗口重建预览")
    print(f"原始条数: {len(rules)}")
    print(f"重建条数: {len(rebuilt)}")
    print(f"窗口参数: chunk_size={args.chunk_size}, chunk_overlap={args.chunk_overlap}")
    print("来源统计预览:")
    for row in stats[:max(0, args.sample)]:
        print(f"  - {row['source_file']}: {row['old_count']} -> {row['new_count']}")
    if len(stats) > args.sample:
        print(f"  ... 另有 {len(stats) - args.sample} 个来源未展示")

    if not args.apply:
        print("Dry-run 完成：未写入任何文件。确认后可追加 --apply 执行。")
        return

    backup_path = _write_backup(args.backup_path)
    ok, msg = replace_all_rules(rebuilt, rebuild_vector=not args.no_vector_rebuild)
    if not ok:
        raise SystemExit(msg)
    print(msg)
    print(f"已导出 JSON 备份: {KB_FILE_PATH}")
    print(f"已创建备份: {backup_path}")


if __name__ == "__main__":
    main()
