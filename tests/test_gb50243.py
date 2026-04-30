import json, glob, sys
import opendataloader_pdf

pdf_path = "temp_uploads/GB50243-2016 通风与空调工程施工质量验收规范.pdf"
try:
    with open(pdf_path, 'rb') as f:
        pass
except:
    print("Cannot find PDF file", pdf_path)
    sys.exit(1)

import tempfile, os
outdir = tempfile.mkdtemp()
print("Parsing PDF to JSON...", outdir)
opendataloader_pdf.convert(input_path=[pdf_path], output_dir=outdir, format="json")

files = glob.glob(outdir + '/*.json')
if not files:
    print("No JSON generated!")
    sys.exit(1)

with open(files[0]) as f:
    data = json.load(f)

elements = data.get("kids", []) if isinstance(data, dict) else data

tables = []
for e in elements:
    if not isinstance(e, dict): continue
    if e.get('type') == 'table':
        tables.append(e)

if not tables:
    print("No tables found in this document!")
    sys.exit(0)

print(f"Found {len(tables)} tables.")
# Show structure of the first 2 tables
for idx, tbl in enumerate(tables[:2]):
    print(f"\n--- Table {idx+1} keys: ---")
    print(tbl.keys())
    # Let's see rows, cells, and kids
    rows = tbl.get('rows', [])
    print(f"Number of rows: {len(rows)}")
    for r in rows[:2]:  # first 2 rows
        print(" Row Type:", r.get('type'), "Row keys:", r.keys())
        cells = r.get('cells', [])
        print(f" Cells count: {len(cells)}")
        for c in cells[:2]: # first 2 cells
            print("  Cell keys:", c.keys())
            kids = c.get('kids', [])
            print(f"  Kids count: {len(kids)}")
            for k in kids:
                print("   Kid content:", json.dumps(k, ensure_ascii=False))
