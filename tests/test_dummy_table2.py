import json, glob
json_files = glob.glob('/tmp/tmp*/t.json')
if json_files:
    with open(json_files[-1]) as f:
        d = json.load(f)
    for e in (d["kids"] if "kids" in d else d):
        if e.get("type") == "table":
            print(json.dumps(e["rows"], ensure_ascii=False, indent=2))
            break
