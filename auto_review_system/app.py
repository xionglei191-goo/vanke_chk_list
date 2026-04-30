"""
万科智能审查系统 V9.1 — Streamlit 多页面入口
=============================================
本文件仅负责 page_config 和首页欢迎信息。
业务逻辑已拆分至 pages/ 目录下的独立页面模块。
"""
import streamlit as st
from ui_config import apply_theme
from rag_engine.kb_manager import get_current_kb_stats

st.set_page_config(
    page_title="万科智能审查系统 V9.1",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="🏗️",
)

apply_theme()

# ==================== 首页 ====================
st.title("🏗️ 万科智能审查系统 V9.1")
st.caption("基于 Multi-Agent RAG + LLM Reranker 的工程方案合规审查平台")

st.divider()

# 系统概览仪表盘
stats = get_current_kb_stats()

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("📚 知识库军规总量", f"{stats['total_rules']} 条")
with col2:
    st.metric("✅ 已完成指代消解", f"{stats.get('total_washed', 0)} 条")
with col3:
    src_count = len(stats.get('categories', []))
    st.metric("📂 覆盖来源文件", f"{src_count} 份")

st.divider()

st.markdown("""
### 📌 快速导航

| 页面 | 功能 |
|------|------|
| **🏗️ 专家审阅** | 上传施工方案/报价单/现场照片，投递至后台 13-Agent 并发审查 |
| **📥 审核收发室** | 查看离线审查结果、专家纠偏批注、导出 Word 报告 |
| **📚 知识库管理** | 上传标准文件、WBS挂载、批量编辑/删除军规、LLM 上下文增强 |

> 👈 请使用左侧导航栏切换页面
""")

st.sidebar.success("👆 选择上方页面开始工作")
