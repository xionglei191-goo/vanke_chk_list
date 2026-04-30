import os
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["FLAGS_enable_new_executor"] = "0"
os.environ["PADDLE_ONEDNN"] = "0"

import logging
logging.basicConfig(level=logging.INFO)
import glob
pdf_path = [f for f in glob.glob('auto_review_system/temp_uploads/KB_GB5024*')][0]
try:
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_textline_orientation=True, lang="ch", show_log=False)
    res = ocr.predict(pdf_path)
    print("Direct predict SUCCESS!", len(res))
except Exception as e:
    import traceback
    traceback.print_exc()
