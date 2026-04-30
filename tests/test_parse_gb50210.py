import json, tempfile, os, sys
sys.path.append('.')
try:
    from parsers.pdf_parser import parse_pdf_structured
    chunks = parse_pdf_structured("temp_uploads/KB_GB50210-2018 建筑装饰装修工程质量验收标准.pdf")
    if not chunks:
        print("Empty chunks from OpenDataLoader")
    else:
        for i, c in enumerate(chunks[:3]):
            print(f"[{i}] HEAD: {c['heading']}")
            print(c['text'][:200])
            print("---")
except Exception as e:
    import traceback
    traceback.print_exc()
