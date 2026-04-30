import os
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_allocator_strategy"] = "naive_best_fit"
import sys, glob, logging
logging.basicConfig(level=logging.INFO)
sys.path.append('auto_review_system')
from parsers.pdf_parser import _paddle_extract_structured
pdf_path = [f for f in glob.glob('auto_review_system/temp_uploads/KB_GB5024*')][0]
print(f"Testing {pdf_path}")
try:
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False, use_mkldnn=False)
    res = ocr.predict(pdf_path)
    print("Direct predict SUCCESS!")
except Exception as e:
    print(f"Paddle crashed: {e}")
