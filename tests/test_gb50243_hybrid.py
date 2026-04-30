import json, glob, sys, os
import opendataloader_pdf
pdf_path = "temp_uploads/GB50243-2016 通风与空调工程施工质量验收规范.pdf"
import tempfile
outdir = tempfile.mkdtemp()
opendataloader_pdf.convert(input_path=[pdf_path], output_dir=outdir, format="json", hybrid=True)
files = glob.glob(outdir + '/*.json')
with open(files[0]) as f:
    data = json.load(f)
elements = data.get("kids", []) if isinstance(data, dict) else data
tables = [e for e in elements if isinstance(e, dict) and e.get('type') == 'table']
for idx, tbl in enumerate(tables[:2]):
    rows = tbl.get('rows', [])
    for r in rows[:1]:
        cells = r.get('cells', [])
        for c in cells[:2]:
            kids = c.get('kids', [])
            print(f"Table {idx+1} kids:", [k.get('content') for k in kids if k.get('content')])
