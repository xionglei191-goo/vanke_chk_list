import json, glob
json_files = glob.glob("/tmp/tmp*/KB_GB50210-2018*.json")
if json_files:
    with open(json_files[0]) as f:
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
            print("Found", len(tables), "tables")
            print("First table structure:", json.dumps(tables[0], indent=2, ensure_ascii=False)[:500])
        else:
            print("No elements of type 'table' found.")
else:
    print("Could not find the JSON file")
