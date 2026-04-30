import sys, glob, logging, os
sys.path.append('.')
from parsers.pdf_parser import _pdf_pages_to_images
logging.basicConfig(level=logging.INFO)
from rapidocr_onnxruntime import RapidOCR
ocr = RapidOCR()
pdf_path = [f for f in glob.glob('temp_uploads/KB_GB5024*')][0]

import tempfile
page_images = _pdf_pages_to_images(pdf_path)
print("Pages:", len(page_images))
with tempfile.TemporaryDirectory() as td:
    img_path = os.path.join(td, "test.png")
    page_images[0][1].save(img_path)
    result, _ = ocr(img_path)
    if result:
        print("RapidOCR extracted lines:", len(result))
        for line in result[:5]:
            print(line[1]) # text inside 
