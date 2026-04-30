"""
异步审查任务队列管理器 (SQLite)
================================
模块级初始化：导入时自动创建表结构，无需每个函数重复调用 init_db()。
"""
import sqlite3
import os
import uuid
import datetime
import json
from glob import glob
from utils.paths import APP_DIR, PROJECT_DIR, DATA_DIR, RESULTS_DIR, app_relative_path

DB_PATH = os.path.join(DATA_DIR, "audit_queue.db")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS t_audit_tasks (
            task_id TEXT PRIMARY KEY,
            project_name TEXT,
            uploaded_files TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            result_docx_path TEXT,
            error_log TEXT
        )
    ''')
    conn.commit()
    conn.close()


# 模块级初始化——只执行一次
init_db()


def _get_conn():
    """获取 SQLite 连接（短生命周期，用完即关）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_task_artifact_path(path):
    """尽量把工单产物路径存为相对 APP 目录的路径，便于跨机器迁移。"""
    raw = str(path or "").strip()
    if not raw:
        return ""

    raw = os.path.expanduser(raw)
    if not os.path.isabs(raw):
        return raw.replace("\\", "/")

    try:
        rel_path = os.path.relpath(raw, APP_DIR)
    except ValueError:
        return raw

    if rel_path == "." or rel_path == ".." or rel_path.startswith(f"..{os.sep}"):
        return raw
    return rel_path.replace("\\", "/")


def resolve_task_artifact_path(path, task_id="", preferred_ext=""):
    """
    解析工单产物路径。
    兼容历史绝对路径、相对路径，以及从其他机器同步过来的旧路径记录。
    """
    raw = str(path or "").strip()
    normalized = normalize_task_artifact_path(raw) if raw else ""
    candidates = []

    def add_candidate(candidate):
        if not candidate:
            return
        candidate = os.path.normpath(candidate)
        if candidate not in candidates:
            candidates.append(candidate)

    if raw:
        if os.path.isabs(raw):
            add_candidate(raw)
        else:
            add_candidate(os.path.join(APP_DIR, raw))
            add_candidate(os.path.join(PROJECT_DIR, raw))

    if normalized and normalized != raw:
        if os.path.isabs(normalized):
            add_candidate(normalized)
        else:
            add_candidate(os.path.join(APP_DIR, normalized))
            add_candidate(os.path.join(PROJECT_DIR, normalized))

    basename = os.path.basename(raw or normalized)
    if basename:
        add_candidate(os.path.join(RESULTS_DIR, basename))

    if task_id:
        suffix = ""
        if preferred_ext:
            suffix = preferred_ext if preferred_ext.startswith(".") else f".{preferred_ext}"
        pattern = os.path.join(RESULTS_DIR, f"{task_id}_*{suffix}")
        for match in sorted(glob(pattern)):
            add_candidate(match)

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return ""


def add_task(project_name, file_paths):
    task_id = f"TASK_{uuid.uuid4().hex[:8].upper()}"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cursor = conn.cursor()
    normalized_files = []
    for item in file_paths:
        if isinstance(item, dict):
            copied = dict(item)
            if copied.get("path"):
                copied["path"] = app_relative_path(copied["path"])
            normalized_files.append(copied)
        else:
            normalized_files.append(app_relative_path(item))

    cursor.execute('''
        INSERT INTO t_audit_tasks 
        (task_id, project_name, uploaded_files, status, created_at, updated_at, result_docx_path, error_log)
        VALUES (?, ?, ?, 'PENDING', ?, ?, '', '')
    ''', (task_id, project_name, json.dumps(normalized_files, ensure_ascii=False), now, now))
    conn.commit()
    conn.close()
    return task_id


def get_pending_task():
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM t_audit_tasks WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT 1
    ''')
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_task_status(task_id, status, result_docx_path='', error_log=''):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result_docx_path = normalize_task_artifact_path(result_docx_path)
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE t_audit_tasks 
        SET status = ?, result_docx_path = ?, error_log = ?, updated_at = ?
        WHERE task_id = ?
    ''', (status, result_docx_path, error_log, now, task_id))
    conn.commit()
    conn.close()


def get_all_tasks():
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM t_audit_tasks ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    tasks = []
    for row in rows:
        item = dict(row)
        item["resolved_result_docx_path"] = resolve_task_artifact_path(
            item.get("result_docx_path", ""),
            task_id=item.get("task_id", ""),
        )
        tasks.append(item)
    return tasks


def get_task_status(task_id):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM t_audit_tasks WHERE task_id = ?', (task_id,))
    row = cursor.fetchone()
    conn.close()
    return row['status'] if row else None


def set_task_status_only(task_id, status):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE t_audit_tasks 
        SET status = ?, updated_at = ?
        WHERE task_id = ?
    ''', (status, now, task_id))
    conn.commit()
    conn.close()


def delete_task(task_id):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM t_audit_tasks WHERE task_id = ?', (task_id,))
    conn.commit()
    conn.close()
