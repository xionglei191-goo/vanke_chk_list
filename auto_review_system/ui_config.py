"""
Streamlit 全局 UI 样式与共享状态
================================
所有页面通过 `from ui_config import apply_theme` 引用统一样式。
"""
import streamlit as st

VANKE_CSS = """
<style>
    /* 全局背景与字体 */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"]  {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    
    /* 顶部导航条 (Header) 万科红品牌化 */
    [data-testid="stHeader"] {
        background: linear-gradient(90deg, #E50012 0%, #B3000E 100%) !important;
    }
    [data-testid="stHeader"] * {
        color: #FFFFFF !important;
        fill: #FFFFFF !important;
    }
    /* 隐藏默认细彩条 */
    [data-testid="stDecoration"] {
        display: none !important;
    }

    /* 修改底层画布背景为层次感浅底色 */
    .stApp {
        background-color: #F1F5F9;
    }
    
    /* 侧边栏高级暗夜主题 (Dark Navy) 打破全白单调 */
    [data-testid="stSidebar"] {
        background-image: linear-gradient(180deg, #0F172A 0%, #1E293B 100%) !important;
        color: #F8FAFC !important;
        box-shadow: 4px 0 15px rgba(0,0,0,0.15) !important;
        border-right: none !important;
    }
    [data-testid="stSidebar"] * {
        color: #F8FAFC !important;
    }
    
    /* 卡片式容器（表单、折叠面板）增加顶部品牌修饰线 (万科红) */
    div[data-testid="stForm"], div[data-testid="stExpander"] {
        background-color: #FFFFFF;
        border-radius: 12px;
        border: none;
        border-top: 4px solid #E50012;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
        padding: 1.5rem;
        transition: transform 0.2s ease-in-out, box-shadow 0.2s ease-in-out;
    }
    div[data-testid="stForm"]:hover, div[data-testid="stExpander"]:hover {
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
        border-top: 4px solid #B3000E;
    }

    /* 主按钮样式（带呼吸感悬浮动效与万科红渐变） */
    div.stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #FF1E33 0%, #E50012 100%);
        color: white;
        border-radius: 8px;
        border: none;
        padding: 0.5rem 1rem;
        font-weight: 600;
        box-shadow: 0 4px 6px rgba(229, 0, 18, 0.25);
        transition: all 0.3s ease;
    }
    div.stButton > button[kind="primary"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(229, 0, 18, 0.35);
        color: white;
        border: none;
    }

    /* 文本输入框等交互组件的高级圆角与悬停颜色 */
    div[data-baseweb="input"] > div {
        border-radius: 8px !important;
        transition: all 0.2s ease;
    }
    
    /* 文件上传区的高级虚线框与柔和配色 (浅红系) */
    [data-testid="stFileUploadDropzone"] {
        border: 2px dashed #FCA5A5 !important;
        border-radius: 12px !important;
        background-color: #FEF2F2 !important;
        transition: all 0.3s ease;
    }
    [data-testid="stFileUploadDropzone"]:hover {
        background-color: #FEE2E2 !important;
        border-color: #E50012 !important;
    }

    /* 标题色彩加深，增加对比度质感 */
    h1, h2, h3 {
        color: #0F172A;
        font-weight: 700;
    }
    /* 正文文字变得更柔和耐读 */
    .stMarkdown p {
        color: #334155;
    }
    
    /* Toast 通知栏的阴影化处理 */
    div[data-baseweb="toast"] {
        border-radius: 10px;
        box-shadow: 0 10px 25px rgba(0,0,0,0.15) !important;
    }
</style>
"""


def apply_theme():
    """注入万科品牌化全局 CSS 和初始化共享 session state。"""
    st.markdown(VANKE_CSS, unsafe_allow_html=True)

    if 'grouped_reports' not in st.session_state:
        st.session_state['grouped_reports'] = None
    if 'cost_issues' not in st.session_state:
        st.session_state['cost_issues'] = None
    if 'project_name' not in st.session_state:
        st.session_state['project_name'] = ""
    if 'global_rule_log' not in st.session_state:
        st.session_state['global_rule_log'] = []
