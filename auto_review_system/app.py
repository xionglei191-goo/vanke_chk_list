"""
万科零星工程 v3 AI 主审系统 — Streamlit 多页面入口
=============================================
本文件仅负责 page_config 和首页欢迎信息。
业务逻辑已拆分至 pages/ 目录下的独立页面模块。
"""
import streamlit as st
from ui_config import apply_theme

st.set_page_config(
    page_title="万科零星工程 v3 AI 主审系统",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="🏗️",
)

apply_theme()

# ==================== 首页 ====================
st.title("🏗️ 万科零星工程 v3 AI 主审系统")
st.caption("完整阅读小方案，注入经验手册，本地工具复核，人工确认导出。")

st.divider()

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("审核引擎", "v3_ai_review")
with col2:
    st.metric("默认 AI 阶段", "3 次")
with col3:
    st.metric("运行方式", "人工复审后导出")

st.divider()

st.markdown("""
### 📌 快速导航

| 页面 | 功能 |
|------|------|
| **🏗️ 专家审阅** | 上传施工方案/报价单/现场照片，投递至后台 v3 AI 主审 |
| **📥 审核收发室** | 逐条复审、提交纠偏审批、导出 Word 报告 |

> 👈 请使用左侧导航栏切换页面
""")

st.sidebar.success("👆 选择上方页面开始工作")
