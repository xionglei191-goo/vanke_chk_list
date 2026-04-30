import sys, glob, logging
logging.basicConfig(level=logging.INFO)
sys.path.append('auto_review_system')
try:
    from parsers.pdf_parser import _paddle_extract_structured
    pdf_path = [f for f in glob.glob('auto_review_system/temp_uploads/KB_GB5024*')][0]
    print(f"Testing {pdf_path}")
    chunks = _paddle_extract_structured(pdf_path)
    print("Chunks returned SUCCESS!", len(chunks))
    for i, c in enumerate(chunks[:5]):
        print(f"Chunk {i}:", c['heading'])
except Exception as e:
    import traceback
    traceback.print_exc()
