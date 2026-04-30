"""
🏗️ 专家审阅与自我演进面板
=========================
上传施工方案、报价单、现场照片，投递至后台 13-Agent 并发审查。
"""
import streamlit as st
import os
from ui_config import apply_theme
from utils.paths import TEMP_UPLOADS_DIR, safe_upload_name

apply_theme()

st.title("🏗️ 方案评审智能交互查验 (Experts-in-the-Loop)")
st.caption('迈向 V4：从被动"阅卷机"升级为辅助判断的"智囊团"。专家在此审阅机器输出，并可点对点纠偏教导大模型。')

os.makedirs(TEMP_UPLOADS_DIR, exist_ok=True)

col1, col2 = st.columns(2)
with col1:
    st.subheader("1. 资料入舱")
    with st.form("upload_form"):
        uploaded_scheme = st.file_uploader("📂 【业务主轴】施工/验收方案 (支持 Word / Excel)", type=['docx', 'xlsx'])
        uploaded_cost = st.file_uploader("💰 【执行防线】配套报价/材料清单 (支持 Word / Excel)", type=['docx', 'xlsx'])
        uploaded_photos = st.file_uploader("📷 【图文互证】现场监控/实景照片辅助 (选填/多图)", type=['jpg', 'jpeg', 'png'], accept_multiple_files=True)
        submitted_audit = st.form_submit_button("🚀 启动复合并发审阅", type="primary")

with col2:
    st.subheader("2. 审查与强化学习设置")
    use_llm = st.checkbox("双专家 Multi-Agent 并发审验", value=True)
    st.info("⚡ 支持历史纠偏样本溯源录入：您在下方每一次对大模型判定的『驳回』，都会写入企业经验大脑。成为以后同类项目大模型防幻觉的肌肉记忆。")

st.divider()

if submitted_audit:
    if not uploaded_scheme and not uploaded_cost:
        st.error("请至少上传一份文档！")
    else:
        with st.spinner("📦 业务投递箱封存中... 文件物理驻留并派发至异步工业列车..."):
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
            
            st.success(f"🎉 投递大本营成功！工程：【{proj_name}】 | 批次跟踪号：{tid}")
            st.info("💡 **物理隔离投递制**：您的数百万字工程文档已经脱离网页生命周期，移交给了完全独立的后台挂机工厂！您现在可以放心**关掉当前全部网页**甚至拔电脑电源了。待服务器长线运行结束并装订为 Word 后，请去【📥 审核结果收发室】认领！")
