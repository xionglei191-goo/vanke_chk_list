"""
OCR 引擎基础类型定义
"""
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class OCRPageResult:
    """单页 OCR 结果"""
    page_num: int
    markdown: str = ""                          # 整页 Markdown (在线API直出)
    lines: List[str] = field(default_factory=list)  # 按行文本 (本地OCR聚类输出)

    @property
    def text(self) -> str:
        """返回最佳文本：优先 markdown，否则 lines 拼接"""
        if self.markdown:
            return self.markdown
        return "\n".join(self.lines) if self.lines else ""


class OCREngine(ABC):
    """OCR 引擎抽象基类"""

    def __init__(self, name: str, display_name: str, engine_type: str,
                 returns_text: bool = True):
        self.name = name
        self.display_name = display_name
        self.engine_type = engine_type      # "local" / "online"
        self.returns_text = returns_text    # PP-OCRv5 在线版 = False

    @abstractmethod
    def ocr_pdf(self, pdf_path: str) -> List[OCRPageResult]:
        """对整份 PDF 执行 OCR，返回逐页结果"""
        ...

    def is_available(self) -> bool:
        """检查引擎是否可用（依赖已安装/API 可达）"""
        return True

    def __repr__(self):
        return f"<OCREngine:{self.name} ({self.engine_type})>"
