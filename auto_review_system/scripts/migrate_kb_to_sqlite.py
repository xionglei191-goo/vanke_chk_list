#!/usr/bin/env python3
"""
知识库 JSON → SQLite 一次性迁移脚本
====================================
用法: python scripts/migrate_kb_to_sqlite.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag_engine.kb_store import migrate_from_json, count_rules, DB_PATH

JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "knowledge_base.json")


def main():
    print(f"📂 源文件: {JSON_PATH}")
    print(f"📂 目标库: {DB_PATH}")

    if not os.path.exists(JSON_PATH):
        print("❌ knowledge_base.json 不存在，无法迁移。")
        return

    file_size = os.path.getsize(JSON_PATH) / 1024 / 1024
    print(f"📊 JSON 文件大小: {file_size:.1f} MB")

    ok, msg = migrate_from_json(JSON_PATH)
    print(f"\n{'✅' if ok else '❌'} {msg}")

    if ok:
        total = count_rules(status_filter=None)
        active = count_rules(status_filter="active")
        db_size = os.path.getsize(DB_PATH) / 1024 / 1024
        print(f"\n📊 迁移结果:")
        print(f"   总规则数: {total}")
        print(f"   活跃规则: {active}")
        print(f"   DB 大小:  {db_size:.1f} MB")
        print(f"\n💡 JSON 原文件已保留作为备份。确认 SQLite 稳定运行后可手动删除。")


if __name__ == "__main__":
    main()
