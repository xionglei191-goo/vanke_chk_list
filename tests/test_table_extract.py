import json, tempfile, os, sys
import opendataloader_pdf

pdf_path = "temp_uploads/KB_GB50210-2018 建筑装饰装修工程质量验收标准.pdf"
temp_dir = tempfile.mkdtemp()
opendataloader_pdf.convert(
    input_path=[pdf_path],
    output_dir=temp_dir,
    format="json"
)

files = os.listdir(temp_dir)
if files:
    json_path = os.path.join(temp_dir, files[0])
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        elements = data.get("kids", []) if isinstance(data, dict) else data
        types = set()
        tables = []
        for e in elements:
            if not getattr(e, "get", None): continue
            t = e.get("type")
            types.add(t)
            if t == "table":
                tables.append(e)
        
        print("Unique types:", types)
        if tables:
            print(f"Found {len(tables)} tables.")
            print("Table structure preview:")
            print(json.dumps(tables[0], indent=2, ensure_ascii=False)[:600])
        else:
            print("No tables found.")
else:
    print("Failed to convert PDF.")
