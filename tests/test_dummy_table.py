import urllib.request
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib import colors
import tempfile, json, os, opendataloader_pdf

doc = SimpleDocTemplate("t.pdf", pagesize=letter)
data = [['Header 1', 'Header 2'], ['Row 1', 'Data 1'], ['Row 2', 'Data 2']]
t = Table(data)
t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.grey),('GRID',(0,0),(-1,-1),1,colors.black)]))
doc.build([t])

temp_dir = tempfile.mkdtemp()
opendataloader_pdf.convert(input_path=["t.pdf"], output_dir=temp_dir, format="json")
files = [f for f in os.listdir(temp_dir) if f.endswith('.json')]
with open(os.path.join(temp_dir, files[0])) as f:
    d = json.load(f)
for e in (d["kids"] if "kids" in d else d):
    if e.get("type") == "table":
        print("KEYS:", e.keys())
        print("CONTENT:", e.get("content"))
        print("HTML length:", len(e.get("html", "")))
        break
