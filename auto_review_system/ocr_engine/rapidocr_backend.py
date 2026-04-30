"""
RapidOCR 本地引擎后端
====================
基于 rapidocr_onnxruntime 的 CPU 端 OCR 引擎。
包含 Y轴行聚类 + X轴列排序 的坐标版面还原算法。
"""
import os
import tempfile
import logging
from typing import List

from ocr_engine.base import OCREngine, OCRPageResult

logger = logging.getLogger(__name__)

# ---- 懒加载单例 ----
_rapid_ocr_instance = None
_init_attempted = False


def _get_rapid_ocr():
    """懒加载 RapidOCR 实例，避免重复初始化。未安装时返回 None。"""
    global _rapid_ocr_instance, _init_attempted
    if _init_attempted:
        return _rapid_ocr_instance
    _init_attempted = True
    try:
        from rapidocr_onnxruntime import RapidOCR
        _rapid_ocr_instance = RapidOCR()
        logger.info("✅ RapidOCR (ONNX后端) 引擎加载成功")
    except ImportError:
        logger.info("ℹ️ RapidOCR 未安装。可选安装: pip install rapidocr_onnxruntime")
    except Exception as e:
        logger.warning(f"⚠️ RapidOCR 初始化异常: {e}")
    return _rapid_ocr_instance


def _pdf_pages_to_images(pdf_path):
    """使用 pypdfium2 将 PDF 每页渲染为 300DPI 的 PIL 图片列表"""
    import pypdfium2 as pdfium
    pages = []
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=300 / 72)  # 300 DPI
            pil_image = bitmap.to_pil()
            pages.append((i + 1, pil_image))
    finally:
        pdf.close()
    return pages


def _extract_text_from_rapidocr_result(result):
    """
    从 RapidOCR 结果中提取文本行，利用坐标信息还原版面排列。
    结果格式: [[[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], '文字', conf], ...]

    核心算法：
      1. 提取每个文本块的 y_center, x_center, height
      2. 按 y_center 排序后，将垂直距离 < 0.6 * avg_height 的块聚为同一行
      3. 行内按 x_center 从左到右排序
      4. 行内 ≥3 个块 → Markdown 表格行 (| cell1 | cell2 |)
      5. 行内 1~2 个块 → 普通段落文本拼接
    """
    if not result:
        return []

    # ---- Step 1: 解析坐标并构建 block 列表 ----
    blocks = []
    try:
        for res in result:
            if not isinstance(res, (list, tuple)) or len(res) < 2:
                continue
            text = str(res[1]).strip()
            if not text:
                continue

            coords = res[0]
            if (isinstance(coords, (list, tuple)) and len(coords) >= 4
                    and all(isinstance(p, (list, tuple)) and len(p) >= 2 for p in coords)):
                ys = [float(p[1]) for p in coords]
                xs = [float(p[0]) for p in coords]
                y_center = sum(ys) / len(ys)
                x_center = sum(xs) / len(xs)
                height = max(ys) - min(ys)
                blocks.append({
                    'text': text,
                    'y_center': y_center,
                    'x_center': x_center,
                    'height': max(height, 1.0),
                })
            else:
                blocks.append({
                    'text': text,
                    'y_center': 0.0,
                    'x_center': 0.0,
                    'height': 20.0,
                })
    except Exception as e:
        logger.debug(f"RapidOCR 坐标解析异常，回退为纯文本拼接: {e}")
        fallback = []
        for res in result:
            if isinstance(res, (list, tuple)) and len(res) >= 2:
                fallback.append(str(res[1]))
        return fallback

    if not blocks:
        return []

    # ---- Step 2: 按 y_center 排序，然后做行聚类 ----
    blocks.sort(key=lambda b: b['y_center'])
    avg_height = sum(b['height'] for b in blocks) / len(blocks)
    row_threshold = max(avg_height * 0.6, 8.0)

    rows = []
    current_row = [blocks[0]]

    for blk in blocks[1:]:
        if abs(blk['y_center'] - current_row[0]['y_center']) <= row_threshold:
            current_row.append(blk)
        else:
            rows.append(current_row)
            current_row = [blk]
    rows.append(current_row)

    # ---- Step 3: 行内按 x_center 排序，智能判桌输出 ----
    output_lines = []
    in_table_zone = False
    table_col_count = 0

    for row_blocks in rows:
        row_blocks.sort(key=lambda b: b['x_center'])
        texts = [b['text'] for b in row_blocks]

        if len(texts) >= 3:
            sanitized = [t.replace('|', ',').replace('\n', ' ') for t in texts]
            table_line = '| ' + ' | '.join(sanitized) + ' |'

            if not in_table_zone:
                in_table_zone = True
                table_col_count = len(sanitized)
                output_lines.append(table_line)
                output_lines.append('| ' + ' | '.join(['---'] * table_col_count) + ' |')
            else:
                output_lines.append(table_line)
        else:
            if in_table_zone:
                in_table_zone = False
                table_col_count = 0
            output_lines.append(' '.join(texts))

    return output_lines


class RapidOCREngine(OCREngine):
    """RapidOCR 本地 OCR 引擎（ONNX Runtime, CPU友好）"""

    def __init__(self):
        super().__init__(
            name="rapidocr",
            display_name="RapidOCR (本地·CPU)",
            engine_type="local",
            returns_text=True,
        )

    def is_available(self) -> bool:
        try:
            from rapidocr_onnxruntime import RapidOCR
            return True
        except ImportError:
            return False

    def ocr_pdf(self, pdf_path: str) -> List[OCRPageResult]:
        ocr = _get_rapid_ocr()
        if ocr is None:
            logger.warning("RapidOCR 不可用，返回空结果")
            return []

        results = []
        try:
            page_images = _pdf_pages_to_images(pdf_path)
            logger.info(f"  [RapidOCR] 共 {len(page_images)} 页待识别")

            for page_num, pil_img in page_images:
                with tempfile.NamedTemporaryFile(suffix=".png") as tf:
                    pil_img.save(tf.name)
                    ocr_result, _ = ocr(tf.name)
                    lines = _extract_text_from_rapidocr_result(ocr_result)

                    if lines:
                        results.append(OCRPageResult(
                            page_num=page_num,
                            lines=[line.strip() for line in lines if line.strip()],
                        ))
        except Exception as e:
            logger.error(f"RapidOCR 提取异常: {e}")

        return results
