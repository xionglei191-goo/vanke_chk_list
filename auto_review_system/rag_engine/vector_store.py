import chromadb
from chromadb.utils import embedding_functions
import os
import json
import jieba
from rank_bm25 import BM25Okapi
import logging
from utils.cost_controls import rag_rerank_mode

# ==========================================
# 向量数据库与 RAG 检索模块 (通过 JSON 动态加载)
# ==========================================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSIST_DIR = os.path.join(BASE_DIR, "vector_db_storage")
KB_FILE_PATH = os.path.join(BASE_DIR, "data", "knowledge_base.json")
COLLECTION_NAME = "vanke_standards_collection"
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "neighbor").strip().lower()
METADATA_FULL_TEXT_LIMIT = int(os.getenv("CHROMA_FULL_TEXT_METADATA_LIMIT", "2000"))
INCLUDE_EXPERIENCE_IN_STANDARD_RAG = os.getenv(
    "INCLUDE_EXPERIENCE_IN_STANDARD_RAG", "false"
).strip().lower() in ("1", "true", "yes")
REQUIRED_METADATA_KEYS = {
    "category",
    "level",
    "wbs_code",
    "source_file",
    "seq_index",
    "status",
    "publish_date",
    "lifecycle_phase",
    "is_washed",
    "condensed_content",
    "ingested",
    "index_source",
    "full_text",
    "start_index",
    "end_index",
    "node_title",
    "node_id",
    "quality_score",
    "quality_flags",
    "quality_notes",
}
RULE_FULL_TEXT_BY_ID = {}


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_rule_metadata(rule):
    """统一 ChromaDB metadata，避免不同写入入口生成不一致的过滤字段。"""
    category = str(rule.get("category") or "默认分类")
    full_text = str(rule.get("full_text") or "")
    metadata = {
        "category": category,
        "level": _safe_int(rule.get("level", 3), 3),
        "wbs_code": str(rule.get("wbs_code") or "通用"),
        "source_file": str(rule.get("source_file") or category or "内部规范"),
        "seq_index": _safe_int(rule.get("seq_index", -1), -1),
        "status": str(rule.get("status") or "active"),
        "publish_date": str(rule.get("publish_date") or "2000-01-01"),
        "lifecycle_phase": str(rule.get("lifecycle_phase") or "施工"),
        "is_washed": bool(rule.get("is_washed", False)),
        "condensed_content": str(rule.get("condensed_content") or ""),
        "ingested": str(rule.get("ingest_time") or rule.get("ingested") or ""),
        "index_source": str(rule.get("index_source") or "legacy"),
        "full_text": full_text[:METADATA_FULL_TEXT_LIMIT],
        "start_index": _safe_int(rule.get("start_index", -1), -1),
        "end_index": _safe_int(rule.get("end_index", -1), -1),
        "node_title": str(rule.get("node_title") or ""),
        "node_id": str(rule.get("node_id") or ""),
        "quality_score": _safe_int(rule.get("quality_score", -1), -1),
        "quality_flags": ",".join(rule.get("quality_flags") or []) if isinstance(rule.get("quality_flags"), list) else str(rule.get("quality_flags") or ""),
        "quality_notes": str(rule.get("quality_notes") or "")[:500],
    }
    if "project_id" in rule:
        metadata["project_id"] = str(rule["project_id"])
    return metadata


def _select_rule_content(rule):
    condensed = str(rule.get("condensed_content") or "")
    if rule.get("is_washed", False) and condensed.strip():
        return condensed
    return str(rule.get("content") or "")


def _filter_rule_objects(data):
    if not isinstance(data, list):
        return []
    return [rule for rule in data if isinstance(rule, dict)]


def _full_text_for_rule(rule_id, fallback=""):
    if rule_id:
        text = RULE_FULL_TEXT_BY_ID.get(str(rule_id))
        if text:
            return text
    return str(fallback or "")




def _build_vector_payload(rules):
    ids = []
    documents = []
    metadatas = []
    for rule in _filter_rule_objects(rules):
        rule_id = str(rule.get("id") or "").strip()
        content = str(rule.get("content") or "").strip()
        if not rule_id or not content:
            continue
        ids.append(rule_id)
        documents.append(content)
        metadatas.append(normalize_rule_metadata(rule))
    return ids, documents, metadatas


def _tail(text, limit=200):
    text = str(text or "").strip()
    return text[-limit:] if len(text) > limit else text


def _head(text, limit=200):
    text = str(text or "").strip()
    return text[:limit] if len(text) > limit else text

# 初始化持久化 ChromaDB
try:
    client = chromadb.PersistentClient(path=PERSIST_DIR)

    # 使用轻量级的多语言 embedding 模型（这里用默认的用于演示，实际生产可替换为 bge-large-zh）
    ef = embedding_functions.DefaultEmbeddingFunction()

    # 创建或获取 Collection
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef
    )
except Exception as e:
    # Environment issue fallback
    client = None
    collection = None
    logging.getLogger(__name__).warning(f"ChromaDB failed to initialize, RAG falls back. Error: {e}")

BM25_CORPUS = []
BM25_INDEX = None
BM25_METAS = []
NEIGHBOR_INDEX = {}
_bm25_dirty = False


def mark_bm25_dirty():
    """标记 BM25 索引需要重建（在 KB 写入后调用，而非立即重建）。"""
    global _bm25_dirty
    _bm25_dirty = True


def _ensure_bm25_fresh():
    """在检索前检查脏标记，按需重建。"""
    global _bm25_dirty
    if _bm25_dirty:
        rules = _load_kb_from_json()
        build_bm25_index(rules)
        _bm25_dirty = False

def build_bm25_index(rules):
    global BM25_CORPUS, BM25_INDEX, BM25_METAS, NEIGHBOR_INDEX, RULE_FULL_TEXT_BY_ID
    BM25_CORPUS = []
    BM25_INDEX = None
    BM25_METAS = []
    NEIGHBOR_INDEX = {}
    RULE_FULL_TEXT_BY_ID = {}

    for r in _filter_rule_objects(rules):
        if r.get("status", "active") != "active":
            continue

        display_content = _select_rule_content(r)
        if not display_content.strip():
            continue

        # [V8.0] 构建上下文坐标网络
        meta = normalize_rule_metadata(r)
        rule_id = str(r.get("id", ""))
        if rule_id and str(r.get("full_text") or "").strip():
            RULE_FULL_TEXT_BY_ID[rule_id] = str(r.get("full_text") or "")
        source_file = meta["source_file"]
        seq_index = meta["seq_index"]
        if source_file and seq_index >= 0:
            NEIGHBOR_INDEX[(source_file, seq_index)] = display_content

        # Jieba 中文分词构建 BM25 词袋
        tokens = list(jieba.cut(display_content))
        BM25_CORPUS.append(tokens)
        BM25_METAS.append({
            "id": rule_id,
            "category": meta["category"],
            "level": meta["level"],
            "wbs_code": meta["wbs_code"],
            "publish_date": meta["publish_date"],
            "lifecycle_phase": meta["lifecycle_phase"],
            "content": display_content,
            "source_file": meta["source_file"],
            "seq_index": meta["seq_index"],
            "is_washed": meta["is_washed"],
            "condensed_content": meta["condensed_content"],
            "index_source": meta["index_source"],
            "full_text": meta["full_text"],
            "start_index": meta["start_index"],
            "end_index": meta["end_index"],
            "node_title": meta["node_title"],
            "node_id": meta["node_id"],
        })

    if BM25_CORPUS:
        BM25_INDEX = BM25Okapi(BM25_CORPUS)


def _load_kb_from_json():
    """
    读取知识库。SQLite 为主存储，JSON 为迁移/降级备份。
    """
    try:
        import rag_engine.kb_store as kb_store
        rules = kb_store.get_all_rules(status_filter=None)
        if rules:
            return _filter_rule_objects(rules)
    except Exception:
        pass

    if not os.path.exists(KB_FILE_PATH):
        return []
    with open(KB_FILE_PATH, 'r', encoding='utf-8') as f:
        return _filter_rule_objects(json.load(f))

# 降级模式的内置匹配
MOCK_RULES_DB = _load_kb_from_json()

def _reset_collection():
    global collection
    if client is None:
        return
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef
    )


def _collection_needs_reset(ids):
    if collection is None:
        return False
    if collection.count() != len(ids):
        return True
    try:
        existing = collection.get(ids=ids, include=[])
        existing_ids = set(existing.get("ids", []))
        return existing_ids != set(ids)
    except Exception:
        return True


def _collection_needs_metadata_sync(ids):
    if collection is None:
        return False
    if _collection_needs_reset(ids):
        return True
    try:
        existing = collection.get(ids=ids, include=["metadatas"])
        for meta in existing.get("metadatas", []):
            if not meta or not REQUIRED_METADATA_KEYS.issubset(meta.keys()):
                return True
    except Exception:
        return True
    return False


def init_vector_db(force=False):
    """
    启动时运行：将 data/knowledge_base.json 中的真实规范刷入向量库。
    实际企业应用中，这个动作可以通过上传知识库 PDF 文件自动触发。
    """
    if collection is None:
        return

    rules = _load_kb_from_json()
    if not rules:
        return

    ids, documents, metadatas = _build_vector_payload(rules)
    if not ids:
        return

    needs_reset = force or _collection_needs_reset(ids)
    if not needs_reset and not _collection_needs_metadata_sync(ids):
        build_bm25_index(rules)
        return

    # 如果集合数量或 ID 集不一致，先重建以清除历史残留；仅 metadata 缺字段时用 upsert 刷新。
    if needs_reset:
        _reset_collection()

    collection.upsert(
        documents=documents,
        metadatas=metadatas,
        ids=ids
    )

    build_bm25_index(rules)

# App 启动时自动注入双栈知识库 (ChromaDB + BM25)
init_vector_db()

def get_wbs_inheritance(wbs_code):
    """
    分解 WBS 码的所有父级血缘。
    例如输入: "04-03-01"，输出: ["04-03-01", "04-03", "04", "通用"]
    """
    if not wbs_code or wbs_code == "通用":
        return ["通用"]

    parts = wbs_code.split("-")
    ancestry = []
    current = ""
    for p in parts:
        if current:
            current += "-" + p
        else:
            current = p
        ancestry.append(current)

    ancestry.reverse() # 最明确的子节点放在最前面
    ancestry.append("通用")
    return ancestry


def _local_rerank_rules(query, candidates):
    """Cheap lexical relevance filter used by the balanced cost profile."""
    if not candidates:
        return []

    stop_words = {
        "工程", "施工", "方案", "要求", "标准", "规范", "项目", "进行", "应当", "必须",
        "以及", "或者", "相关", "检查", "验收", "质量", "内容", "规定",
    }
    query_tokens = {
        token.strip().lower()
        for token in jieba.cut(str(query or ""))
        if len(token.strip()) >= 2 and token.strip() not in stop_words
    }
    if not query_tokens:
        return candidates

    scored = []
    for idx, doc in enumerate(candidates):
        doc_text = str(doc or "").lower()
        overlap = sum(1 for token in query_tokens if token in doc_text)
        # Keep RRF order as a tie-breaker by subtracting idx very lightly.
        scored.append((overlap, -idx / 1000.0, doc))

    positives = [item for item in scored if item[0] > 0]
    if not positives:
        return candidates
    positives.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in positives]

def retrieve_rules(query, wbs_code=None, lifecycle="施工", n_results=2):
    """
    高级知识图谱检索引擎 (支持冲突消解与血缘上朔检索):
    【V8.0】 接入 ChromaDB 语义向量 + Rank_BM25 词频匹配的双引擎 RRF 融合打分。
    """
    if not query.strip():
         return "【系统提示】检索内容为空，系统无挂载规范要求。"

    _ensure_bm25_fresh()

    recall_n = n_results * 3
    valid_wbs_list = get_wbs_inheritance(wbs_code)

    chroma_results = []
    # 1. ChromaDB 语义召回
    if collection is not None and collection.count() > 0:
        try:
            where_clause = {
                "$and": [
                    {"status": {"$eq": "active"}},
                    {"lifecycle_phase": {"$in": [lifecycle, "通用"]}}
                ]
            }
            if not INCLUDE_EXPERIENCE_IN_STANDARD_RAG:
                where_clause["$and"].append({"index_source": {"$ne": "review_experience"}})
            if wbs_code and wbs_code != "通用":
                where_clause["$and"].append({"wbs_code": {"$in": valid_wbs_list}})

            results = collection.query(
                query_texts=[query],
                n_results=recall_n,
                where=where_clause
            )

            if results['documents'] and len(results['documents']) > 0:
                retrieved_docs = results['documents'][0]
                retrieved_metas = results['metadatas'][0]
                retrieved_ids = results.get('ids', [[]])[0]
                for doc_id, doc, meta in zip(retrieved_ids, retrieved_docs, retrieved_metas):
                    actual_content = meta.get('condensed_content') if meta.get('is_washed', False) else doc
                    if not actual_content or not actual_content.strip():
                        actual_content = doc

                    chroma_results.append({
                        "id": doc_id,
                        "content": actual_content,
                        "level": int(meta.get('level', 3)),
                        "publish_date": meta.get('publish_date', '2000-01-01'),
                        "source_file": meta.get('source_file', ''),
                        "seq_index": meta.get('seq_index', -1),
                        "index_source": meta.get('index_source', 'legacy'),
                        "full_text": meta.get('full_text', ''),
                    })
        except: pass

    # 2. BM25 词频极速猎犬召回
    bm25_results = []
    if BM25_INDEX is not None:
        query_tokens = list(jieba.cut(query))
        doc_scores = BM25_INDEX.get_scores(query_tokens)

        scored_docs = []
        for idx, score in enumerate(doc_scores):
            if score <= 0.5: continue
            meta = BM25_METAS[idx]
            if meta['lifecycle_phase'] not in [lifecycle, "通用"]: continue
            if not INCLUDE_EXPERIENCE_IN_STANDARD_RAG and meta.get("index_source") == "review_experience": continue
            if wbs_code and wbs_code != "通用" and meta['wbs_code'] not in valid_wbs_list: continue
            scored_docs.append((score, meta))

        scored_docs.sort(key=lambda x: x[0], reverse=True)
        for score, meta in scored_docs[:recall_n]:
            actual_content = meta.get('condensed_content') if meta.get('is_washed', False) else meta['content']
            if not actual_content or not actual_content.strip():
                actual_content = meta['content']

            bm25_results.append({
                "id": meta.get("id", ""),
                "content": actual_content,
                "level": meta.get("level", 3),
                "publish_date": meta.get("publish_date", '2000-01-01'),
                "source_file": meta.get("source_file", ''),
                "seq_index": meta.get("seq_index", -1),
                "index_source": meta.get("index_source", "legacy"),
                "full_text": meta.get("full_text", ""),
            })

    # 3. RRF (Reciprocal Rank Fusion) 双擎融合
    k = 60
    rrf_scores = {}

    def score_and_merge(rank_list):
        for rank, item in enumerate(rank_list):
            key = item.get("id") or item["content"]
            if key not in rrf_scores:
                rrf_scores[key] = {
                    "id": item.get("id", ""),
                    "score": 0.0,
                    "content": item["content"],
                    "level": item["level"],
                    "pub": item["publish_date"],
                    "source_file": item.get("source_file", ""),
                    "seq_index": item.get("seq_index", -1),
                    "index_source": item.get("index_source", "legacy"),
                    "full_text": item.get("full_text", ""),
                }
            # RRF 核心公式: 1 / (60 + Rank)
            if item.get("full_text") and not rrf_scores[key].get("full_text"):
                rrf_scores[key]["full_text"] = item["full_text"]
            if item.get("index_source") == "pageindex" and rrf_scores[key].get("index_source") != "pageindex":
                rrf_scores[key]["index_source"] = "pageindex"
            rrf_scores[key]["score"] += 1.0 / (k + rank + 1)

    score_and_merge(chroma_results)
    score_and_merge(bm25_results)

    # 4. 冲突消解最终排序: Score降序 -> Level升序 -> Date降序
    merged_list = [{
        "id": d["id"],
        "content": d["content"],
        "score": d["score"],
        "level": d["level"],
        "pub": d["pub"],
        "source_file": d["source_file"],
        "seq_index": d["seq_index"],
        "index_source": d["index_source"],
        "full_text": d["full_text"],
    } for d in rrf_scores.values()]

    def rrf_sort_key(x):
        date_v = x["pub"]
        try:
            date_score = -int(date_v.replace("-", "")[:8])
        except (ValueError, TypeError):
            date_score = 0
        return (-x["score"], x["level"], date_score)

    merged_list.sort(key=rrf_sort_key)

    # 获取 RRF 初筛前 10 条最高分规范，并实施 [V8.0 Neighbor Expansion 上下文连带扩张]
    top_candidates = []
    for m in merged_list[:10]:
        sf = m["source_file"]
        si = m["seq_index"]
        core_text = m["content"]

        if m.get("index_source") == "pageindex":
            full_text = _full_text_for_rule(m.get("id"), m.get("full_text")).strip()
            source_label = str(sf or "未知来源").strip()
            source_prefix = f"【来源】：{source_label}\n"
            if full_text:
                top_candidates.append(f"【PageIndex语义节点】：\n{source_prefix}{core_text}\n【完整条款原文】：{full_text}")
            else:
                top_candidates.append(f"【PageIndex语义节点】：\n{source_prefix}{core_text}")
        elif RETRIEVAL_MODE in ("neighbor", "parent") and sf and si >= 0:
            prev_txt = NEIGHBOR_INDEX.get((sf, si - 1), "")
            next_txt = NEIGHBOR_INDEX.get((sf, si + 1), "")

            expanded_text = core_text
            if prev_txt or next_txt:
                expanded_text = f"【命中段落】：{core_text}"
                if prev_txt:
                    expanded_text = f"【前序背景】：{_tail(prev_txt)}\n{expanded_text}"
                if next_txt:
                    expanded_text = f"{expanded_text}\n【后续延伸】：{_head(next_txt)}"
            top_candidates.append(expanded_text)
        else:
            top_candidates.append(core_text)

    # --- 低成本重排：balanced 默认走本地词面过滤，quality 可显式启用 LLM 法官 ---
    mode = rag_rerank_mode()
    if mode == "llm":
        try:
            from auditors.engineering_auditor import llm_rerank_rules
            final_docs = llm_rerank_rules(query, top_candidates)
        except Exception as e:
            logging.getLogger(__name__).warning(f"LLM Rerank Failed: {e}")
            final_docs = top_candidates
    elif mode == "off":
        final_docs = top_candidates
    else:
        final_docs = _local_rerank_rules(query, top_candidates)

    # 只取最靠前的 n_results 个有效强相关规范
    final_docs = final_docs[:n_results]

    if not final_docs:
        # Fallback to pure string matching if both vectors failed (very rare)
        matched_rules = []
        for rule in MOCK_RULES_DB:
            if wbs_code and wbs_code != "通用" and rule.get("wbs_code") != wbs_code:
                continue
            tags = rule.get("tags", [])
            if any(tag in query for tag in tags):
                matched_rules.append(rule)
        if matched_rules:
            matched_rules.sort(key=lambda x: int(x.get('level', 3)))
            final_docs = [r["content"] for r in matched_rules[:n_results]]
        else:
            return "【安全预警】未检索到高度匹配的国家工程标准红线。"

    return "\n".join(final_docs)
