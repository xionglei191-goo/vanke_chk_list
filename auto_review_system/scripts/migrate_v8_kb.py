import json
import os
import shutil
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(APP_DIR)
sys.path.insert(0, APP_DIR)
sys.path.insert(0, PROJECT_DIR)

from rag_engine.kb_manager import get_all_rules, replace_all_rules  # noqa: E402

KB_FILE_PATH = os.path.join(APP_DIR, "data", "knowledge_base.json")
BACKUP_PATH = os.path.join(APP_DIR, "data", "knowledge_base.json.v7_backup")

def migrate():
    print("🚀 [V8.0 迁移] 开始洗库...")
    
    # 1. Load existing DB from SQLite primary storage.
    rules = get_all_rules()
        
    if not rules:
        print("🈳 知识库为空。")
        return
        
    # 2. Backup
    shutil.copy(KB_FILE_PATH, BACKUP_PATH)
    print(f"✅ 已备份底层 JSON 至: {BACKUP_PATH}")
    
    # 3. Group by Category (source_file)
    grouped = {}
    for r in rules:
        src = r.get("category", "默认规范")
        if src not in grouped:
            grouped[src] = []
        grouped[src].append(r)
        
    # 4. Re-assign seq_index
    new_rules = []
    total_processed = 0
    for src, group in grouped.items():
        # Usually they were ingested sequentially, so the existing order is chronological
        for idx, r in enumerate(group):
            r["source_file"] = src
            r["seq_index"] = idx
            new_rules.append(r)
            total_processed += 1
            
    # 4.1 Make sure to format rule list safely. (Order doesn't really matter as long as seq is assigned)
    # Actually wait! The rules in the JSON are technically already in ingestion order. We should maintain the absolute order in the file.
    
    ok, msg = replace_all_rules(rules, rebuild_vector=True)
    if not ok:
        print(f"❌ {msg}")
        return

    print(f"✅ 已重筑坐标系。共清洗并打标了 {total_processed} 条独立规范切片。")
    print("✅ 已通过 SQLite 主库写入，并重建 JSON 备份与 ChromaDB。")
    
if __name__ == "__main__":
    migrate()
