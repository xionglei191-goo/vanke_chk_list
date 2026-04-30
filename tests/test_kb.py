import json
with open('auto_review_system/data/knowledge_base.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
for k in data[-5:]:
    print(k['category'], len(k['content']), k['content'][:50])
