"""
📚 经验手册维护
==============
查看、重建和检查当前运行期经验手册。
"""
from __future__ import annotations

import os
import streamlit as st

from ui_config import apply_theme
from rag_engine.playbook_manager import (
    DEFAULT_OUTPUT,
    DEFAULT_SOURCE,
    playbook_info,
    rebuild_playbook,
    read_playbook,
)
from rag_engine.review_workflow import approved_lessons_markdown

st.set_page_config(page_title="经验手册维护", layout="wide")
apply_theme()


def _panel_style():
    st.markdown(
        """
        <style>
        section.main > div.block-container {
            max-width: 1480px;
            padding-top: 1.2rem;
            padding-left: 1.8rem;
            padding-right: 1.8rem;
        }
        .handbook-box {
            border: 1px solid #d8dee8;
            border-radius: 8px;
            padding: 14px 16px;
            background: #fff;
            margin-bottom: 12px;
        }
        .muted { color: #64748b; font-size: .88rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _refresh_summary():
    approved = approved_lessons_markdown()
    result = rebuild_playbook(approved_feedback=approved)
    st.session_state["playbook_refresh_result"] = result
    return result


_panel_style()

st.title("📚 经验手册维护")
st.caption("查看当前运行期经验手册，按需重建并检查审批回流后的内容。")

info = playbook_info()
latest_result = st.session_state.get("playbook_refresh_result") or info

left, right = st.columns([0.36, 0.64], gap="large")
with left:
    st.markdown('<div class="handbook-box">', unsafe_allow_html=True)
    st.markdown("**当前手册**")
    st.caption(f"路径 `{info['path']}`")
    st.caption(f"状态：{'已生成' if info['exists'] else '未生成'}")
    st.caption(f"大小：{info['size']} bytes")
    st.caption(f"字符：{info['chars']}")
    st.caption(f"更新时间：{info['mtime'] or '-'}")
    st.caption(f"章节数：{info['sections']}")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="handbook-box">', unsafe_allow_html=True)
    st.markdown("**重建来源**")
    st.caption(f"源报告 `{DEFAULT_SOURCE}`")
    st.caption(f"输出文件 `{DEFAULT_OUTPUT}`")
    if st.button("重建经验手册", type="primary", use_container_width=True):
        result = _refresh_summary()
        if result.get("ok"):
            st.success("经验手册已重建。")
            st.caption(f"更新时间：{result.get('mtime', '')}")
        else:
            st.error(result.get("error", "重建失败"))
    if st.button("重新读取当前文件", use_container_width=True):
        st.session_state["playbook_refresh_result"] = playbook_info()
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="handbook-box">', unsafe_allow_html=True)
    st.markdown("**审批回流**")
    st.caption("当前审批通过的经验会被汇总到手册末尾。")
    if st.button("查看审批回流并重建", use_container_width=True):
        result = _refresh_summary()
        if result.get("ok"):
            st.success("已按最新审批记录重建。")
        else:
            st.warning(result.get("error", "未能重建"))
    st.markdown('</div>', unsafe_allow_html=True)

with right:
    st.markdown('<div class="handbook-box">', unsafe_allow_html=True)
    st.markdown("**内容预览**")
    text = read_playbook()
    if text:
        st.text_area("经验手册", value=text, height=760, label_visibility="collapsed")
    else:
        st.info("当前没有可预览的经验手册。先点左侧重建。")
    st.markdown('</div>', unsafe_allow_html=True)

if isinstance(latest_result, dict) and latest_result.get("ok"):
    st.caption(f"最近刷新：{latest_result.get('mtime', '')}")
