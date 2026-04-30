import sys, glob, logging
logging.basicConfig(level=logging.INFO)
sys.path.append('auto_review_system')
from parsers.pdf_parser import _paddle_extract_structured
pdf_path = [f for f in glob.glob('auto_review_system/temp_uploads/KB_GB5024*')][0]
print(f"Testing {pdf_path}")
chunks = _paddle_extract_structured(pdf_path)
print(f"Chunks returned: {len(chunks)}")
