"""
🏗️ 专家审阅与自我演进面板
=========================
上传施工方案、报价单、现场照片，投递至后台 v3 AI 主审。
"""
import streamlit as st
import os
from ui_config import apply_theme
from utils.paths import TEMP_UPLOADS_DIR, safe_upload_name

apply_theme()

st.title("🏗️ v3 零星工程方案审核")
st.caption("完整小方案由 AI 主审一次性阅读，结合经验手册和本地工具复核，输出可人工确认的分项修改意见。")

os.makedirs(TEMP_UPLOADS_DIR, exist_ok=True)

col1, col2 = st.columns(2)
with col1:
    st.subheader("1. 上传资料")
    with st.form("upload_form"):
        uploaded_scheme = st.file_uploader("施工/验收方案（Word / Excel / PDF）", type=['docx', 'xlsx', 'pdf'])
        uploaded_cost = st.file_uploader("配套报价/白单/材料清单（Word / Excel / PDF，可选）", type=['docx', 'xlsx', 'pdf'])
        uploaded_photos = st.file_uploader("现场照片（可选/多图）", type=['jpg', 'jpeg', 'png'], accept_multiple_files=True)
        submitted_audit = st.form_submit_button("🚀 启动 v3 AI 主审", type="primary")

with col2:
    st.subheader("2. 审核方式")
    st.info("v3 默认三阶段：AI 初审、本地工具复核、AI 终审与质量复核。历史经验以手册注入，不再使用关键词规则或多 Agent 路由。")

st.divider()

if submitted_audit:
    if not uploaded_scheme and not uploaded_cost:
        st.error("请至少上传一份文档！")
    else:
        with st.spinner("📦 正在保存资料并投递后台 v3 主审..."):
            proj_name = "未命名工程"
            if uploaded_scheme: proj_name = uploaded_scheme.name.rsplit('.', 1)[0]
            elif uploaded_cost: proj_name = uploaded_cost.name.rsplit('.', 1)[0]
            
            file_paths = []
            target_files = []
            if uploaded_scheme: target_files.append((uploaded_scheme, "scheme"))
            if uploaded_cost: target_files.append((uploaded_cost, "cost"))
            if uploaded_photos: 
                for p in uploaded_photos:
                    target_files.append((p, "photo"))
            
            for f, doc_type in target_files:
                f_path = os.path.join(TEMP_UPLOADS_DIR, safe_upload_name(f.name))
                with open(f_path, "wb") as disk_file:
                    disk_file.write(f.getbuffer())
                file_paths.append({"path": f_path, "type": doc_type})
            
            from rag_engine.queue_manager import add_task
            tid = add_task(proj_name, file_paths)
            
            st.success(f"已投递：{proj_name} | 任务号：{tid}")
            st.info("后台 worker 会继续处理。完成后请到【审核结果收发室】进行人工确认和导出。")
