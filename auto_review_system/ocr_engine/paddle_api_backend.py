"""
PaddleOCR 在线异步任务 API 后端
================================
统一封装 4 个在线模型：
  - PaddleOCR-VL-1.5 (最强精度)
  - PaddleOCR-VL (VLM基础版)
  - PP-StructureV3 (版面分析)
  - PP-OCRv5 (仅图片输出)

所有模型共享同一个 API 端点和调用模式，仅 MODEL 参数不同。

环境变量：
  PADDLE_API_TOKEN  — API 认证 Token
  PADDLE_API_URL    — API 端点 (默认 https://paddleocr.aistudio-app.com/api/v2/ocr/jobs)

限制：单次提交不超过 100 页，超过自动分段。
"""
import os
import json
import time
import tempfile
import logging
from typing import List
from pathlib import Path

from ocr_engine.base import OCREngine, OCRPageResult

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv

    APP_DIR = Path(__file__).resolve().parents[1]
    PROJECT_DIR = APP_DIR.parent
    load_dotenv(PROJECT_DIR / ".env")
    load_dotenv(APP_DIR / ".env")
except Exception:
    pass

# ---- 常量 ----
DEFAULT_API_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 120  # 5秒×120 = 10分钟超时


def _max_pages_per_request() -> int:
    try:
        return int(os.environ.get("PADDLE_MAX_PAGES_PER_REQUEST", "100"))
    except (TypeError, ValueError):
        return 100


def _get_api_config():
    """从环境变量读取 API 配置"""
    token = os.environ.get("PADDLE_API_TOKEN", "")
    url = os.environ.get("PADDLE_API_URL", DEFAULT_API_URL)
    return token, url


def _split_pdf(pdf_path: str, max_pages: int | None = None):
    """
    将超过 max_pages 的 PDF 分割为多个临时 PDF 文件。
    返回: [(start_page, temp_pdf_path), ...]
    """
    import pypdfium2 as pdfium

    if max_pages is None:
        max_pages = _max_pages_per_request()

    pdf = pdfium.PdfDocument(pdf_path)
    total_pages = len(pdf)
    pdf.close()

    if total_pages <= max_pages:
        return [(0, pdf_path)]  # 不需要分割

    message = f"  PDF 共 {total_pages} 页，超过 {max_pages} 页限制，自动分段提交"
    logger.info(message)
    print(message)

    segments = []
    temp_dir = tempfile.mkdtemp(prefix="ocr_split_")

    for start in range(0, total_pages, max_pages):
        end = min(start + max_pages, total_pages)

        # 用 pypdfium2 提取子集页面
        src_pdf = pdfium.PdfDocument(pdf_path)
        new_pdf = pdfium.PdfDocument.new()

        for i in range(start, end):
            new_pdf.import_pages(src_pdf, [i])

        segment_path = os.path.join(temp_dir, f"segment_{start}_{end}.pdf")
        with open(segment_path, 'wb') as f:
            new_pdf.save(f)

        new_pdf.close()
        src_pdf.close()

        segments.append((start, segment_path))
        message = f"    分段 {start + 1}~{end} 页 → {segment_path}"
        logger.info(message)
        print(message)

    return segments


def _submit_job(api_url: str, token: str, model_id: str, pdf_path: str,
                optional_payload: dict = None) -> str:
    """提交异步 OCR 任务，返回 jobId"""
    import requests

    headers = {"Authorization": f"bearer {token}"}

    if optional_payload is None:
        # VL系列和StructureV3 的默认选项
        if model_id == "PP-OCRv5":
            optional_payload = {
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useTextlineOrientation": False,
            }
        else:
            optional_payload = {
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useChartRecognition": False,
            }

    data = {
        "model": model_id,
        "optionalPayload": json.dumps(optional_payload),
    }

    with open(pdf_path, "rb") as f:
        files = {"file": f}
        response = requests.post(api_url, headers=headers, data=data, files=files,
                                 timeout=60)

    if response.status_code != 200:
        raise RuntimeError(
            f"PaddleOCR API 提交失败 (HTTP {response.status_code}): {response.text[:500]}"
        )

    job_id = response.json()["data"]["jobId"]
    logger.info(f"  任务已提交: jobId={job_id}, model={model_id}")
    return job_id


def _poll_job(api_url: str, token: str, job_id: str) -> str:
    """轮询任务状态直到完成，返回 JSONL 下载 URL"""
    import requests

    headers = {"Authorization": f"bearer {token}"}
    poll_url = f"{api_url}/{job_id}"

    for attempt in range(MAX_POLL_ATTEMPTS):
        response = requests.get(poll_url, headers=headers, timeout=30)
        if response.status_code != 200:
            logger.warning(f"  轮询请求失败 (HTTP {response.status_code}), 重试...")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        data = response.json()["data"]
        state = data["state"]

        if state == "pending":
            logger.debug(f"  任务等待中... ({attempt + 1}/{MAX_POLL_ATTEMPTS})")
        elif state == "running":
            progress = data.get("extractProgress", {})
            total = progress.get("totalPages", "?")
            done = progress.get("extractedPages", "?")
            logger.info(f"  识别中: {done}/{total} 页")
        elif state == "done":
            progress = data.get("extractProgress", {})
            logger.info(f"  ✅ 任务完成: {progress.get('extractedPages', '?')} 页")
            jsonl_url = data.get("resultUrl", {}).get("jsonUrl", "")
            if not jsonl_url:
                raise RuntimeError("任务完成但未返回结果 URL")
            return jsonl_url
        elif state == "failed":
            error_msg = data.get("errorMsg", "未知错误")
            raise RuntimeError(f"PaddleOCR 任务失败: {error_msg}")
        else:
            logger.warning(f"  未知状态: {state}")

        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"PaddleOCR 任务超时 ({MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS}秒)"
    )


def _download_and_parse_results(jsonl_url: str, model_id: str,
                                page_offset: int = 0) -> List[OCRPageResult]:
    """下载 JSONL 结果并解析为 OCRPageResult 列表"""
    import requests

    response = requests.get(jsonl_url, timeout=120)
    response.raise_for_status()

    results = []
    lines = response.text.strip().split('\n')
    page_num = page_offset

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            result_data = json.loads(line)["result"]
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"  JSONL 行解析失败: {e}")
            continue

        if model_id == "PP-OCRv5":
            # PP-OCRv5 只返回图片，不返回文本
            ocr_results = result_data.get("ocrResults", [])
            for res in ocr_results:
                page_num += 1
                results.append(OCRPageResult(
                    page_num=page_num,
                    markdown=f"[PP-OCRv5: 此页仅生成标注图片，无文本输出]",
                ))
        else:
            # VL-1.5 / VL / PP-StructureV3 返回 Markdown
            layout_results = result_data.get("layoutParsingResults", [])
            for res in layout_results:
                page_num += 1
                md_text = res.get("markdown", {}).get("text", "")
                results.append(OCRPageResult(
                    page_num=page_num,
                    markdown=md_text,
                ))

    return results


class PaddleAsyncEngine(OCREngine):
    """PaddleOCR 在线异步任务 API 引擎"""

    def __init__(self, name: str, display_name: str, model_id: str,
                 returns_text: bool = True):
        super().__init__(
            name=name,
            display_name=display_name,
            engine_type="online",
            returns_text=returns_text,
        )
        self.model_id = model_id

    def is_available(self) -> bool:
        """检查 Token 是否已配置"""
        token, _ = _get_api_config()
        return bool(token)

    def ocr_pdf(self, pdf_path: str) -> List[OCRPageResult]:
        token, api_url = _get_api_config()
        if not token:
            raise RuntimeError(
                f"PaddleOCR API Token 未配置。请设置环境变量 PADDLE_API_TOKEN"
            )

        logger.info(f"[{self.display_name}] 开始处理: {os.path.basename(pdf_path)}")

        # 分段处理超过100页的PDF
        segments = _split_pdf(pdf_path)
        all_results = []

        for page_offset, segment_path in segments:
            try:
                job_id = _submit_job(api_url, token, self.model_id, segment_path)
                jsonl_url = _poll_job(api_url, token, job_id)
                page_results = _download_and_parse_results(
                    jsonl_url, self.model_id, page_offset=page_offset
                )
                all_results.extend(page_results)
            except Exception as e:
                logger.error(f"  分段 {page_offset} 处理失败: {e}")
                # 继续处理下一段，不中断整体流程
            finally:
                # 清理临时分段文件
                if segment_path != pdf_path and os.path.exists(segment_path):
                    try:
                        os.unlink(segment_path)
                    except OSError:
                        pass

        logger.info(f"[{self.display_name}] 完成: {len(all_results)} 页结果")
        return all_results


# ---- 预定义引擎实例 ----
PADDLE_ENGINES = [
    PaddleAsyncEngine(
        name="paddle_vl_1.5",
        display_name="PaddleOCR-VL-1.5 (在线·最强精度)",
        model_id="PaddleOCR-VL-1.5",
        returns_text=True,
    ),
    PaddleAsyncEngine(
        name="paddle_vl",
        display_name="PaddleOCR-VL (在线)",
        model_id="PaddleOCR-VL",
        returns_text=True,
    ),
    PaddleAsyncEngine(
        name="pp_structure_v3",
        display_name="PP-StructureV3 (在线·版面分析)",
        model_id="PP-StructureV3",
        returns_text=True,
    ),
    PaddleAsyncEngine(
        name="pp_ocrv5",
        display_name="PP-OCRv5 (在线·仅图片输出)",
        model_id="PP-OCRv5",
        returns_text=False,
    ),
]
