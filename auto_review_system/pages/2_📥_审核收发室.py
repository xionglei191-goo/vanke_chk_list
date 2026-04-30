"""
📥 离线审核结果收发室
=====================
查看后台审查任务状态、专家纠偏批注、导出 Word 报告。
"""
import streamlit as st
import os
import json
from ui_config import apply_theme
from utils.paths import RESULTS_DIR

apply_theme()

st.title("📥 离线审核结果收发室 (Inbox)")
st.info("这里展示您投递给后台大规模挂机算力工厂的所有重工业工单。")

from rag_engine.queue_manager import (
    delete_task,
    get_all_tasks,
    resolve_task_artifact_path,
    set_task_status_only,
)
tasks = get_all_tasks()


def _render_delete_button(task, caption=None):
    if caption:
        st.caption(caption)
    if st.button("🗑️ 删除该工单记录", key=f"del_{task['task_id']}"):
        delete_task(task['task_id'])
        if st.session_state.get('active_review_task') == task['task_id']:
            st.session_state['active_review_task'] = None
        st.rerun()

if not tasks:
    st.write("🎯 尚未投递过任何离线查验工单。")
else:
    for t in tasks:
        status = t['status']
        icon = "🕒"
        if status == "COMPLETED": icon = "✅"
        elif status == "RUNNING": icon = "🔄"
        elif status == "FAILED": icon = "❌"
        elif status == "PAUSED": icon = "⏸️"
        elif status == "CANCELLED": icon = "⏹️"
        elif status == "REVIEW_PENDING": icon = "👀"
        
        with st.expander(f"{icon} [{status}] {t['project_name']} | 投递时间: {t['created_at']}", expanded=(status in ["RUNNING", "COMPLETED", "REVIEW_PENDING"])):
            st.write(f"**流转追踪号:** `{t['task_id']}`")
            st.write(f"**更新脉搏:** `{t['updated_at']}`")
            
            # ------ 队列干预控件 ------
            if status in ["PENDING", "RUNNING", "PAUSED"]:
                colbtn1, colbtn2 = st.columns([1, 1])
                if status in ["PENDING", "RUNNING"]:
                    if colbtn1.button("⏸️ 临时挂起 (Pause)", key=f"pause_{t['task_id']}"):
                        set_task_status_only(t['task_id'], "PAUSED")
                        st.rerun()
                if status == "PAUSED":
                    if colbtn1.button("▶️ 恢复生产 (Resume)", key=f"resume_{t['task_id']}"):
                        set_task_status_only(t['task_id'], "PENDING")
                        st.rerun()
                if colbtn2.button("⏹️ 强行叫停 (Cancel)", key=f"cancel_{t['task_id']}"):
                    set_task_status_only(t['task_id'], "CANCELLED")
                    st.rerun()
            st.divider()
            
            if status == 'COMPLETED':
                st.success("🎉 该大盘所有的红黑线审计作业已连夜打穿，红头批文已固化！")
                download_path = resolve_task_artifact_path(
                    t.get('result_docx_path', ''),
                    task_id=t['task_id'],
                    preferred_ext='.docx',
                )
                if download_path and os.path.exists(download_path):
                    with open(download_path, "rb") as f:
                        st.download_button(
                            label="📥 领取企业级专项审查 Word 报告", 
                            data=f, 
                            file_name=os.path.basename(download_path), 
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", 
                            key=t['task_id']
                        )
                else:
                    st.error("物理文件丢失，可能被清理脚本重置。")
            elif status == 'REVIEW_PENDING':
                st.info("✋ 机器初审已杀青。当前案卷被冻结，等待权威工程师审校...")
                if st.button("🔍 开启专家人工纠偏复审 (Audit)", key=f"review_{t['task_id']}", use_container_width=True):
                    st.session_state['active_review_task'] = t['task_id']
                    st.rerun()
                    
                if st.session_state.get('active_review_task') == t['task_id']:
                    st.divider()
                    st.markdown("### 📝 专家批注工作台")
                    json_path = resolve_task_artifact_path(
                        t.get('result_docx_path', ''),
                        task_id=t['task_id'],
                        preferred_ext='.json',
                    )
                    if not json_path or not os.path.exists(json_path):
                        st.error("未找到 JSON 初审卷宗。该任务可能来自其他机器同步，或卷宗已被清理。")
                        if t.get('result_docx_path'):
                            st.caption(f"任务记录中的原始路径：`{t['result_docx_path']}`")
                    else:
                        reports = None
                        load_error = None
                        with open(json_path, "r", encoding="utf-8") as f:
                            try:
                                reports = json.load(f)
                            except Exception as exc:
                                load_error = str(exc)

                        if load_error:
                            st.error(f"JSON 初审卷宗损坏：{load_error}")
                        elif not isinstance(reports, dict) or not reports:
                            st.warning("JSON 初审卷宗为空，暂时无法进入人工复审。")
                        else:
                            with st.form(key=f"review_form_{t['task_id']}"):
                                st.write("请在下方输入框中直接修正 AI 的初研结论。如发现严重幻觉，可点击「标记误判」录入纠偏教材。")
                                modified_reports = {}
                                for h, reps in reports.items():
                                    st.markdown(f"#### 【章节剖析】{h}")
                                    modified_reps = []
                                    for idx, r in enumerate(reps):
                                        new_res = st.text_area(f"{r['agent']} 结论：", value=r['result'], height=150, key=f"ta_{t['task_id']}_{h}_{idx}")
                                        modified_reps.append({
                                            'agent': r['agent'],
                                            'heading': r.get('heading', h),
                                            'result': new_res
                                        })
                                    modified_reports[h] = modified_reps
                                
                                submitted = st.form_submit_button("✅ 终级确认并盖章成文 (Export Word)", type="primary", use_container_width=True)
                                if submitted:
                                    from utils.exporter import markdown_to_docx
                                    from rag_engine.queue_manager import update_task_status
                                    
                                    lines = [f"# {t['project_name']} 自动化审核结论批文\n"]
                                    for h, reps in modified_reports.items():
                                        lines.append(f"## 【章节剖析】{h}")
                                        for r in reps:
                                            lines.append(f"### {r['agent']} 核验结论")
                                            lines.append(f"{r['result']}\n")
                                    md_content = "\n".join(lines)
                                    
                                    doc_filename = f"{t['task_id']}_{t['project_name']}_最终审查批文.docx"
                                    result_dir = RESULTS_DIR
                                    os.makedirs(result_dir, exist_ok=True)
                                    out_path = os.path.join(result_dir, doc_filename)
                                    
                                    buff = markdown_to_docx(md_content, f"{t['project_name']} 审查批文")
                                    with open(out_path, "wb") as f:
                                        f.write(buff.getbuffer())
                                    
                                    update_task_status(t['task_id'], 'COMPLETED', result_docx_path=out_path)
                                    st.session_state['active_review_task'] = None
                                    st.rerun()
                            
                            # ---- 纠偏录入台（在 form 外部）----
                            st.divider()
                            st.markdown("##### ❌ 误判标记台")
                            st.caption("如发现 AI 有严重幻觉或越权判定，请在此录入纠偏教材。录入后将自动注入后续 Prompt，防止重犯。")
                            from rag_engine.correction_manager import record_correction
                            for h, reps in reports.items():
                                for idx, r in enumerate(reps):
                                    corr_key = f"corr_{t['task_id']}_{h}_{idx}"
                                    with st.expander(f"🔖 {r.get('agent', '?')} | {h}", expanded=False):
                                        corr_text = st.text_area(
                                            "✏️ 专家纠偏指导（告诉 AI 正确的逻辑）",
                                            key=f"corr_text_{corr_key}",
                                            height=80,
                                            placeholder="例：该切片属于屋面防水范畴，不应提及电梯要求。"
                                        )
                                        if st.button("📌 录入纠偏教材", key=f"corr_btn_{corr_key}"):
                                            if corr_text.strip():
                                                record_correction(
                                                    agent_name=r.get('agent', ''),
                                                    chunk_heading=h,
                                                    wrong_result=r.get('result', '')[:200],
                                                    correction_text=corr_text.strip()
                                                )
                                                st.success("✅ 已录入纠偏教材。")
                                            else:
                                                st.warning("请先填写纠偏指导内容。")
            elif status == 'FAILED':
                st.error("大模型死锁或系统性崩溃报警")
                st.code(t['error_log'])
            elif status == 'RUNNING':
                st.warning("⚠️ 机械特工正在轰鸣！当前这个项目正独占算力轨道在强行推进出锅中...")
            elif status == 'PAUSED':
                st.info("⏸️ 任务已由人工挂起，等待重新唤醒。")
            elif status == 'CANCELLED':
                st.warning("⏹️ 取消归档指令已生效，任务已终止推进。")
            else:
                st.info("🕒 等待流水线上游释放坑位排号中...")
                
            # 提供清理入口
            if status in ['REVIEW_PENDING', 'COMPLETED', 'FAILED', 'CANCELLED']:
                st.divider()
                delete_caption = None
                if status == 'REVIEW_PENDING':
                    delete_caption = "如果初审卷宗已丢失，或这是从其他机器同步来的失效任务，可以直接删除。"
                _render_delete_button(t, caption=delete_caption)
