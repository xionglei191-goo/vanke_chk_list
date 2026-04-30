import opendataloader_pdf
import json
import tempfile
import os
import pypdfium2

# create dummy pdf
from reportlab.pdfgen import canvas
c = canvas.Canvas("dummy.pdf")
c.drawString(100, 750, "Hello World")
c.save()

temp_dir = tempfile.mkdtemp()
opendataloader_pdf.convert(
    input_path=["dummy.pdf"],
    output_dir=temp_dir,
    format="json",
)

files = os.listdir(temp_dir)
if files:
    with open(os.path.join(temp_dir, files[0])) as f:
        data = json.load(f)
        print(type(data))
        if isinstance(data, list) and data:
            print(type(data[0]))
            print(data[0])
        elif isinstance(data, dict):
            print("KEYS:", list(data.keys()))
