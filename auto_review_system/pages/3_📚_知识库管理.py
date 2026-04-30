"""
📚 知识库大本营
===============
上传标准文件、WBS挂载、批量编辑/删除军规、LLM 上下文增强。
"""
import streamlit as st
import os
import time
import json
from ui_config import apply_theme
from utils.paths import TEMP_UPLOADS_DIR, safe_upload_name

apply_theme()

st.title("📚 知识库大本营 (Level & WBS Admin)")

from rag_engine.kb_manager import get_current_kb_stats, ingest_standard_doc

stats = get_current_kb_stats()
colA, colB = st.columns(2)
with colA: st.metric(label="当前已武装的【审核军规】总数量", value=f"{stats['total_rules']} 条")
with colB: st.metric(label="覆盖的红头文件来源", value=" | ".join(stats['categories']) if stats['categories'] else "暂无数据")

st.divider()
st.subheader("🔄 闲时大模型无损上下文增强引擎 (Lossless Context Enrichment)")
st.info("💡 纠正碎片化切片：大模型将在后台静默接管，补全被切碎的代词指代，并 100% 极限保真其所有数字边界门槛，绝不遗落、且防幻觉！")

col_w1, col_w2 = st.columns(2)
with col_w1: st.metric(label="✅ 已完成指代消解补全", value=f"{stats.get('total_washed', 0)} 条")
with col_w2: st.metric(label="⏳ 待上下文增强补全", value=f"{stats.get('total_unwashed', 0)} 条")

auto_wash = st.checkbox("▶️ 启动免值守挂机增强 (随时取消勾选即刻急停)", value=False)

if auto_wash:
    from rag_engine.kb_manager import get_unwashed_rules, enrich_rule_llm, save_washed_rule
    unwashed = get_unwashed_rules()
    if not unwashed:
        st.success("✅ 恭喜！当前知识库所有红线都具备完美的结构化上下文独立语境！")
    else:
        target_r = unwashed[0]
        with st.spinner(f"正在动用大模型保真增强 | 聚焦 ID: {target_r['id']} ... (随时取消勾选停止)"):
            enriched = enrich_rule_llm(target_r['content'])
            if enriched:
                save_washed_rule(target_r['id'], enriched)
                time.sleep(1.5)
                st.rerun()
            else:
                st.error(f"大模型响应枯竭或超时: {target_r['id']}，安全防护介入暂停。")
     
# 加载 WBS 数据
wbs_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rag_engine", "wbs_ontology.json")
wbs_options = {
    "通用": "全局通用无限定",
    "AI_AUTO": "🤖 AI 自动切片映射 (后台推断每个段落的专属WBS)"
}
if os.path.exists(wbs_path):
    with open(wbs_path, 'r', encoding='utf-8') as f:
        wbs_data = json.load(f)
        for div_k, div_v in wbs_data.items():
            for subdiv_k, subdiv_v in div_v.get("sub_divisions", {}).items():
                for item in subdiv_v.get("items", []):
                    wbs_options[item['code']] = f"[{item['code']}] {div_v['name']} - {subdiv_v['name']} - {item['name']}"

st.divider()
st.subheader("上传并熔炼新的底线黑名单 / 标准")
with st.form("kb_ingest_form"):
    kb_file = st.file_uploader("📚 填装标准军规卷宗 (支持 Word / PDF / Excel)", type=['docx', 'pdf', 'xlsx'])
    kb_category = st.text_input("给此份军规命名 (如：防渗漏A级标准)", placeholder="必填项")
    
    col1, col2 = st.columns(2)
    with col1:
        kb_level = st.selectbox("强制统治权重 (Level 冲突熔断制)", [
            "1 - 最高 (防线直通车：专家纠偏/项目实战黑名单) 🚨", 
            "2 - 较高 (不可逾越：企业标准底线/集采合同强条) ⚠️", 
            "3 - 基础 (兜底通用：国家GB/行业JGJ指导标准) 📖"
        ])
    with col2:
        wbs_selected = st.selectbox("精准挂载至 GB50300 WBS 树节点", list(wbs_options.keys()), format_func=lambda x: wbs_options[x])
    
    # OCR 引擎选择 (仅影响 PDF 文件的 OCR 兜底路径)
    try:
        from ocr_engine import list_engines
        engines_info = list_engines()
        engine_choices = {"🤖 auto (自动选择最佳引擎)": "auto"}
        for e in engines_info:
            icon = "✅" if e["available"] else "❌"
            suffix = " ⚠️仅图片" if not e["returns_text"] else ""
            engine_choices[f"{icon} {e['display_name']}{suffix}"] = e["name"]
        ocr_engine_selected = st.selectbox(
            "🔍 PDF OCR 引擎 (当文字层损坏时启用)",
            options=list(engine_choices.keys()),
            help="当 PDF 文字层损坏(如CID字体)时自动启用 OCR 兜底，可手动指定引擎。对 Word/Excel 无影响。"
        )
        ocr_engine_name = engine_choices[ocr_engine_selected]
    except Exception:
        ocr_engine_name = "auto"

    kb_tags_str = st.text_input("可选：补充召回标签 (用逗号分隔)", placeholder="选填，如：防水, 渗漏...")
    submitted = st.form_submit_button("🔨 靶向铸造并注入大脑", type="primary")
    
if submitted:
    if not kb_file or not kb_category.strip():
        st.warning("必须要上传文件并填写对应的分类名称才能入库！")
    else:
        tags = [t.strip() for t in kb_tags_str.split(',')] if kb_tags_str.strip() else [kb_category]
        lvl_val = int(kb_level.split(" ")[0])
        save_path = os.path.join(TEMP_UPLOADS_DIR, f"KB_{safe_upload_name(kb_file.name)}")
        os.makedirs(TEMP_UPLOADS_DIR, exist_ok=True)
        with open(save_path, "wb") as f: f.write(kb_file.getbuffer())
            
        with st.spinner("🧠 知识内化引擎运转中：自动锁定坐标与优先级..."):
            success, msg = ingest_standard_doc(save_path, kb_category, wbs_selected, lvl_val, tags, ocr_engine=ocr_engine_name)
            
        if success:
            st.success(f"{msg} 🎉 重启单文件查验时即刻具备最高级防御！")
            time.sleep(1.5)
            st.rerun()
        else:
            st.error(msg)
            
st.divider()
st.subheader("【知识库大本营】知识大盘透视镜与批量修订台")
from rag_engine.kb_manager import get_all_rules, batch_update_rules, delete_rules_by_category
all_rules = get_all_rules()

if all_rules:
    import pandas as pd
    # Master View: 紧凑表格式来源卷宗管理
    categories = sorted(set([r.get('category', '未知') for r in all_rules]))
    cat_counts = {}
    for r in all_rules:
        cat = r.get('category', '未知')
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    
    st.markdown("#### 📂 来源卷宗管理")
    st.caption(f"共 **{len(categories)}** 个来源文件 · **{len(all_rules)}** 条军规。勾选「删除」列后点击下方按钮可整卷清除。")
    
    cat_df = pd.DataFrame([
        {"删除 🗑️": False, "来源卷宗名称": cat, "军规数量": cat_counts.get(cat, 0)}
        for cat in categories
    ])
    
    edited_cat_df = st.data_editor(
        cat_df,
        use_container_width=True,
        height=min(390, (len(cat_df) + 1) * 35 + 3),
        num_rows="fixed",
        column_config={
            "删除 🗑️": st.column_config.CheckboxColumn("删除", default=False, width="small"),
            "来源卷宗名称": st.column_config.TextColumn("来源卷宗", disabled=True),
            "军规数量": st.column_config.NumberColumn("军规数", disabled=True, width="small"),
        },
        key="cat_master_editor"
    )
    
    col_del_btn, col_filter = st.columns([1, 2])
    with col_del_btn:
        if st.button("🗑️ 删除选中卷宗", type="primary"):
            cats_to_del = [row["来源卷宗名称"] for _, row in edited_cat_df.iterrows() if row["删除 🗑️"]]
            if not cats_to_del:
                st.warning("请先在表格中勾选要删除的来源卷宗。")
            else:
                total_del = 0
                for cat in cats_to_del:
                    ok, msg = delete_rules_by_category(cat)
                    if ok:
                        total_del += cat_counts.get(cat, 0)
                st.success(f"✅ 已清除 {len(cats_to_del)} 个卷宗、共 {total_del} 条军规。")
                time.sleep(1)
                st.rerun()
    with col_filter:
        filter_options = ["全部显示"] + categories
        selected_cat = st.selectbox("筛选卷宗查看详情 ↓", filter_options, label_visibility="collapsed")
    
    st.divider()
    
    display_rules = all_rules
    if selected_cat != "全部显示":
        display_rules = [r for r in all_rules if r.get('category', '未知') == selected_cat]
        
    if not display_rules:
        st.info("该来源卷宗下暂无军规。")
    else:
        df_data = []
        for r in display_rules:
            df_data.append({
                "删除 🗑️": False,
                "军规 ID (禁止修改)": r["id"],
                "权重 (1最重, 3最轻)": int(r.get('level', 3)),
                "WBS 挂载节点": r.get('wbs_code', '通用'),
                "原文核心法则 (双击修改)": r["content"],
            })
        df = pd.DataFrame(df_data)
        
        st.caption(f"💡 **批量编辑工作台 (Detail View)**：当前卷宗检索到 {len(df)} 条红线军规。您可以像操作 Excel 一样**双击**原文单元格修错、**下拉**批量改权重、或**勾选**最左侧列直接物理拔除。")
        
        wbs_opts = list(wbs_options.keys())
        
        dynamic_height = min(800, (len(df) + 1) * 35 + 3)
        
        edited_df = st.data_editor(
            df,
            use_container_width=True,
            height=dynamic_height,
            num_rows="fixed",
            column_config={
                "删除 🗑️": st.column_config.CheckboxColumn("批量删除", default=False),
                "军规 ID (禁止修改)": st.column_config.TextColumn(disabled=True),
                "权重 (1最重, 3最轻)": st.column_config.SelectboxColumn("级别", options=[1, 2, 3], required=True),
                "WBS 挂载节点": st.column_config.SelectboxColumn("WBS", options=wbs_opts, required=True),
                "原文核心法则 (双击修改)": st.column_config.TextColumn("正文", required=True)
            },
            key="rules_batch_editor"
        )
        
        if st.button("💾 万剑归宗：一键执行本页所有增改删操作", type="primary"):
            with st.spinner("正在将重塑后的军事法则批量同步至只读 JSON 与高维空间向量引擎..."):
                deletes = []
                updates = []
                
                for idx, row in edited_df.iterrows():
                    orig_row = df.iloc[idx]
                    rule_id = row["军规 ID (禁止修改)"]
                    
                    if row["删除 🗑️"]:
                        deletes.append(rule_id)
                    else:
                        if (row["权重 (1最重, 3最轻)"] != orig_row["权重 (1最重, 3最轻)"] or
                            row["WBS 挂载节点"] != orig_row["WBS 挂载节点"] or
                            row["原文核心法则 (双击修改)"] != orig_row["原文核心法则 (双击修改)"]):
                            
                            updates.append({
                                "id": rule_id,
                                "level": row["权重 (1最重, 3最轻)"],
                                "wbs_code": row["WBS 挂载节点"],
                                "content": row["原文核心法则 (双击修改)"]
                            })
                            
                if not deletes and not updates:
                    st.info("未检测到表格发生破坏或改写，触发拦截。")
                else:
                    success, m = batch_update_rules(updates, deletes)
                    if success: 
                        st.success(m)
                        time.sleep(2)
                        st.rerun()
                    else: 
                        st.error(m)
else:
    st.info("🎯 当前武器库尚空，待行业专家将实战经验进行填装。")
