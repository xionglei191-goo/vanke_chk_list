"""
OCR 统一引擎模块
================
支持多引擎注册、手动选型、自动降级。

使用方式:
    from ocr_engine import ocr_extract, list_engines

    # 自动选最佳可用引擎
    pages = ocr_extract("document.pdf", engine="auto")

    # 指定引擎
    pages = ocr_extract("document.pdf", engine="paddle_vl_1.5")

    # 查看已注册引擎
    engines = list_engines()
"""
from ocr_engine.registry import ocr_extract, list_engines

__all__ = ["ocr_extract", "list_engines"]
