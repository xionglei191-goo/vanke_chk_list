import pandas as pd

try:
    df = pd.read_excel('检查结果整理.xlsx', sheet_name=None)
    for sheet_name, sheet_df in df.items():
        print(f"Sheet: {sheet_name}")
        print(sheet_df.head())
        print("Columns:", sheet_df.columns.tolist())
        print("-" * 50)
except Exception as e:
    print(f"Error: {e}")
