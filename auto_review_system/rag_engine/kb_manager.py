import os
import json
import uuid
import datetime
import random
import re
import fcntl
import logging
from contextlib import contextmanager
import rag_engine.kb_store as kb_store


@contextmanager
def _kb_file_lock():
    """进程级文件锁——保护 knowledge_base.json 的并发读写安全。"""
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", ".kb_lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

from rag_engine.vector_store import (
    KB_FILE_PATH,
    build_bm25_index,
    collection,
    normalize_rule_metadata,
    _filter_rule_objects,
)
from rag_engine.wbs_classifier import classify_wbs

# 尝试重用文档切片器
from parsers.word_parser import parse_word_doc_structured

DEFAULT_CHUNK_SIZE = int(os.getenv("KB_CHUNK_SIZE", "800"))
DEFAULT_CHUNK_OVERLAP = int(os.getenv("KB_CHUNK_OVERLAP", "200"))


def _write_kb_json(rules):
    """Write JSON backup under the same file lock used by readers."""
    with _kb_file_lock(), open(KB_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(_filter_rule_objects(rules), f, ensure_ascii=False, indent=4)


def build_overlap_chunks(chunks, chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=DEFAULT_CHUNK_OVERLAP):
    """
    将解析器输出的章节块转换为带前后文重叠的知识库切片。
    解析器仍保持简单，知识入库层统一负责制造连续上下文窗口。
    """
    if not isinstance(chunks, list):
        return []

    blocks = []
    for chunk in chunks:
        text = str(chunk.get("text", "")).strip()
        if len(text) < 15:
            continue
        heading = str(chunk.get("heading", "未命名章节")).strip() or "未命名章节"
        block_text = f"### {heading}\n{text}"
        blocks.append({
            "heading": heading,
            "text": text,
            "block_text": block_text
        })

    if not blocks:
        return []

    chunk_size = max(1, int(chunk_size))
    chunk_overlap = max(0, min(int(chunk_overlap), chunk_size - 1))
    step = max(1, chunk_size - chunk_overlap)

    doc_parts = []
    spans = []
    cursor = 0
    for block in blocks:
        block_text = block["block_text"]
        start = cursor
        end = start + len(block_text)
        doc_parts.append(block_text)
        spans.append((start, end, block["heading"]))
        cursor = end + 2  # account for the "\n\n" separator below

    doc_text = "\n\n".join(doc_parts)

    def window_heading(start, end):
        headings = []
        for span_start, span_end, heading in spans:
            if span_end <= start or span_start >= end:
                continue
            if heading not in headings:
                headings.append(heading)
        if not headings:
            return "连续上下文切片"
        if len(headings) == 1:
            return headings[0]
        return f"{headings[0]} ... {headings[-1]}"

    overlapped = []
    start = 0
    while start < len(doc_text):
        end = min(start + chunk_size, len(doc_text))
        window_text = doc_text[start:end].strip()
        if len(window_text) >= 15:
            overlapped.append({
                "heading": window_heading(start, end),
                "text": window_text
            })
        if end >= len(doc_text):
            break
        start += step

    return overlapped


from utils.tree_utils import tree_roots as _tree_roots, tree_children as _tree_children, flatten_tree_leaf_nodes


def _safe_seq_from_node(node, fallback):
    raw = str(node.get("node_id") or node.get("id") or "").strip()
    if raw.isdigit():
        return int(raw)
    return fallback


def _node_identity(node, fallback):
    return str(node.get("node_id") or node.get("id") or f"{fallback:04d}")


def _compact_summary_text(text):
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_pageindex_fallback_summary(title, path, full_text, max_chars=360):
    """
    当 PageIndex 节点缺少 LLM summary 时，生成短而稳定的本地索引摘要。
    目标是避免 embedding/BM25 退化为长原文前 600 字。
    """
    title = str(title or "未命名节点").strip()
    path_text = " > ".join(str(item).strip() for item in path if str(item).strip()) or title
    text = _compact_summary_text(full_text)
    if not text:
        return f"节点：{path_text}"

    parts = [
        _compact_summary_text(part)
        for part in re.split(r"(?<=[。；;])\s+|\n+|(?<=检验方法[:：])", text)
    ]
    keywords = ("应", "不得", "必须", "严禁", "宜", "符合", "检验", "验收", "允许偏差", "质量", "施工", "防水", "安全")
    preferred = [part for part in parts if len(part) >= 12 and any(keyword in part for keyword in keywords)]
    if not preferred:
        preferred = [part for part in parts if len(part) >= 12]

    excerpt = "；".join(preferred[:4]) or text[:max_chars]
    summary = f"节点路径：{path_text}。关键要求：{excerpt}"
    return summary[:max_chars].rstrip("，,；; ")


def _is_pageindex_frontmatter_node(node):
    title = str(node.get("title") or node.get("node_title") or "").strip()
    path = [str(item).strip() for item in node.get("_path", []) if str(item).strip()]
    frontmatter_titles = {
        "preface",
        "front matter",
        "foreword",
        "cover",
        "前言",
        "目录",
        "封面",
        "公告",
    }
    title_key = title.lower()
    path_key = path[0].lower() if path else ""
    return title_key in frontmatter_titles or path_key in frontmatter_titles


def _safe_page_index(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _llm_verify_rule(rule):
    from auditors.engineering_auditor import call_llm

    system_prompt = """
    你是工程规范知识库灌入质量审查员。
    判断输入条目是否像一个语义完整的工程规范条款，而不是半截断句、目录残片或无意义文本。
    只能输出 JSON：{"pass": true/false, "reason": "一句话原因"}。
    """
    user_text = json.dumps({
        "id": rule.get("id"),
        "title": rule.get("node_title"),
        "summary": rule.get("content", "")[:500],
        "full_text": rule.get("full_text", "")[:2000],
        "start_index": rule.get("start_index"),
        "end_index": rule.get("end_index"),
    }, ensure_ascii=False)
    result = call_llm(system_prompt, user_text, max_retries=2)

    import re
    match = re.search(r"\{.*\}", result, re.S)
    if not match:
        return False, f"LLM 返回非 JSON: {result[:120]}"
    data = json.loads(match.group(0))
    return bool(data.get("pass")), str(data.get("reason") or "")


def verify_ingested_rules(rules, source_pdf_path=None, sample_n=10, use_llm=False):
    """
    对 PageIndex 灌入条目做抽样质量校验。
    默认使用离线结构化检查；use_llm=True 时追加 LLM 语义完整性判断。
    """
    if not rules:
        return 0.0, [{"id": "", "issues": ["无可校验条目"]}]

    sample_n = max(1, int(sample_n or 1))
    sample_rules = random.sample(rules, min(sample_n, len(rules)))
    failed_items = []

    for rule in sample_rules:
        issues = []
        content = str(rule.get("content") or "").strip()
        full_text = str(rule.get("full_text") or "").strip()
        start_index = _safe_page_index(rule.get("start_index"))
        end_index = _safe_page_index(rule.get("end_index"))

        if rule.get("index_source") != "pageindex":
            issues.append("index_source 不是 pageindex")
        if len(content) < 15:
            issues.append("摘要/索引文本过短")
        if len(full_text) < 30:
            issues.append("完整条款原文过短")
        if start_index >= 0 and end_index >= 0 and start_index > end_index:
            issues.append("页码范围倒置")
        if source_pdf_path and not os.path.exists(source_pdf_path):
            issues.append(f"源 PDF 不存在: {source_pdf_path}")

        if use_llm and not issues:
            try:
                passed, reason = _llm_verify_rule(rule)
                if not passed:
                    issues.append(f"LLM 判定不通过: {reason}")
            except Exception as exc:
                issues.append(f"LLM 校验异常: {exc}")

        if issues:
            failed_items.append({
                "id": rule.get("id", ""),
                "title": rule.get("node_title", ""),
                "issues": issues,
            })

    accuracy = (len(sample_rules) - len(failed_items)) / len(sample_rules)
    return accuracy, failed_items


def build_pageindex_rule_records(
    tree_json_path,
    category,
    wbs_code="AI_AUTO",
    level=1,
    custom_tags=None,
    include_frontmatter=False,
    resolve_wbs=True,
):
    """按真实灌库逻辑把 PageIndex 树索引转换为知识库规则记录。"""
    if not os.path.exists(tree_json_path):
        raise FileNotFoundError(f"树索引文件不存在: {tree_json_path}")

    with open(tree_json_path, "r", encoding="utf-8") as f:
        tree_data = json.load(f)

    raw_nodes = flatten_tree_leaf_nodes(tree_data)
    nodes = [
        node for node in raw_nodes
        if include_frontmatter or not _is_pageindex_frontmatter_node(node)
    ]

    tags = custom_tags if custom_tags is not None else [category, "pageindex"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_rules = []

    for seq, node in enumerate(nodes):
        title = str(node.get("title") or node.get("node_title") or "未命名节点").strip()
        summary = str(node.get("summary") or node.get("prefix_summary") or "").strip()
        full_text = str(node.get("text") or node.get("full_text") or node.get("content") or "").strip()
        if len(full_text) < 15:
            continue

        path = " > ".join(node.get("_path", [title]))
        if not summary:
            summary = build_pageindex_fallback_summary(title, node.get("_path", [title]), full_text)
        embedding_text = f"【{path}】\n{summary}"
        final_wbs = wbs_code
        if resolve_wbs and wbs_code == "AI_AUTO":
            final_wbs, _, _ = classify_wbs(
                text=summary or full_text,
                category=category,
                heading=title,
            )

        node_id = _node_identity(node, seq)
        stable_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{category}:{node_id}:{path}:{embedding_text[:120]}").hex[:10].upper()
        new_rules.append({
            "id": f"KB_PI_{stable_id}",
            "category": category,
            "source_file": category,
            "seq_index": seq,
            "wbs_code": final_wbs,
            "level": level,
            "content": embedding_text,
            "full_text": full_text,
            "summary": summary,
            "node_title": title,
            "node_id": node_id,
            "start_index": node.get("start_index", node.get("start_page", -1)),
            "end_index": node.get("end_index", node.get("end_page", -1)),
            "tags": tags,
            "status": "active",
            "publish_date": "2000-01-01",
            "lifecycle_phase": "施工",
            "index_source": "pageindex",
            "is_washed": False,
            "condensed_content": "",
            "ingest_time": now,
        })

    return new_rules, {
        "total_nodes": len(raw_nodes),
        "kept_nodes": len(nodes),
        "skipped_frontmatter": len(raw_nodes) - len(nodes),
        "source_pdf_path": tree_data.get("source_path"),
    }


def _is_same_source_rule(rule, category):
    return rule.get("source_file") == category or rule.get("category") == category


def get_retirable_legacy_rules(rules, category):
    """PageIndex-first 灌入时，用于识别同一规范下旧的按页/切片 legacy 条目。"""
    return [
        rule for rule in rules
        if _is_same_source_rule(rule, category)
        and rule.get("index_source", "legacy") != "pageindex"
        and rule.get("status", "active") == "active"
    ]


def _retire_legacy_rules_for_pageindex(rules, category):
    retired_rules = []
    retired_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for rule in rules:
        if not _is_same_source_rule(rule, category):
            continue
        if rule.get("index_source", "legacy") == "pageindex":
            continue
        if rule.get("status", "active") != "active":
            continue
        rule["status"] = "inactive"
        rule["retired_by_index_source"] = "pageindex"
        rule["retired_time"] = retired_time
        rule["retired_reason"] = "同源规范已由 PageIndex 语义节点替代，避免重复 OCR 按页条目参与召回。"
        retired_rules.append(rule)
    return retired_rules


def ingest_from_tree_index(
    tree_json_path,
    category,
    wbs_code='AI_AUTO',
    level=1,
    custom_tags=None,
    replace_existing=True,
    verify=False,
    source_pdf_path=None,
    verify_sample_n=10,
    verify_use_llm=False,
    min_verify_accuracy=0.7,
    include_frontmatter=False,
    retire_legacy=True,
):
    """
    从 PageIndex 树索引 JSON 中提取语义完整叶节点，作为高质量知识库条目灌入。
    PageIndex 条目的 content 使用 title + summary 供 embedding，full_text 保存完整条款供 Agent 投喂。
    """
    try:
        new_rules, ingest_meta = build_pageindex_rule_records(
            tree_json_path,
            category,
            wbs_code=wbs_code,
            level=level,
            custom_tags=custom_tags,
            include_frontmatter=include_frontmatter,
            resolve_wbs=True,
        )
    except FileNotFoundError as exc:
        return False, str(exc)

    if ingest_meta["kept_nodes"] == 0:
        return False, "树索引中未找到可灌入的语义节点。"

    if not new_rules:
        return False, "树索引节点内容过短，未生成有效知识库条目。"

    verify_note = ""
    if verify:
        accuracy, failed_items = verify_ingested_rules(
            new_rules,
            source_pdf_path=source_pdf_path,
            sample_n=verify_sample_n,
            use_llm=verify_use_llm,
        )
        if accuracy < min_verify_accuracy:
            return False, f"PageIndex 灌入质量未达标（抽样准确率 {accuracy:.0%}），已阻止写入。失败样本: {failed_items[:3]}"

        failed_ids = {item["id"] for item in failed_items}
        for rule in new_rules:
            rule["verification_status"] = "warning" if rule["id"] in failed_ids else "passed"
        verify_note = f"；质量校验通过（抽样准确率 {accuracy:.0%}，警告 {len(failed_items)} 条）"

    existing_rules = get_all_rules()
    stale_ids = []
    if replace_existing:
        kept_rules = []
        for rule in existing_rules:
            if rule.get("source_file") == category and rule.get("index_source") == "pageindex":
                stale_ids.append(rule["id"])
            else:
                kept_rules.append(rule)
        existing_rules = kept_rules

    retired_legacy_rules = []
    if retire_legacy:
        retired_legacy_rules = _retire_legacy_rules_for_pageindex(existing_rules, category)

    if stale_ids:
        for stale_id in stale_ids:
            kb_store.delete_rule(stale_id)
    if retired_legacy_rules or new_rules:
        kb_store.upsert_rules_batch(retired_legacy_rules + new_rules)

    final_rules = existing_rules + new_rules
    _write_kb_json(final_rules)

    if collection is not None:
        try:
            if stale_ids:
                collection.delete(ids=stale_ids)
            collection.upsert(
                ids=[r["id"] for r in new_rules],
                documents=[r["content"] for r in new_rules],
                metadatas=[normalize_rule_metadata(r) for r in new_rules],
            )
            if retired_legacy_rules:
                collection.upsert(
                    ids=[r["id"] for r in retired_legacy_rules],
                    documents=[r["content"] for r in retired_legacy_rules],
                    metadatas=[normalize_rule_metadata(r) for r in retired_legacy_rules],
                )
        except Exception as e:
            return False, f"PageIndex 条目向量同步失败: {str(e)}"

    build_bm25_index(final_rules)
    skipped_frontmatter = ingest_meta["skipped_frontmatter"]
    skip_note = f"；已跳过前置信息节点 {skipped_frontmatter} 条" if skipped_frontmatter else ""
    retire_note = f"；已停用同源 legacy OCR/切片条目 {len(retired_legacy_rules)} 条" if retired_legacy_rules else ""
    return True, f"成功从 PageIndex 树索引灌入 {len(new_rules)} 条语义完整节点{skip_note}{verify_note}{retire_note}。"


def ingest_standard_doc(file_path, category, wbs_code, level, custom_tags, ocr_engine="auto"):
    """
    业务架构层：吸收外部标准红线文档，自动化转化为知识库里的原子“审核要点”
    支持了 WBS 标签和 Level 优先权重。
    当 wbs_code 为 'AI_AUTO' 时，调用 LLM 逐条切片打标。
    新增支持 PDF 与 Excel 宽基吞吐。

    参数:
        ocr_engine: OCR 引擎名称 (auto/rapidocr/paddle_vl_1.5/...) — 仅影响 PDF
    """
    ext = file_path.rsplit('.', 1)[-1].lower()

    if ext == 'docx':
        from parsers.word_parser import parse_word_doc_structured
        chunks = parse_word_doc_structured(file_path)
    elif ext == 'pdf':
        from parsers.pdf_parser import parse_pdf_structured
        chunks = parse_pdf_structured(file_path, ocr_engine=ocr_engine)
    elif ext == 'xlsx':
        from parsers.excel_parser import parse_excel_as_scheme_chunks
        chunks = parse_excel_as_scheme_chunks(file_path)
    else:
        return False, "系统当前仅受理 docx / pdf / xlsx 格式的标准源。"

    if isinstance(chunks, str):
        return False, chunks

    chunks = build_overlap_chunks(chunks)
    if not chunks:
        return False, "提取失败：文档内部无有效标准内容。"

    # 查明当前类目下已有的最大 seq_index, 以便增量追加时序号连续
    existing_rules = get_all_rules()

    max_seq_idx = -1
    for r in existing_rules:
        if r.get("source_file") == category:
            max_seq_idx = max(max_seq_idx, int(r.get("seq_index", -1)))

    new_rules = []
    current_seq_idx = max_seq_idx + 1

    for chunk in chunks:
        if len(chunk['text'].strip()) < 15: continue

        final_wbs = wbs_code
        if wbs_code == 'AI_AUTO':
            final_wbs, _, _ = classify_wbs(
                text=chunk['text'],
                category=category,
                heading=chunk.get('heading', ''),
            )

        rule_record = {
            "id": f"KB_{uuid.uuid4().hex[:8].upper()}",
            "category": category,
            "source_file": category,       # V8.0 上下文扩展溯源标识
            "seq_index": current_seq_idx,  # V8.0 自然段落连贯序号
            "wbs_code": final_wbs,
            "level": level,
            "content": f"【{category} - {chunk['heading']}】{chunk['text']}",
            "tags": custom_tags,
            "status": "active",
            "publish_date": "2000-01-01",
            "lifecycle_phase": "施工",
            "is_washed": False,              # 闲时洗库大模型标记
            "condensed_content": "",         # 大模型提纯后的精华
            "ingest_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        new_rules.append(rule_record)
        current_seq_idx += 1

    if not new_rules:
         return False, "提取失败：文档内部无有效标准内容。"

    # 1. 写入主存储 (SQLite) + 降级 JSON 双写
    kb_store.upsert_rules_batch(new_rules)
    existing_rules = get_all_rules()
    _write_kb_json(existing_rules)

    # 2. 刷新到 ChromaDB (向量维度)
    if collection is not None:
        ids = [r["id"] for r in new_rules]
        documents = [r["content"] for r in new_rules]
        metadatas = [normalize_rule_metadata(r) for r in new_rules]
        collection.add(documents=documents, metadatas=metadatas, ids=ids)

    build_bm25_index(existing_rules)

    return True, f"成功向企业知识库注入 {len(new_rules)} 条标准【Lv{level}审核防线】。"

_rules_cache = None
_rules_cache_mtime = 0.0


def get_all_rules():
    """优先从 SQLite 读取，降级到 JSON。"""
    try:
        rules = kb_store.get_all_rules(status_filter=None)
        if rules:
            return [r for r in rules if isinstance(r, dict) and r.get('id')]
    except Exception:
        pass
    # 降级：从 JSON 读取
    if not os.path.exists(KB_FILE_PATH):
        return []
    try:
        with _kb_file_lock(), open(KB_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return _filter_rule_objects(data)
    except Exception:
        return []


def invalidate_rules_cache():
    """兼容接口（SQLite 模式下不需要缓存）。"""
    pass

def delete_rule(rule_id):
    """从本地和 ChromaDB 中删除指定 ID 的规则"""
    rules = get_all_rules()
    new_rules = [r for r in rules if r['id'] != rule_id]

    if len(new_rules) == len(rules):
        return False, "未找到对应的知识库 ID"

    kb_store.delete_rule(rule_id)
    _write_kb_json(new_rules)

    if collection is not None:
        try:
            collection.delete(ids=[rule_id])
        except: pass

    build_bm25_index(new_rules)

    return True, f"成功删除规则 {rule_id}"

def delete_rules_by_category(category):
    """删除某个来源卷宗下的所有规则（SQLite + JSON + ChromaDB 向量同步清除）"""
    rules = get_all_rules()
    to_delete = [r for r in rules if r.get('category', '未知') == category]
    if not to_delete:
        return False, f"未找到来源为 [{category}] 的军规。"

    remaining = [r for r in rules if r.get('category', '未知') != category]
    kb_store.delete_rules_by_category(category)
    _write_kb_json(remaining)

    if collection is not None:
        try:
            ids_to_del = [r['id'] for r in to_delete]
            collection.delete(ids=ids_to_del)
        except: pass

    build_bm25_index(remaining)

    return True, f"已清除来源【{category}】下的 {len(to_delete)} 条军规。"

def get_rule_by_id(rule_id):
    """根据 ID 提取规则详情（优先 SQLite）。"""
    try:
        r = kb_store.get_rule_by_id(rule_id)
        if r:
            return r
    except Exception:
        pass
    rules = get_all_rules()
    for r in rules:
        if r['id'] == rule_id:
            return r
    return None

def update_rule(rule_id, new_content, new_wbs, new_level):
    """热更新现存条款：同步修改内存文档和向量索引元数据，杜绝废话和滞后标准"""
    rules = get_all_rules()
    found = False

    # 1. Update JSON
    for r in rules:
        if r['id'] == rule_id:
            r['content'] = new_content
            r['wbs_code'] = new_wbs
            r['level'] = int(new_level)
            r['ingest_time'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S (已修订)")
            found = True
            break

    if not found:
        return False, f"更新失败：系统中未找到兵力ID '{rule_id}'"

    target_r = [r for r in rules if r['id'] == rule_id][0]
    kb_store.upsert_rule(target_r)
    _write_kb_json(rules)

    # 2. Update ChromaDB
    if collection is not None:
        try:
            target_r = [r for r in rules if r['id'] == rule_id][0]
            collection.update(
                ids=[rule_id],
                documents=[target_r['content']],
                metadatas=[normalize_rule_metadata(target_r)]
            )
        except Exception as e:
            return False, f"向量引擎同步突触失败: {str(e)}"

    build_bm25_index(rules)

    return True, f"✅ 【修订生效】军规 {rule_id} 已完成活体演进！"

def batch_update_rules(updates, deletes):
    """
    前端 st.data_editor 批量更新引擎
    - updates: list of dict, 每个 dict 包含修改后的详细行信息
    - deletes: list of string IDs 待删除的 Rule ID
    """
    rules = get_all_rules()

    rules_map = {r['id']: r for r in rules}

    # 1. 批量拔除 (Deletes)
    if deletes:
        for did in deletes:
            if did in rules_map:
                del rules_map[did]

    # 2. 批量修订 (Updates)
    updated_ids = []
    updated_docs = []
    updated_metas = []

    if updates:
        for u in updates:
            rid = u['id']
            if rid in rules_map:
                r = rules_map[rid]
                r['content'] = u.get('content', r.get('content', ''))
                r['level'] = int(u.get('level', r.get('level', 3)))
                r['wbs_code'] = u.get('wbs_code', r.get('wbs_code', '通用'))
                r['category'] = r.get('category', '未知')
                r['ingest_time'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S (批量修订)")

                updated_ids.append(rid)
                # 提取活体向量核心：永远使用原汁原味全量文本做高维嵌入，把提纯精华放进元数据
                updated_docs.append(r['content'])
                updated_metas.append(normalize_rule_metadata(r))

    # 3. 全局落盘 SQLite + JSON
    final_rules = list(rules_map.values())
    if deletes:
        for did in deletes:
            kb_store.delete_rule(did)
    if updates:
        updated_rules = [rules_map[u['id']] for u in updates if u['id'] in rules_map]
        kb_store.upsert_rules_batch(updated_rules)
    _write_kb_json(final_rules)

    # 4. 批量同步 ChromaDB
    from rag_engine.vector_store import collection
    if collection is not None:
        try:
            if deletes:
                collection.delete(ids=deletes)
            if updated_ids:
                collection.update(
                    ids=updated_ids,
                    documents=updated_docs,
                    metadatas=updated_metas
                )
        except Exception as e:
            return False, f"向量库批量同步异常: {str(e)}"

    # 【V8.0】触发 BM25 混合索引热更新
    try:
        build_bm25_index(final_rules)
    except: pass

    return True, f"批量洗髓完毕：永久抹除 {len(deletes)} 条，升华修订 {len(updated_ids)} 条。"


def replace_all_rules(rules, rebuild_vector=True):
    """Replace the SQLite knowledge base, export JSON backup, and refresh indexes."""
    clean_rules = _filter_rule_objects(rules)
    kb_store.replace_all_rules(clean_rules)
    _write_kb_json(clean_rules)
    build_bm25_index(clean_rules)

    if rebuild_vector:
        try:
            from rag_engine.vector_store import init_vector_db
            init_vector_db(force=True)
        except Exception as exc:
            return False, f"知识库已写入 SQLite/JSON，但向量库重建失败: {exc}"

    return True, f"已用 SQLite 主库替换知识库，共 {len(clean_rules)} 条。"

def get_current_kb_stats():
    """获取当前知识库的词条总量与分类情况（优先 SQLite 高效查询）"""
    try:
        total = kb_store.count_rules(status_filter=None)
        categories = kb_store.get_categories()
        from rag_engine.kb_store import _get_conn
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM rules WHERE is_washed = 1")
        washed = cur.fetchone()[0]
        conn.close()
        return {
            "total_rules": total,
            "total_washed": washed,
            "total_unwashed": total - washed,
            "categories": categories
        }
    except Exception:
        rules = get_all_rules()
        return {
            "total_rules": len(rules),
            "total_washed": len([r for r in rules if r.get('is_washed', False)]),
            "total_unwashed": len([r for r in rules if not r.get('is_washed', False)]),
            "categories": list(set([r.get("category", "未分类") for r in rules]))
        }

def get_unwashed_rules():
    try:
        return kb_store.get_unwashed_rules(limit=10)
    except Exception:
        rules = get_all_rules()
        return [r for r in rules if not r.get('is_washed', False)]

def save_washed_rule(rule_id, condensed_text):
    # SQLite 更新
    kb_store.update_washed(rule_id, condensed_text)
    # JSON 双写
    rules = get_all_rules()
    target = None
    for r in rules:
        if r['id'] == rule_id:
            r['is_washed'] = True
            r['condensed_content'] = condensed_text
            target = r
            break

    if target:
        _write_kb_json(rules)

        from rag_engine.vector_store import collection, build_bm25_index
        if collection is not None:
            try:
                collection.update(
                    ids=[rule_id],
                    documents=[target['content']],
                    metadatas=[normalize_rule_metadata(target)]
                )
            except: pass

        try:
            build_bm25_index(rules)
        except: pass
        return True
    return False

def enrich_rule_llm(rule_content):
    try:
        from auditors.engineering_auditor import call_llm
        sys_prompt = (
            "你是一个极其严谨的国家建筑工程标准解析专家。请阅读以下原始规范切片，"
            "你的唯一任务是进行【无损指代消解与上下文补全】，绝不能删减或总结原文！\n\n"
            "核心纪律：\n"
            "1. 结合文本前面可能带有的【所属专业-章节名】，将孤立句子中的“其”、“该工序”、“本条规定”等代词替换为明确的工程实体主语。\n"
            "2. 100%保留原文中所有的尺寸、温度、公差、毫米数、时间等所有材料极限条件与数值，一个字都不准扔。\n"
            "3. 绝对禁止根据自己的常识捏造或幻想着编写原文未提及的工艺要求。\n"
            "4. 输出的内容必须是一段连贯、独立且完全自包含的工程判定准则，不再依赖任何外部上下文。\n"
            "5. 直接输出重构后的准则即可，不要任何解释与寒暄！"
        )
        res = call_llm(sys_prompt, f"【带标题的原始切片文档】：\n{rule_content}")
        return res.strip() if res else ""
    except Exception as e:
        print(f"Enrich LLM Error: {e}")
        return ""
