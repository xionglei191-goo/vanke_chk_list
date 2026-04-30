"""
PDF 双引擎解析器 — 智能路由架构
=================================
引擎 1 (主力): OpenDataLoader-PDF  → 数字原生 PDF 极速结构提取
引擎 2 (兜底): ocr_engine 模块     → 多引擎 OCR (本地/在线可选)

自动路由策略:
  1. OpenDataLoader 先行提取
  2. 若提取结果文本量过低 (疑似扫描件/CID字体)，自动切换 OCR 兜底
  3. 可通过环境变量 PDF_ENGINE=ocr 强制使用 OCR
  4. OCR 引擎可通过参数指定 (auto/rapidocr/paddle_vl_1.5/...)
"""

import os
import re
import tempfile
import json
import logging
import sys
from pathlib import Path

try:
    import opendataloader_pdf
    _ODL_AVAILABLE = True
except ImportError:
    _ODL_AVAILABLE = False

logger = logging.getLogger(__name__)


# ============================================================
# PageIndex 可选路径（默认关闭）
# ============================================================
from utils.tree_utils import (
    tree_roots as _pageindex_roots,
    tree_children as _pageindex_children,
    flatten_tree_leaf_nodes as _flatten_pageindex_leaf_nodes,
)


def _pageindex_extract_structured(pdf_path):
    """
    可选 PageIndex 方案解析路径。
    仅在 SCHEME_PARSER=pageindex 或 PDF_ENGINE=pageindex 时启用，默认不影响现有 ODL/OCR。
    """
    pageindex_root = os.getenv("PAGEINDEX_ROOT")
    if pageindex_root:
        sys.path.insert(0, str(Path(pageindex_root).expanduser().resolve()))

    model = os.getenv("SCHEME_PAGEINDEX_MODEL") or os.getenv("PAGEINDEX_MODEL") or os.getenv("LLM_MODEL") or "gpt-5.4"

    from scripts.build_tree_index import _configure_llm_env, _configure_pageindex_import, _load_pageindex, _preflight_llm

    _configure_llm_env(use_project_llm=True)
    _configure_pageindex_import(pageindex_root)
    if os.getenv("SCHEME_PAGEINDEX_PREFLIGHT", "1").strip().lower() not in ("0", "false", "no"):
        _preflight_llm(model)

    page_index = _load_pageindex()

    result = page_index(
        doc=pdf_path,
        model=model,
        if_add_node_summary="yes",
        if_add_node_text="yes",
        if_add_node_id="yes",
        if_add_doc_description="yes",
    )

    chunks = []
    for node in _flatten_pageindex_leaf_nodes(result):
        title = str(node.get("title") or node.get("node_title") or "未命名节点").strip()
        summary = str(node.get("summary") or node.get("prefix_summary") or "").strip()
        text = str(node.get("text") or node.get("full_text") or node.get("content") or summary).strip()
        if len(text) < 15 and len(summary) < 15:
            continue
        path = " > ".join(node.get("_path", [title]))
        chunks.append({
            "heading": path,
            "text": text or summary,
            "summary": summary,
            "parser_source": "pageindex",
            "node_id": str(node.get("node_id") or node.get("id") or ""),
            "start_index": node.get("start_index", node.get("start_page", -1)),
            "end_index": node.get("end_index", node.get("end_page", -1)),
        })

    return chunks


# ============================================================
# OCR 兜底路径（调用 ocr_engine 统一模块）
# ============================================================
def _ocr_fallback_structured(pdf_path, engine="auto"):
    """OCR 兜底：调用统一 OCR 模块提取 PDF 结构化内容"""
    from ocr_engine import ocr_extract

    pages = ocr_extract(pdf_path, engine=engine)
    chunks = []
    for page in pages:
        content = page.text  # 优先 markdown，否则 lines 拼接
        if content and len(content.strip()) > 15:
            chunks.append({
                "heading": f"第 {page.page_num} 页 (OCR提取)",
                "text": content
            })
    return chunks


def _ocr_fallback_cost_context(pdf_path, engine="auto"):
    """OCR 兜底：提取造价上下文"""
    from ocr_engine import ocr_extract

    pages = ocr_extract(pdf_path, engine=engine)
    all_text = []
    for page in pages:
        content = page.text
        if content and content.strip():
            all_text.append(f"--- 造价明细 P{page.page_num} (OCR兜底) ---")
            all_text.append(content)
    return "\n".join(all_text)


# ============================================================
# OpenDataLoader 主引擎实现
# ============================================================
def _odl_extract_structured(pdf_path):
    """OpenDataLoader 路径：快速结构化 JSON 提取"""
    if not _ODL_AVAILABLE:
        return []
    chunks = []
    with tempfile.TemporaryDirectory() as temp_dir:
        opendataloader_pdf.convert(
            input_path=[pdf_path],
            output_dir=temp_dir,
            format="json"
        )

        # 定位输出 JSON
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        expected_json = os.path.join(temp_dir, f"{base_name}.json")
        json_file = expected_json if os.path.exists(expected_json) else None
        if not json_file:
            for f in os.listdir(temp_dir):
                if f.endswith(".json"):
                    json_file = os.path.join(temp_dir, f)
                    break
        if not json_file:
            return []

        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 适配 opendataloader-pdf 的 JSON 输出结构
        elements = data.get("kids", []) if isinstance(data, dict) else data
        
        current_heading = "文档开头"
        current_text = []

        def commit(heading, text_arr):
            if text_arr:
                content = "\n".join(text_arr)
                if len(content) > 15:
                    chunks.append({"heading": heading, "text": content})

        def _extract_odl_table(element):
            rows = element.get('rows', [])
            if not rows:
                return ""
            
            table_lines = []
            has_actual_text = False  # 如果全是空内容，丢弃这个空壳表格
            
            for row in rows:
                if not isinstance(row, dict) or row.get('type') != 'table row': continue
                cells = row.get('cells', [])
                row_content = []
                for cell in cells:
                    cell_text = []
                    for kid in cell.get('kids', []):
                        if kid.get('content'):
                            text_fragment = str(kid['content']).strip()
                            cell_text.append(text_fragment)
                            if text_fragment:
                                has_actual_text = True
                    cell_str = " ".join(cell_text).replace("|", ",").replace("\n", " ")
                    row_content.append(cell_str)
                table_lines.append("| " + " | ".join(row_content) + " |")
                
            if not table_lines or not has_actual_text:
                return ""
                
            header_col_count = table_lines[0].count('|') - 1
            if header_col_count > 0:
                table_lines.insert(1, "| " + " | ".join(["---"] * header_col_count) + " |")
            return "\n".join(table_lines)

        for element in elements:
            if not getattr(element, "get", None):
                continue
            e_type = element.get('type')
            content = element.get('content', '')

            if e_type == 'heading':
                commit(current_heading, current_text)
                current_text = []
                current_heading = str(content).strip()
            elif e_type in ('paragraph', 'list'):
                if content:
                    current_text.append(content)
            elif e_type == 'table':
                table_content = _extract_odl_table(element)
                if table_content:
                    commit(current_heading, current_text)
                    current_text = []
                    page_num = element.get('page number', '?')
                    chunks.append({
                        "heading": f"{current_heading} (表格摘要 P{page_num})",
                        "text": table_content
                    })
            elif e_type in ('image', 'picture'):
                desc = element.get('description', '')
                if desc:
                    current_text.append(f"[图纸批注: {desc}]")

        commit(current_heading, current_text)

    return chunks


def _odl_extract_cost_context(pdf_path):
    """OpenDataLoader 路径：Markdown 表格提取"""
    if not _ODL_AVAILABLE:
        return ""
    with tempfile.TemporaryDirectory() as temp_dir:
        opendataloader_pdf.convert(
            input_path=[pdf_path],
            output_dir=temp_dir,
            format="markdown"
        )
        md_file = None
        for f in os.listdir(temp_dir):
            if f.endswith(".md"):
                md_file = os.path.join(temp_dir, f)
                break
        if not md_file:
            return ""
        with open(md_file, 'r', encoding='utf-8') as f:
            return f.read()


# ============================================================
# 质量评估（自动路由决策核心）
# ============================================================
def _is_quality_sufficient(chunks, page_count=1):
    """
    评估 OpenDataLoader 提取结果的质量。
    如果文本总量太少或者平均每页字数极低（扫描件/CID乱码的典型特征），
    返回 False 触发 OCR 视觉图片提取兜底。
    """
    if not isinstance(chunks, list) or len(chunks) == 0:
        return False
    
    # 统计有效字符：过滤掉 Markdown 表格产生的空白、竖线和减号
    effective_len = 0
    for c in chunks:
        text = str(c.get('text', ''))
        # 移除 markdown 制表符和常见空白
        filtered = text.translate(str.maketrans('', '', '|-\n \t'))
        effective_len += len(filtered)
        
    # 计算文本密度
    avg_chars_per_page = effective_len / max(1, page_count)
    
    if effective_len < 50 or avg_chars_per_page < 20:
        logger.warning(f"检测到提取文本密度极低 (总计{effective_len}字 / {page_count}页 = {avg_chars_per_page:.1f}字/页)")
        logger.warning("由于极大可能遭遇无 Unicode 映射表或全篇扫描件，自动降级切换至 OCR 视觉引擎兜底")
        return False
        
    return True


# ============================================================
# 对外公开接口（保持完全向后兼容）
# ============================================================
def parse_pdf_structured(pdf_path, ocr_engine="auto"):
    """
    双引擎自动路由 PDF 结构化提取。

    路由策略:
      1. 环境变量 SCHEME_PARSER=pageindex 或 PDF_ENGINE=pageindex → 强制使用 PageIndex
      2. 环境变量 PDF_ENGINE=ocr → 强制使用 OCR
      3. 默认使用 OpenDataLoader 快速提取
      4. 若提取结果质量不足 (疑为扫描件)，自动切换 OCR 兜底
    
    参数:
        pdf_path: PDF 文件路径
        ocr_engine: OCR 引擎名称 (auto/rapidocr/paddle_vl_1.5/...)

    返回: list[{"heading": str, "text": str}] 或 错误字符串
    """
    forced_engine = os.environ.get("PDF_ENGINE", "").lower()
    scheme_parser = os.environ.get("SCHEME_PARSER", "").lower()
    fname = os.path.basename(pdf_path)

    try:
        # ---- 可选 PageIndex 模式 ----
        if scheme_parser == "pageindex" or forced_engine in ("pageindex", "page_index", "tree"):
            logger.info(f"[PDF] 🌳 强制使用 PageIndex 方案树解析: {fname}")
            chunks = _pageindex_extract_structured(pdf_path)
            return chunks if chunks else "PageIndex 未能提取到有效方案节点。"

        # ---- 强制 OCR 模式 ----
        if forced_engine in ("paddleocr", "ocr"):
            logger.info(f"[PDF] 🔍 强制使用 OCR 引擎: {fname}")
            chunks = _ocr_fallback_structured(pdf_path, engine=ocr_engine)
            return chunks if chunks else "OCR 引擎未能提取到有效内容。"

        # ---- 默认路径: OpenDataLoader 先行 ----
        if not _ODL_AVAILABLE:
            logger.info(f"[PDF] ℹ️ OpenDataLoader 未安装，直接使用 OCR: {fname}")
            chunks = _ocr_fallback_structured(pdf_path, engine=ocr_engine)
            return chunks if chunks else "PDF 引擎未就绪，请安装 opendataloader-pdf 或配置 OCR 引擎。"

        chunks = []
        logger.info("尝试执行 OpenDataLoader 结构化提取...")
        try:
            chunks = _odl_extract_structured(pdf_path)
            
            # 挂载底层 PDF 提取真实页数以计算密度
            try:
                import pypdfium2 as pdfium
                with pdfium.PdfDocument(pdf_path) as pdf:
                    page_count = len(pdf)
            except Exception as e:
                logger.warning(f"无法获取 PDF 页数进行密度计算，默认回退单页评估: {e}")
                page_count = 1
                
            if _is_quality_sufficient(chunks, page_count):
                logger.info(f"✅ OpenDataLoader 结构提取有效，共抓取 {len(chunks)} 个片段块 (文档 {page_count} 页)。")
                return chunks
            else:
                logger.warning("⚠️ OpenDataLoader 提取质量不达标 (可能遭遇扫描件/CID字体)。切换 OCR 兜底...")
        except Exception as e:
            logger.warning(f"⚠️ OpenDataLoader 提取失败: {e}。切换 OCR 兜底...")

        # ---- 质量不达标，启用 OCR 兜底 ----
        logger.info(f"[PDF] ⚠️ 切换 OCR 兜底引擎 ({ocr_engine}): {fname}")
        ocr_chunks = _ocr_fallback_structured(pdf_path, engine=ocr_engine)

        if ocr_chunks and len(ocr_chunks) > len(chunks if isinstance(chunks, list) else []):
            logger.info(f"[PDF] ✅ OCR 兜底成功，提取 {len(ocr_chunks)} 切片")
            return ocr_chunks

        # 两个引擎都提取到了一些内容，择优返回
        return chunks if chunks else "未从 PDF 提取到有价值结构文本。"

    except Exception as e:
        logger.error(f"PDF 双引擎解析异常: {e}")
        return f"PDF文档解析失败: {str(e)}"


def parse_pdf_as_cost_context(pdf_path, ocr_engine="auto"):
    """
    提取 PDF 核心表格数据作为全局造价上下文。
    同样采用双引擎策略：OpenDataLoader 优先，OCR 兜底。

    参数:
        pdf_path: PDF 文件路径
        ocr_engine: OCR 引擎名称 (auto/rapidocr/paddle_vl_1.5/...)
    """
    forced_engine = os.environ.get("PDF_ENGINE", "").lower()

    try:
        # ---- 强制 OCR ----
        if forced_engine in ("paddleocr", "ocr"):
            result = _ocr_fallback_cost_context(pdf_path, engine=ocr_engine)
            if result:
                return f"--- OCR 智能提取造价上下文 ---\n{result}"
            return "OCR 引擎未能提取表格数据。"

        # ---- OpenDataLoader 主路径 ----
        if not _ODL_AVAILABLE:
            result = _ocr_fallback_cost_context(pdf_path, engine=ocr_engine)
            return f"--- OCR 智能提取造价上下文 ---\n{result}" if result else "PDF 引擎未就绪。"
        content = _odl_extract_cost_context(pdf_path)

        if len(content.strip()) >= 50:
            return f"--- 智能提取造价上下文 (Markdown 格式) ---\n{content}"

        # ---- OCR 兜底 ----
        logger.info(f"[PDF] ⚠️ 造价表格提取量不足，尝试 OCR 兜底...")
        ocr_result = _ocr_fallback_cost_context(pdf_path, engine=ocr_engine)
        if ocr_result and len(ocr_result) > len(content):
            return f"--- OCR 智能提取造价上下文 ---\n{ocr_result}"

        return f"--- 智能提取造价上下文 (Markdown 格式) ---\n{content}" if content else "造价上下文提取失败。"

    except Exception as e:
        logger.error(f"造价上下文提取异常: {e}")
        return f"PDF表格解析失败: {str(e)}"
