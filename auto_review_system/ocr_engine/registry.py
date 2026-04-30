"""
OCR 引擎注册表 + 路由
====================
管理所有已注册引擎，提供统一调用入口和引擎发现。
"""
import logging
from typing import List, Dict, Optional

from ocr_engine.base import OCREngine, OCRPageResult

logger = logging.getLogger(__name__)

# ---- 引擎注册表 ----
_ENGINES: Dict[str, OCREngine] = {}
_initialized = False


def _ensure_initialized():
    """首次调用时自动发现并注册所有引擎"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    # 注册本地引擎
    try:
        from ocr_engine.rapidocr_backend import RapidOCREngine
        register_engine(RapidOCREngine())
    except Exception as e:
        logger.debug(f"RapidOCR 引擎注册跳过: {e}")

    # 注册在线引擎
    try:
        from ocr_engine.paddle_api_backend import PADDLE_ENGINES
        for engine in PADDLE_ENGINES:
            register_engine(engine)
    except Exception as e:
        logger.debug(f"PaddleOCR 引擎注册跳过: {e}")

    logger.info(f"OCR 引擎注册完成: {list(_ENGINES.keys())}")


def register_engine(engine: OCREngine):
    """注册一个 OCR 引擎"""
    _ENGINES[engine.name] = engine
    logger.debug(f"  注册引擎: {engine.name} ({engine.display_name})")


def list_engines() -> List[Dict]:
    """
    返回所有已注册引擎的信息，供 UI 下拉框使用。

    返回格式:
    [
        {
            "name": "rapidocr",
            "display_name": "RapidOCR (本地·CPU)",
            "type": "local",
            "available": True,
            "returns_text": True,
        },
        ...
    ]
    """
    _ensure_initialized()
    return [
        {
            "name": e.name,
            "display_name": e.display_name,
            "type": e.engine_type,
            "available": e.is_available(),
            "returns_text": e.returns_text,
        }
        for e in _ENGINES.values()
    ]


# ---- auto 模式优先级 ----
_AUTO_PRIORITY = [
    "paddle_vl_1.5",    # 1. 最强在线 VLM
    "pp_structure_v3",  # 2. 版面分析在线
    "paddle_vl",        # 3. VLM 基础版
    "rapidocr",         # 4. 本地兜底
]


def ocr_extract(pdf_path: str, engine: str = "auto") -> List[OCRPageResult]:
    """
    统一 OCR 提取入口。

    参数:
        pdf_path: PDF 文件路径
        engine: 引擎名称，或 "auto" 自动选最佳可用引擎

    返回:
        list[OCRPageResult] — 逐页 OCR 结果

    auto 优先级:
        1. paddle_vl_1.5 (最强精度，在线可用时)
        2. pp_structure_v3 (版面分析，在线可用时)
        3. paddle_vl (VLM基础版，在线可用时)
        4. rapidocr (本地兜底，始终可用)
    """
    _ensure_initialized()

    if engine == "auto":
        # 按优先级尝试可用引擎
        for name in _AUTO_PRIORITY:
            eng = _ENGINES.get(name)
            if eng and eng.is_available() and eng.returns_text:
                logger.info(f"[OCR auto] 选中引擎: {eng.display_name}")
                try:
                    return eng.ocr_pdf(pdf_path)
                except Exception as e:
                    logger.warning(f"[OCR auto] {eng.name} 失败: {e}, 尝试下一个...")
                    continue

        logger.error("[OCR auto] 所有引擎均不可用")
        return []

    # 指定引擎
    eng = _ENGINES.get(engine)
    if not eng:
        available = [e.name for e in _ENGINES.values()]
        raise ValueError(
            f"未知 OCR 引擎: '{engine}'. 可用引擎: {available}"
        )

    if not eng.is_available():
        raise RuntimeError(
            f"OCR 引擎 '{eng.display_name}' 当前不可用。"
            f"{'请设置环境变量 PADDLE_API_TOKEN' if eng.engine_type == 'online' else '请安装 rapidocr_onnxruntime'}"
        )

    if not eng.returns_text:
        logger.warning(
            f"⚠️ {eng.display_name} 仅返回图片标注，不包含可用文本。"
            f"建议选择 VL 系列或 PP-StructureV3。"
        )

    return eng.ocr_pdf(pdf_path)
