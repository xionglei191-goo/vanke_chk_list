import os
import pandas as pd
from difflib import SequenceMatcher

def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()

folder_path = '方案评审'
excel_path = '检查结果整理.xlsx'

# 1. Get files
try:
    files = os.listdir(folder_path)
    files = [f for f in files if os.path.isfile(os.path.join(folder_path, f))]
except Exception as e:
    files = []

# 2. Read Excel - ALL SHEETS
try:
    dfs = pd.read_excel(excel_path, sheet_name=None)
    excel_items = []
    for sheet_name, sheet_df in dfs.items():
        sheet_df = sheet_df.fillna('')
        excel_items.extend(sheet_df.to_dict('records'))
except Exception as e:
    print(f"Error reading excel: {e}")
    excel_items = []

reviewed_files = [] # Files with comments
unreviewed_files = [] # Files without comments
comments_without_files = [] # Excel items with comments but no file

matched_excel_indices = set()
matched_file_indices = set()

# Clean up column names since sometimes there are spaces
cleaned_excel_items = []
for item in excel_items:
    clean_item = {str(k).strip(): v for k, v in item.items()}
    cleaned_excel_items.append(clean_item)
excel_items = cleaned_excel_items

# Fuzzy match files to excel items
for i, file_name in enumerate(files):
    name_no_ext = os.path.splitext(file_name)[0]
    best_match_idx = -1
    best_score = 0
    
    for j, item in enumerate(excel_items):
        proj_name = str(item.get('方案/白单名称', item.get('项目', item.get('工程名称', item.get('文件名', '')))))
        proj_name = proj_name.strip()
        if not proj_name:
            continue
        
        if proj_name in name_no_ext or name_no_ext in proj_name:
            score = 1.0
        else:
            score = similar(name_no_ext, proj_name)
            
        if score > 0.6 and score > best_score:
            best_score = score
            best_match_idx = j
            
    if best_score > 0.6:  # Threshold
        matched_file_indices.add(i)
        matched_excel_indices.add(best_match_idx)
        
        item = excel_items[best_match_idx]
        opinion_text = str(item.get('意见', item.get('具体意见', ''))).strip()
        has_opinion_flag = str(item.get('是否存在意见', '')).strip() == '是'
        
        if has_opinion_flag or opinion_text:
            reviewed_files.append((file_name, opinion_text if opinion_text else "有意见（未写明具体内容）"))
        else:
            unreviewed_files.append(file_name)
    else:
        unreviewed_files.append(file_name)

# Find excel items with comments but no file
for j, item in enumerate(excel_items):
    if j not in matched_excel_indices:
        proj_name = str(item.get('方案/白单名称', item.get('项目', item.get('工程名称', item.get('文件名', ''))))).strip()
        opinion_text = str(item.get('意见', item.get('具体意见', ''))).strip()
        has_opinion_flag = str(item.get('是否存在意见', '')).strip() == '是'
        
        if (has_opinion_flag or opinion_text) and proj_name:
            comments_without_files.append((proj_name, opinion_text))

# 3. Write output to result.md
output_path = 'result.md'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write("# 方案评审整理结果\n\n")
    
    f.write(f"## 一、已评审的文件 (匹配到文件，且有修改意见) ({len(reviewed_files)})\n\n")
    for file_name, comment in reviewed_files:
        _comment = comment.replace('\n', ' ')
        if len(_comment) > 100:
            _comment = _comment[:100] + "..."
        f.write(f"- **{file_name}**\n  - > 意见简述: {_comment}\n")
    f.write("\n")
    
    f.write(f"## 二、未评审的文件 (包含已匹配但没意见的，或未匹配的) ({len(unreviewed_files)})\n\n")
    for file_name in unreviewed_files:
        f.write(f"- {file_name}\n")
    f.write("\n")
    
    f.write(f"## 三、有评审意见但缺失对应的评审文件 ({len(comments_without_files)})\n\n")
    for proj, comment in comments_without_files:
        _comment = comment.replace('\n', ' ')
        if len(_comment) > 100:
            _comment = _comment[:100] + "..."
        f.write(f"- **{proj}**\n  - > 意见简述: {_comment}\n")
    f.write("\n")

print(f"Results written to {output_path}")
