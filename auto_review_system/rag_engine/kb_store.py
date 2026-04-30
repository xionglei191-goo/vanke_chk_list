"""
知识库 SQLite 存储层
====================
将 knowledge_base.json (6.9MB, 2411+ rules) 迁移至 SQLite，
提供结构化 CRUD 接口，支持高效查询、索引和并发安全。
"""
import sqlite3
import os
import json
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "knowledge_base.db")

# 所有已知字段（union of all rules）
_ALL_COLUMNS = [
    "id", "category", "wbs_code", "level", "content", "tags",
    "is_washed", "condensed_content", "ingest_time", "source_file", "seq_index",
    "status", "full_text", "summary", "publish_date", "lifecycle_phase",
    "index_source", "node_id", "node_title", "start_index", "end_index",
    "verification_status", "retired_by_index_source", "retired_reason", "retired_time",
    "quality_score", "quality_flags", "quality_notes",
]


def init_db():
    """创建 SQLite 数据库和表结构。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            id TEXT PRIMARY KEY,
            category TEXT DEFAULT '',
            wbs_code TEXT DEFAULT '通用',
            level INTEGER DEFAULT 3,
            content TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            is_washed INTEGER DEFAULT 0,
            condensed_content TEXT DEFAULT '',
            ingest_time TEXT DEFAULT '',
            source_file TEXT DEFAULT '',
            seq_index INTEGER DEFAULT -1,
            status TEXT DEFAULT 'active',
            full_text TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            publish_date TEXT DEFAULT '',
            lifecycle_phase TEXT DEFAULT '',
            index_source TEXT DEFAULT '',
            node_id TEXT DEFAULT '',
            node_title TEXT DEFAULT '',
            start_index INTEGER DEFAULT -1,
            end_index INTEGER DEFAULT -1,
            verification_status TEXT DEFAULT '',
            retired_by_index_source TEXT DEFAULT '',
            retired_reason TEXT DEFAULT '',
            retired_time TEXT DEFAULT '',
            quality_score INTEGER DEFAULT -1,
            quality_flags TEXT DEFAULT '[]',
            quality_notes TEXT DEFAULT ''
        )
    """)
    cursor.execute("PRAGMA table_info(rules)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    migrations = {
        "quality_score": "ALTER TABLE rules ADD COLUMN quality_score INTEGER DEFAULT -1",
        "quality_flags": "ALTER TABLE rules ADD COLUMN quality_flags TEXT DEFAULT '[]'",
        "quality_notes": "ALTER TABLE rules ADD COLUMN quality_notes TEXT DEFAULT ''",
    }
    for col_name, sql in migrations.items():
        if col_name not in existing_cols:
            cursor.execute(sql)
    # 索引：按来源文件和状态查询
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_category ON rules(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_source_file ON rules(source_file)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_wbs_code ON rules(wbs_code)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rules_quality_score ON rules(quality_score)")
    conn.commit()
    conn.close()


# 模块级初始化
init_db()


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _rule_to_dict(row):
    """将 sqlite3.Row 转为兼容旧 JSON 格式的 dict。"""
    d = dict(row)
    # tags 存储为 JSON 字符串
    if isinstance(d.get("tags"), str):
        try:
            d["tags"] = json.loads(d["tags"])
        except Exception:
            d["tags"] = []
    if isinstance(d.get("quality_flags"), str):
        try:
            d["quality_flags"] = json.loads(d["quality_flags"])
        except Exception:
            d["quality_flags"] = []
    # is_washed 从 0/1 转为 bool
    d["is_washed"] = bool(d.get("is_washed", 0))
    return d


def _dict_to_row(rule):
    """将规则 dict 转为 SQLite 插入参数。"""
    tags = rule.get("tags", [])
    if isinstance(tags, list):
        tags = json.dumps(tags, ensure_ascii=False)
    quality_flags = rule.get("quality_flags", [])
    if isinstance(quality_flags, list):
        quality_flags = json.dumps(quality_flags, ensure_ascii=False)
    is_washed = 1 if rule.get("is_washed") else 0
    return {
        "id": rule.get("id", ""),
        "category": rule.get("category", ""),
        "wbs_code": rule.get("wbs_code", "通用"),
        "level": int(rule.get("level", 3)),
        "content": rule.get("content", ""),
        "tags": tags,
        "is_washed": is_washed,
        "condensed_content": rule.get("condensed_content", ""),
        "ingest_time": rule.get("ingest_time", ""),
        "source_file": rule.get("source_file", ""),
        "seq_index": int(rule.get("seq_index", -1)),
        "status": rule.get("status", "active"),
        "full_text": rule.get("full_text", ""),
        "summary": rule.get("summary", ""),
        "publish_date": rule.get("publish_date", ""),
        "lifecycle_phase": rule.get("lifecycle_phase", ""),
        "index_source": rule.get("index_source", ""),
        "node_id": rule.get("node_id", ""),
        "node_title": rule.get("node_title", ""),
        "start_index": int(rule.get("start_index", -1)),
        "end_index": int(rule.get("end_index", -1)),
        "verification_status": rule.get("verification_status", ""),
        "retired_by_index_source": rule.get("retired_by_index_source", ""),
        "retired_reason": rule.get("retired_reason", ""),
        "retired_time": rule.get("retired_time", ""),
        "quality_score": int(rule.get("quality_score", -1)),
        "quality_flags": quality_flags,
        "quality_notes": rule.get("quality_notes", ""),
    }


# ==================== CRUD ====================

def get_all_rules(status_filter="active"):
    """获取所有规则（默认只返回 active 状态）。传入 None 获取全部。"""
    conn = _get_conn()
    cursor = conn.cursor()
    if status_filter:
        cursor.execute("SELECT * FROM rules WHERE status = ? ORDER BY source_file, seq_index", (status_filter,))
    else:
        cursor.execute("SELECT * FROM rules ORDER BY source_file, seq_index")
    rows = cursor.fetchall()
    conn.close()
    return [_rule_to_dict(r) for r in rows]


def get_rule_by_id(rule_id):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rules WHERE id = ?", (rule_id,))
    row = cursor.fetchone()
    conn.close()
    return _rule_to_dict(row) if row else None


def upsert_rule(rule):
    """插入或更新一条规则。"""
    row = _dict_to_row(rule)
    conn = _get_conn()
    cursor = conn.cursor()
    cols = list(row.keys())
    placeholders = ", ".join(["?"] * len(cols))
    updates = ", ".join([f"{c} = excluded.{c}" for c in cols if c != "id"])
    sql = f"INSERT INTO rules ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}"
    cursor.execute(sql, [row[c] for c in cols])
    conn.commit()
    conn.close()


def upsert_rules_batch(rules):
    """批量插入或更新。"""
    if not rules:
        return
    conn = _get_conn()
    cursor = conn.cursor()
    for rule in rules:
        row = _dict_to_row(rule)
        cols = list(row.keys())
        placeholders = ", ".join(["?"] * len(cols))
        updates = ", ".join([f"{c} = excluded.{c}" for c in cols if c != "id"])
        sql = f"INSERT INTO rules ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}"
        cursor.execute(sql, [row[c] for c in cols])
    conn.commit()
    conn.close()
    logger.info(f"Batch upserted {len(rules)} rules to SQLite")


def replace_all_rules(rules):
    """Atomically replace the full rules table."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rules")
    for rule in rules:
        row = _dict_to_row(rule)
        cols = list(row.keys())
        placeholders = ", ".join(["?"] * len(cols))
        updates = ", ".join([f"{c} = excluded.{c}" for c in cols if c != "id"])
        sql = f"INSERT INTO rules ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}"
        cursor.execute(sql, [row[c] for c in cols])
    conn.commit()
    conn.close()
    logger.info(f"Replaced SQLite knowledge base with {len(rules)} rules")


def delete_rule(rule_id):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()


def delete_rules_by_category(category):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rules WHERE category = ?", (category,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected


def count_rules(status_filter="active"):
    conn = _get_conn()
    cursor = conn.cursor()
    if status_filter:
        cursor.execute("SELECT COUNT(*) FROM rules WHERE status = ?", (status_filter,))
    else:
        cursor.execute("SELECT COUNT(*) FROM rules")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_categories():
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT category FROM rules WHERE status = 'active'")
    cats = [row[0] for row in cursor.fetchall()]
    conn.close()
    return cats


def get_unwashed_rules(limit=10):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM rules WHERE is_washed = 0 AND status = 'active' LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [_rule_to_dict(r) for r in rows]


def update_washed(rule_id, condensed_content):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE rules SET is_washed = 1, condensed_content = ? WHERE id = ?", (condensed_content, rule_id))
    conn.commit()
    conn.close()


# ==================== 迁移工具 ====================

def migrate_from_json(json_path):
    """从 JSON 文件迁移所有规则到 SQLite。"""
    if not os.path.exists(json_path):
        logger.error(f"Migration source not found: {json_path}")
        return False, "JSON 文件不存在"

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    rules = [r for r in data if isinstance(r, dict) and r.get("id")]
    if not rules:
        return False, "JSON 文件中无有效规则"

    upsert_rules_batch(rules)
    # 验证
    actual_count = count_rules(status_filter=None)
    logger.info(f"Migration complete: {len(rules)} rules loaded, {actual_count} in DB")
    return True, f"成功迁移 {len(rules)} 条规则至 SQLite (DB 实际: {actual_count})"
