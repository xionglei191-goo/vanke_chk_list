"""
SQLite-backed LLM response cache and call log.
"""
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import threading

from utils.paths import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "llm_cache.db")
_LOCK = threading.Lock()


def _enabled():
    return os.getenv("LLM_CACHE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _now():
    return _dt.datetime.now()


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def success_ttl_seconds():
    days = int(os.getenv("LLM_CACHE_TTL_DAYS", "30"))
    return max(0, days) * 24 * 60 * 60


def failure_ttl_seconds():
    return max(0, int(os.getenv("LLM_FAILURE_CACHE_TTL_SECONDS", "600")))


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_response_cache (
                cache_key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT DEFAULT '',
                api_type TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_call_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                cache_hit INTEGER NOT NULL,
                model TEXT DEFAULT '',
                api_type TEXT DEFAULT '',
                caller TEXT DEFAULT '',
                cache_key TEXT DEFAULT '',
                status TEXT DEFAULT ''
            )
            """
        )


def build_cache_key(api_type, model, system_prompt, user_text, params=None):
    payload = {
        "api_type": api_type,
        "model": model,
        "system_prompt": system_prompt,
        "user_text": user_text,
        "params": params or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_text(cache_key):
    if not _enabled():
        return None
    init_db()
    now_text = _ts(_now())
    with _LOCK, sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT content FROM llm_response_cache WHERE cache_key = ? AND expires_at >= ?",
            (cache_key, now_text),
        ).fetchone()
    return row[0] if row else None


def store_cached_text(cache_key, content, status, model="", api_type="", ttl_seconds=None):
    if not _enabled() or not cache_key or content is None:
        return
    ttl = success_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    if ttl <= 0:
        return
    init_db()
    now = _now()
    expires = now + _dt.timedelta(seconds=ttl)
    with _LOCK, sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO llm_response_cache
            (cache_key, status, content, model, api_type, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                status = excluded.status,
                content = excluded.content,
                model = excluded.model,
                api_type = excluded.api_type,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at
            """,
            (cache_key, status, content, model, api_type, _ts(now), _ts(expires)),
        )


def record_call(cache_key, cache_hit, model="", api_type="", caller="", status=""):
    if not _enabled():
        return
    init_db()
    with _LOCK, sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO llm_call_log
            (created_at, cache_hit, model, api_type, caller, cache_key, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_ts(_now()), 1 if cache_hit else 0, model, api_type, caller, cache_key, status),
        )


def cache_stats():
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cached = conn.execute("SELECT COUNT(*) FROM llm_response_cache").fetchone()[0]
        calls = conn.execute("SELECT COUNT(*) FROM llm_call_log").fetchone()[0]
        hits = conn.execute("SELECT COUNT(*) FROM llm_call_log WHERE cache_hit = 1").fetchone()[0]
    return {"cached_responses": cached, "logged_calls": calls, "cache_hits": hits}
