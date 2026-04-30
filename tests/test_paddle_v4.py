import sys, glob, logging
logging.basicConfig(level=logging.INFO)
sys.path.append('auto_review_system')
try:
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(ocr_version='PP-OCRv4', lang="ch")
    pdf_path = [f for f in glob.glob('auto_review_system/temp_uploads/KB_GB5024*')][0]
    res = ocr.predict(pdf_path)
    print("Direct predict SUCCESS!", len(res))
except Exception as e:
    import traceback
    traceback.print_exc()
