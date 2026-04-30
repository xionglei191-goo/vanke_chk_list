import os
import sys

# Ensure backend imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

from rag_engine import kb_manager, vector_store
from rag_engine.kb_manager import (
    _is_pageindex_frontmatter_node,
    _retire_legacy_rules_for_pageindex,
    build_overlap_chunks,
    flatten_tree_leaf_nodes,
    get_retirable_legacy_rules,
    verify_ingested_rules,
)
from parsers.pdf_parser import _flatten_pageindex_leaf_nodes
from auditors.engineering_auditor import _extract_llm_content
from auditors.multi_agent import _selected_scheme_agents, local_triage_chunk
from llm.cache import build_cache_key, get_cached_text, store_cached_text
from llm.client import _parse_streaming_response, _to_anthropic_payload
from rag_engine.kb_quality import assess_rule_quality
from rag_engine.wbs_classifier import classify_wbs
from utils.cost_controls import rag_rerank_mode, triage_mode
from utils.paths import app_relative_path, resolve_runtime_path
from build_tree_index import (
    _find_standard_toc_pages,
    _ocr_results_to_page_texts,
    _page_list_quality,
    _page_text_quality_sufficient,
    _parse_standard_toc,
    _standard_toc_index_extract,
)


class _FakeStreamingResponse:
    def __init__(self, events):
        self.events = events

    def iter_lines(self, decode_unicode=False):
        for event in self.events:
            yield event


class _FakeOCRPage:
    def __init__(self, page_num, text):
        self.page_num = page_num
        self.text = text


def _disable_llm_reranker():
    """测试 RAG 本地逻辑时禁用外部 LLM，避免验证脚本访问网络。"""
    from auditors import engineering_auditor

    engineering_auditor.llm_rerank_rules = lambda query, candidates: candidates

def run_tests():
    print("🚀 开始测试高级 RAG 知识图谱检索引擎...\n")
    
    # 测试1：WBS 祖宗向上追溯算法
    print("🟢 测试 1：WBS 血缘溯源 (04-03-01)")
    ancestry = vector_store.get_wbs_inheritance("04-03-01")
    print(f"输入: 04-03-01\n输出: {ancestry}")
    assert ancestry == ["04-03-01", "04-03", "04", "通用"]
    print("✅ 血缘分型溯源测试通过！\n")

    # 测试2：metadata 默认字段补齐，保证 Chroma where 过滤不会漏召回
    print("🟢 测试 2：知识库 metadata 默认字段归一化")
    long_full_text = "长原文" * 1000
    metadata = vector_store.normalize_rule_metadata({
        "id": "KB_TEST",
        "category": "测试规范",
        "content": "测试内容",
        "full_text": long_full_text,
    })
    assert metadata["status"] == "active"
    assert metadata["lifecycle_phase"] == "施工"
    assert metadata["publish_date"] == "2000-01-01"
    assert metadata["source_file"] == "测试规范"
    assert metadata["seq_index"] == -1
    assert metadata["index_source"] == "legacy"
    assert len(metadata["full_text"]) == min(len(long_full_text), vector_store.METADATA_FULL_TEXT_LIMIT)
    print("✅ metadata 默认字段归一化测试通过！\n")

    # 测试3：邻居扩展不依赖 Chroma/LLM，命中中间切片时应带上前后文
    print("🟢 测试 3：V8 邻居扩展召回")
    _disable_llm_reranker()
    original_collection = vector_store.collection
    original_mode = vector_store.RETRIEVAL_MODE
    try:
        vector_store.collection = None
        vector_store.RETRIEVAL_MODE = "neighbor"
        vector_store.build_bm25_index([
            {
                "id": "KB_PREV",
                "category": "测试规范",
                "source_file": "测试规范",
                "seq_index": 0,
                "wbs_code": "通用",
                "level": 1,
                "content": "previous_context 防水基层应处理干净并保持平整。",
            },
            {
                "id": "KB_HIT",
                "category": "测试规范",
                "source_file": "测试规范",
                "seq_index": 1,
                "wbs_code": "通用",
                "level": 1,
                "content": "unique_anchor 防水卷材搭接宽度不得小于规范要求。",
            },
            {
                "id": "KB_NEXT",
                "category": "测试规范",
                "source_file": "测试规范",
                "seq_index": 2,
                "wbs_code": "通用",
                "level": 1,
                "content": "next_context 收头部位应密封牢固并验收记录。",
            },
        ])
        results = vector_store.retrieve_rules(query="unique_anchor", wbs_code="通用", lifecycle="施工", n_results=1)
        assert "【前序背景】" in results
        assert "【命中段落】" in results
        assert "【后续延伸】" in results
        assert "previous_context" in results
        assert "next_context" in results
        print("✅ 邻居扩展召回测试通过！\n")
    finally:
        vector_store.collection = original_collection
        vector_store.RETRIEVAL_MODE = original_mode

    # 测试4：滑动窗口切片应保留相邻窗口的重叠带
    print("🟢 测试 4：V8 滑动窗口重叠切片")
    overlap_chunks = build_overlap_chunks(
        [
            {"heading": "A段", "text": "A" * 60},
            {"heading": "B段", "text": "B" * 60},
        ],
        chunk_size=80,
        chunk_overlap=20,
    )
    assert len(overlap_chunks) >= 2
    assert overlap_chunks[0]["text"][-20:] in overlap_chunks[1]["text"]
    print("✅ 滑动窗口重叠切片测试通过！\n")

    # 测试5：PageIndex 树叶节点提取与 full_text 投喂策略
    print("🟢 测试 5：V9 PageIndex 树节点投喂")
    leaves = flatten_tree_leaf_nodes({
        "structure": [{
            "title": "第5章 防水",
            "nodes": [{
                "title": "5.1 卷材搭接",
                "node_id": "0001",
                "summary": "pageindex_anchor 卷材搭接要求摘要",
                "text": "PAGEINDEX_FULL_TEXT 卷材搭接完整条款，包含基层、搭接宽度和收头密封要求。",
            }]
        }]
    })
    assert len(leaves) == 1
    assert leaves[0]["title"] == "5.1 卷材搭接"
    assert _is_pageindex_frontmatter_node({"title": "Preface", "_path": ["Preface"]})
    assert not _is_pageindex_frontmatter_node({"title": "5.1 卷材搭接", "_path": ["第5章 防水", "5.1 卷材搭接"]})

    original_collection = vector_store.collection
    try:
        vector_store.collection = None
        pageindex_rules = [
            {
                "id": "KB_PI_TEST",
                "category": "测试规范",
                "source_file": "测试规范",
                "seq_index": 1,
                "wbs_code": "通用",
                "level": 1,
                "content": "pageindex_anchor 卷材搭接要求摘要",
                "full_text": "PAGEINDEX_FULL_TEXT 卷材搭接完整条款，包含基层、搭接宽度和收头密封要求。" + "补充原文" * 800 + "FULL_TEXT_TAIL",
                "index_source": "pageindex",
            }
        ]
        for i in range(8):
            pageindex_rules.append({
                "id": f"KB_PI_OTHER_{i}",
                "category": "测试规范",
                "source_file": "测试规范",
                "seq_index": i + 2,
                "wbs_code": "通用",
                "level": 1,
                "content": f"irrelevant_node_{i} 其他章节摘要",
                "full_text": f"其他完整条款内容 {i}。",
                "index_source": "pageindex",
            })
        vector_store.build_bm25_index(pageindex_rules)
        results = vector_store.retrieve_rules(query="pageindex_anchor", wbs_code="通用", lifecycle="施工", n_results=1)
        assert "【PageIndex语义节点】" in results
        assert "【来源】：测试规范" in results
        assert "PAGEINDEX_FULL_TEXT" in results
        assert "FULL_TEXT_TAIL" in results
        assert results.count("PAGEINDEX_FULL_TEXT") == 1

        bm25_text = vector_store._select_rule_content({
            "content": "短摘要",
            "full_text": "fulltext_only_anchor 不应进入 BM25 搜索文本",
            "index_source": "pageindex",
        })
        assert "fulltext_only_anchor" not in bm25_text

        accuracy, failed = verify_ingested_rules(pageindex_rules[:1], sample_n=1)
        assert accuracy == 1.0
        assert not failed

        bad_accuracy, bad_failed = verify_ingested_rules([{
            "id": "KB_PI_BAD",
            "content": "短",
            "full_text": "短",
            "index_source": "legacy",
        }], sample_n=1)
        assert bad_accuracy == 0.0
        assert bad_failed

        scheme_nodes = _flatten_pageindex_leaf_nodes({
            "structure": [{
                "title": "施工方案",
                "nodes": [{
                    "title": "施工工艺",
                    "summary": "包含施工工艺摘要",
                    "text": "这里是 PageIndex 解析出的完整施工方案语义节点。",
                }]
            }]
        })
        assert len(scheme_nodes) == 1
        assert scheme_nodes[0]["_path"] == ["施工方案", "施工工艺"]
        print("✅ PageIndex 树节点投喂测试通过！\n")
    finally:
        vector_store.collection = original_collection

    # 测试5.1：质量门禁必须随机抽样，而不是永远取前 N 条
    print("🟢 测试 5.1：PageIndex 质量门禁随机抽样")
    original_sample = kb_manager.random.sample
    kb_manager.random.sample = lambda population, k: population[-k:]
    try:
        random_accuracy, random_failed = verify_ingested_rules([
            {
                "id": "KB_PI_GOOD",
                "content": "有效摘要 防水卷材搭接要求",
                "full_text": "有效完整条款 防水卷材搭接宽度和收头密封应符合规范要求。",
                "index_source": "pageindex",
                "start_index": 1,
                "end_index": 1,
            },
            {
                "id": "KB_PI_BAD_RANDOM_SAMPLE",
                "content": "短",
                "full_text": "短",
                "index_source": "legacy",
            },
        ], sample_n=1)
    finally:
        kb_manager.random.sample = original_sample
    assert random_accuracy == 0.0
    assert random_failed and random_failed[0]["id"] == "KB_PI_BAD_RANDOM_SAMPLE"
    print("✅ PageIndex 质量门禁随机抽样测试通过！\n")

    # 测试6：流式 LLM 响应必须按 UTF-8 解析中文，避免 PageIndex 目录标题乱码
    print("🟢 测试 6：LLM 流式中文解码")
    fake_response = _FakeStreamingResponse([
        'data: {"choices":[{"delta":{"content":"总"}}]}'.encode("utf-8"),
        'data: {"choices":[{"delta":{"content":"则"},"finish_reason":"stop"}]}'.encode("utf-8"),
        b"data: [DONE]",
    ])
    parsed = _parse_streaming_response(fake_response)
    assert parsed["choices"][0]["message"]["content"] == "总则"
    print("✅ LLM 流式中文解码测试通过！\n")

    # 测试7：Anthropic /v1/messages 适配应提升 system，并忽略 thinking 块
    print("🟢 测试 7：Anthropic 消息格式适配")
    anthropic_payload = _to_anthropic_payload({
        "model": "qwen3.5-plus",
        "max_tokens": 256,
        "messages": [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "用户问题"},
        ],
    })
    assert anthropic_payload["system"] == "系统提示"
    assert anthropic_payload["messages"] == [{"role": "user", "content": "用户问题"}]
    anthropic_text = _extract_llm_content({
        "content": [
            {"type": "thinking", "thinking": "内部推理"},
            {"type": "text", "text": "最终答案"},
        ]
    })
    assert anthropic_text == "最终答案"
    print("✅ Anthropic 消息格式适配测试通过！\n")

    # 测试8：国标中文目录解析兜底应避开 LLM 卡在 toc_transformer/fix 阶段
    print("🟢 测试 8：PageIndex 国标目录确定性解析")
    toc_items = _parse_standard_toc(
        """
        目 次
        1 总则 ........................................ 1
        2 术语 ........................................ 2
        3 基本规定 .................................... 5
        3.1 设计 ...................................... 5
        附录A 隐蔽工程验收记录 ........................ 120
        本标准用词说明 ................................. 128
        引用标准名录 ................................... 129

        Contents
        1 General provisions ............................ 1
        """
    )
    assert toc_items[0] == {"structure": "1", "title": "总则", "page": 1}
    assert {"structure": "3.1", "title": "设计", "page": 5} in toc_items
    assert {"structure": "附录A", "title": "附录A 隐蔽工程验收记录", "page": 120} in toc_items
    assert toc_items[-1] == {"structure": None, "title": "引用标准名录", "page": 129}
    assert all("General provisions" not in item["title"] for item in toc_items)
    toc_pages = _find_standard_toc_pages([
        ("前言 本标准主要技术内容是：1 总则；2 术语。", 0),
        ("目次\n1 总则 ........ 1\n2 术语 ........ 2", 0),
        ("3 基本规定 ........ 3\n4 抹灰工程 ........ 6", 0),
        ("Contents\n1 General Provisions ........ 1", 0),
        ("1 总则\n1.0.1 为了统一建筑装饰装修工程的质量验收。", 0),
    ], start_page_index=0)
    assert toc_pages == [1, 2, 3]
    toc_index_items = _standard_toc_index_extract(
        [{"structure": "1", "title": "总则"}, {"structure": "2", "title": "术语"}],
        "<physical_index_12>\n1 总则\n正文\n<physical_index_12>\n"
        "<physical_index_13>\n2 术语\n正文\n<physical_index_13>",
    )
    assert toc_index_items == [
        {"structure": "1", "title": "总则", "physical_index": "<physical_index_12>"},
        {"structure": "2", "title": "术语", "physical_index": "<physical_index_13>"},
    ]
    wrapped_toc_items = _parse_standard_toc(
        """
        目
        次
        3
        . 4
        门式钢管脚手架
        · · ·
        ⋯
        11
        3
        . 8
        悬挑式脚手架
        · · ·
        19
        """
    )
    assert wrapped_toc_items == [
        {"structure": "3.4", "title": "门式钢管脚手架", "page": 11},
        {"structure": "3.8", "title": "悬挑式脚手架", "page": 19},
    ]
    compact_toc_items = _parse_standard_toc(
        """
        1总则············································… …1
        3.14施工用电········，···························，…33
        6附录B建筑施工安全分项检查评分表·············… …54
        """
    )
    assert compact_toc_items == [
        {"structure": "1", "title": "总则", "page": 1},
        {"structure": "3.14", "title": "施工用电", "page": 33},
        {"structure": "附录B", "title": "附录B 建筑施工安全分项检查评分表", "page": 54},
    ]
    inline_ocr_toc_items = _parse_standard_toc(
        "## 目 次 6.3 盾构隧道 …… 406.4 沉井 …… 43 "
        "D.4 胶粘带的剥离性能试验方法 …… 79 "
        "本规范用词说明 …… 80 引用标准名录 …… 81 附：条文说明 …… 83"
    )
    assert inline_ocr_toc_items == [
        {"structure": "6.3", "title": "盾构隧道", "page": 40},
        {"structure": "6.4", "title": "沉井", "page": 43},
        {"structure": "D.4", "title": "胶粘带的剥离性能试验方法", "page": 79},
        {"structure": None, "title": "本规范用词说明", "page": 80},
        {"structure": None, "title": "引用标准名录", "page": 81},
        {"structure": None, "title": "附 条文说明", "page": 83},
    ]
    print("✅ PageIndex 国标目录确定性解析测试通过！\n")

    # 测试9：PageIndex 应在 PDF 文本层不足时可切换到 OCR 逐页文本
    print("🟢 测试 9：PageIndex OCR 前置兜底")
    weak_quality = _page_list_quality([("", 0), (None, 0), ("   ", 0)])
    assert weak_quality["total_chars"] == 0
    assert not _page_text_quality_sufficient([("", 0), (None, 0), ("   ", 0)])
    ocr_texts = _ocr_results_to_page_texts([
        _FakeOCRPage(1, "第一页 OCR 总则"),
        _FakeOCRPage(3, "第三页 OCR 术语"),
    ], page_count=3)
    assert ocr_texts == ["第一页 OCR 总则", "", "第三页 OCR 术语"]
    print("✅ PageIndex OCR 前置兜底测试通过！\n")

    # 测试10：PageIndex-first 灌入应停用同源 legacy OCR/切片条目
    print("🟢 测试 10：PageIndex-first legacy 去重")
    sample_rules = [
        {"id": "legacy-1", "source_file": "标准A", "category": "标准A", "status": "active"},
        {"id": "legacy-2", "source_file": "标准A", "category": "标准A", "status": "inactive"},
        {"id": "pi-1", "source_file": "标准A", "category": "标准A", "status": "active", "index_source": "pageindex"},
        {"id": "legacy-other", "source_file": "标准B", "category": "标准B", "status": "active"},
    ]
    assert [r["id"] for r in get_retirable_legacy_rules(sample_rules, "标准A")] == ["legacy-1"]
    retired = _retire_legacy_rules_for_pageindex(sample_rules, "标准A")
    assert [r["id"] for r in retired] == ["legacy-1"]
    assert sample_rules[0]["status"] == "inactive"
    assert sample_rules[0]["retired_by_index_source"] == "pageindex"
    assert sample_rules[2]["status"] == "active"
    assert sample_rules[3]["status"] == "active"
    print("✅ PageIndex-first legacy 去重测试通过！\n")

    # 测试11：balanced 默认应避免 LLM 分流/重排
    print("🟢 测试 11：低成本默认开关")
    old_rerank = os.environ.pop("RAG_RERANK_MODE", None)
    old_triage = os.environ.pop("TRIAGE_MODE", None)
    old_profile = os.environ.pop("AUDIT_COST_PROFILE", None)
    old_agent_routing = os.environ.pop("AGENT_ROUTING_ENABLED", None)
    old_max_agents = os.environ.pop("AGENT_MAX_SCHEME_AGENTS", None)
    try:
        assert rag_rerank_mode() == "local"
        assert triage_mode() == "local"
        assert local_triage_chunk("施工工艺", "屋面防水涂膜施工，完成后进行闭水试验。")
        assert not local_triage_chunk("目录", "第一章 第二章 第三章")
        labels = [label for _, label in _selected_scheme_agents("安全措施", "高处拆除玻璃，设置临电和防坠措施")]
        assert "Agent 2 [施工工艺]" in labels
        assert "Agent 3 [验收标准]" in labels
        assert "Agent 4 [安全管理]" in labels
        assert len(labels) <= 5
    finally:
        if old_rerank is not None:
            os.environ["RAG_RERANK_MODE"] = old_rerank
        if old_triage is not None:
            os.environ["TRIAGE_MODE"] = old_triage
        if old_profile is not None:
            os.environ["AUDIT_COST_PROFILE"] = old_profile
        if old_agent_routing is not None:
            os.environ["AGENT_ROUTING_ENABLED"] = old_agent_routing
        if old_max_agents is not None:
            os.environ["AGENT_MAX_SCHEME_AGENTS"] = old_max_agents
    print("✅ 低成本默认开关测试通过！\n")

    # 测试12：WBS 本地分类和路径解析
    print("🟢 测试 12：WBS 本地分类与路径解析")
    code, confidence, reason = classify_wbs(text="屋面911涂膜防水层施工，保护层恢复", category="屋面防水")
    assert code == "04-03-02"
    assert confidence >= 3
    assert reason
    rel = app_relative_path("auto_review_system/data/results/example.docx")
    assert rel.endswith("data/results/example.docx")
    assert resolve_runtime_path(rel).endswith("data/results/example.docx")
    print("✅ WBS 本地分类与路径解析测试通过！\n")

    # 测试13：LLM cache key 可命中
    print("🟢 测试 13：LLM 响应缓存命中")
    cache_key = build_cache_key("test", "model", "system", "user", {"temperature": 0})
    store_cached_text(cache_key, "cached-answer", "success", "model", "test", ttl_seconds=60)
    assert get_cached_text(cache_key) == "cached-answer"
    print("✅ LLM 响应缓存命中测试通过！\n")

    # 测试14：知识库质量审计应停用明显目录/版权噪声，保留规范条文
    print("🟢 测试 14：知识库质量本地审计")
    noisy = assess_rule_quality({
        "content": "【某规范 - 目录】4. 1 一 （ 4 ) · · · · · · · · · · · · · · · · · · ·",
    })
    assert noisy["critical"]
    assert "symbol_toc" in noisy["flags"] or "mostly_symbols" in noisy["flags"]
    useful = assess_rule_quality({
        "content": "4.2.31 涂膜防水层的平均厚度应符合设计要求，涂膜厚度不应小于设计厚度的80%。检验方法：针刺法或取样量测。",
    })
    assert not useful["critical"]
    assert useful["score"] >= 80
    print("✅ 知识库质量本地审计测试通过！\n")

if __name__ == "__main__":
    run_tests()
