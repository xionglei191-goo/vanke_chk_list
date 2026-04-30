import json, tempfile, os, opendataloader_pdf
temp_dir = tempfile.mkdtemp()
opendataloader_pdf.convert(input_path=["dummy.pdf"], output_dir=temp_dir, format="json")
with open(os.path.join(temp_dir, os.listdir(temp_dir)[0])) as f:
    data = json.load(f)
    print("Page 1 keys:", list(data["kids"][0].keys()))
    print("Page 1 first kid:", type(data["kids"][0]["kids"][0]))
    print(data["kids"][0]["kids"][0])
