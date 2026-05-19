"""
v3 审核收发室：逐条人工复审、纠偏审批、导出 Word。
"""
import json
import os

import streamlit as st

from ui_config import apply_theme
from utils.exporter import markdown_to_docx
from utils.paths import RESULTS_DIR, safe_artifact_stem
from rag_engine.queue_manager import (
    delete_task,
    get_all_tasks,
    resolve_task_artifact_path,
    set_task_status_only,
    update_task_status,
)
from rag_engine.review_workflow import (
    build_review_markdown,
    current_reviewer,
    list_corrections,
    load_review_state,
    review_correction,
    review_stats,
    save_correction,
    save_issue_review,
)

st.set_page_config(page_title="审核收发室", layout="wide")
apply_theme()

st.markdown(
    """
    <style>
    section.main > div.block-container {
        max-width: 1480px;
        padding-top: 1.35rem;
        padding-left: 2rem;
        padding-right: 2rem;
    }
    div[data-testid="stForm"], div[data-testid="stExpander"] {
        border: 1px solid #d8dee8 !important;
        border-top: 1px solid #d8dee8 !important;
        border-radius: 8px !important;
        box-shadow: none !important;
        padding: 1rem !important;
        transform: none !important;
        background: #ffffff !important;
    }
    div[data-testid="stForm"]:hover, div[data-testid="stExpander"]:hover {
        border-top: 1px solid #d8dee8 !important;
        box-shadow: none !important;
        transform: none !important;
    }
    .review-toolbar {
        border: 1px solid #d8dee8;
        border-radius: 8px;
        padding: 14px 16px;
        background: #ffffff;
        margin: 8px 0 18px 0;
    }
    .soft-panel {
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 16px 18px;
        background: #ffffff;
        margin-bottom: 14px;
        line-height: 1.72;
    }
    .compact-muted {font-size: .86rem; color: #64748b;}
    .issue-counts {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin: 6px 0 12px 0;
    }
    .issue-pill {
        display: inline-flex;
        align-items: center;
        border: 1px solid #d8dee8;
        border-radius: 999px;
        padding: 4px 10px;
        background: #ffffff;
        color: #334155;
        font-size: .84rem;
    }
    .detail-title {
        margin-top: .1rem;
        margin-bottom: .15rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


STATUS_LABELS = {
    "pending": "待复审",
    "accepted": "保留",
    "edited": "已修改",
    "rejected": "误判剔除",
    "needs_more_info": "需补资料",
}

TASK_STATUS_OPTIONS = ["REVIEW_PENDING", "RUNNING", "PENDING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"]


def _load_reports(task):
    json_path = resolve_task_artifact_path(
        task.get("result_docx_path", ""),
        task_id=task["task_id"],
        preferred_ext=".json",
    )
    if not json_path or not os.path.exists(json_path):
        return None, "", "未找到 JSON 初审卷宗。"
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f), json_path, ""
    except Exception as exc:
        return None, json_path, f"JSON 初审卷宗损坏：{exc}"


def _task_label(task):
    return f"[{task['status']}] {task['project_name']} · {task['created_at']}"


def _status_summary(stats):
    pills = "".join(
        f"<span class='issue-pill'>{STATUS_LABELS.get(k, k)} {v}</span>"
        for k, v in stats.items()
        if v
    )
    return f"<div class='issue-counts'>{pills}</div>"


def _issue_search_text(issue):
    return " ".join(str(issue.get(k, "")) for k in ("work_item", "dimension", "finding", "reason", "recommendation"))


def _filter_issues(issues, dimensions, statuses, keyword):
    keyword = (keyword or "").strip()
    result = []
    for issue in issues:
        if dimensions and issue.get("dimension") not in dimensions:
            continue
        if statuses and issue.get("status") not in statuses:
            continue
        if keyword and keyword not in _issue_search_text(issue):
            continue
        result.append(issue)
    return result


def _render_task_controls(task):
    status = task["status"]
    st.caption(f"任务号 `{task['task_id']}` · 更新 `{task['updated_at']}`")
    if status in ["PENDING", "RUNNING", "PAUSED"]:
        a, b = st.columns(2)
        if status in ["PENDING", "RUNNING"] and a.button("挂起", key=f"pause_{task['task_id']}", use_container_width=True):
            set_task_status_only(task["task_id"], "PAUSED")
            st.rerun()
        if status == "PAUSED" and a.button("恢复", key=f"resume_{task['task_id']}", use_container_width=True):
            set_task_status_only(task["task_id"], "PENDING")
            st.rerun()
        if b.button("取消", key=f"cancel_{task['task_id']}", use_container_width=True):
            set_task_status_only(task["task_id"], "CANCELLED")
            st.rerun()
    if status in ["REVIEW_PENDING", "COMPLETED", "FAILED", "CANCELLED"]:
        if st.button("删除工单记录", key=f"delete_{task['task_id']}", use_container_width=True):
            delete_task(task["task_id"])
            st.session_state.pop(f"selected_issue_{task['task_id']}", None)
            st.rerun()


def _render_completed_download(task):
    download_path = resolve_task_artifact_path(
        task.get("result_docx_path", ""),
        task_id=task["task_id"],
        preferred_ext=".docx",
    )
    if download_path and os.path.exists(download_path):
        with open(download_path, "rb") as f:
            st.download_button(
                "下载 Word 审核报告",
                data=f,
                file_name=os.path.basename(download_path),
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
                key=f"download_{task['task_id']}",
            )
    else:
        st.error("未找到已导出的 Word 文件。")


def _render_issue_list(task, issues):
    st.markdown("### 问题列表")
    dimensions = sorted({i.get("dimension", "") for i in issues if i.get("dimension")})
    statuses = sorted({i.get("status", "pending") for i in issues})
    selected_dims = st.multiselect("维度", dimensions, default=[])
    selected_statuses = st.multiselect("复审状态", statuses, default=[])
    keyword = st.text_input("搜索问题/分项", placeholder="如 EPDM、收口、模板")
    filtered = _filter_issues(issues, selected_dims, selected_statuses, keyword)
    st.caption(f"显示 {len(filtered)} / {len(issues)} 条")

    state_key = f"selected_issue_{task['task_id']}"
    if filtered and st.session_state.get(state_key) not in {i["issue_id"] for i in filtered}:
        st.session_state[state_key] = filtered[0]["issue_id"]

    for issue in filtered:
        label = f"{STATUS_LABELS.get(issue['status'], issue['status'])} | {issue.get('work_item')}"
        if st.button(label[:72], key=f"pick_{task['task_id']}_{issue['issue_id']}", use_container_width=True):
            st.session_state[state_key] = issue["issue_id"]
            st.rerun()
        finding = issue.get("finding", "")
        st.caption(f"{issue.get('dimension')} · {finding[:88]}{'...' if len(finding) > 88 else ''}")
    return filtered


def _render_issue_detail(task, issue):
    st.markdown(f"<h2 class='detail-title'>{issue.get('work_item')}</h2>", unsafe_allow_html=True)
    st.caption(f"{issue.get('dimension')} · 置信度 {issue.get('confidence') or '未标注'} · 当前状态 {STATUS_LABELS.get(issue.get('status'), issue.get('status'))}")

    st.markdown('<div class="soft-panel">', unsafe_allow_html=True)
    st.markdown(f"**问题**：{issue.get('finding')}")
    evidence = issue.get("scheme_evidence") or []
    if evidence:
        st.markdown("**方案证据**")
        for line in evidence:
            st.markdown(f"- {line}")
    st.markdown(f"**背景/原因**：{issue.get('reason') or '未返回'}")
    st.markdown(f"**依据类型**：{issue.get('evidence_type') or '未返回'}")
    st.markdown(f"**依据出处**：{issue.get('evidence_ref') or '未返回'}")
    st.markdown(f"**修改建议**：{issue.get('recommendation') or '未返回'}")
    st.markdown('</div>', unsafe_allow_html=True)

    with st.expander("查看 AI 原始报告文本", expanded=False):
        st.text_area("AI 原始文本", value=issue.get("original_result", ""), height=220, disabled=True, label_visibility="collapsed")

    st.markdown("#### 逐条复审")
    status_options = ["accepted", "edited", "needs_more_info", "rejected", "pending"]
    current_status = issue.get("status", "pending")
    with st.form(f"review_form_{task['task_id']}_{issue['issue_id']}"):
        status = st.radio(
            "复审结论",
            status_options,
            index=status_options.index(current_status) if current_status in status_options else 4,
            format_func=lambda x: STATUS_LABELS.get(x, x),
            horizontal=True,
        )
        reviewed_result = st.text_area(
            "人工确认后的报告内容",
            value=issue.get("reviewed_result") or issue.get("original_result", ""),
            height=260,
        )
        if st.form_submit_button("保存本条复审", type="primary", use_container_width=True):
            save_issue_review(task["task_id"], issue["issue_id"], status, reviewed_result)
            st.success("已保存本条复审状态。")
            st.rerun()

    st.markdown("#### 误判记录与经验回流")
    corrections = list_corrections(task["task_id"], issue["issue_id"])
    for item in corrections[:3]:
        st.caption(
            f"{item['status']} · {item.get('correction_type') or '纠偏'} · "
            f"提交 {item.get('submitted_by') or '-'} {item.get('submitted_at') or item.get('created_at') or ''}"
        )
        if item.get("review_comment"):
            st.caption(f"审批意见：{item['review_comment']}")

    draft = next((item for item in corrections if item.get("status") == "draft"), None)
    with st.form(f"correction_form_{task['task_id']}_{issue['issue_id']}"):
        correction_type = st.selectbox(
            "纠偏类型",
            ["误判", "表述优化", "经验补充", "需补资料", "适用边界"],
            index=0,
        )
        human_comment = st.text_area(
            "人工纠偏说明",
            value=(draft or {}).get("human_comment", ""),
            height=110,
            placeholder="说明为什么 AI 这条判断不合适，或应该如何判断。",
        )
        lesson_summary = st.text_area(
            "可沉淀经验",
            value=(draft or {}).get("lesson_summary", ""),
            height=90,
            placeholder="写成以后可复用的一句话经验，例如：户外石凳刷地坪漆时必须核查界面剂、遮蔽保护和倒角收口。",
        )
        c1, c2 = st.columns(2)
        save_draft = c1.form_submit_button("保存纠偏草稿", use_container_width=True)
        submit = c2.form_submit_button("提交审批", type="primary", use_container_width=True)
        if save_draft or submit:
            if not human_comment.strip():
                st.warning("请先填写人工纠偏说明。")
            else:
                save_correction(
                    correction_id=(draft or {}).get("correction_id"),
                    task_id=task["task_id"],
                    issue_id=issue["issue_id"],
                    project_name=task["project_name"],
                    work_item=issue.get("work_item", ""),
                    dimension=issue.get("dimension", ""),
                    ai_finding=issue.get("finding", ""),
                    scheme_evidence=issue.get("scheme_evidence", []),
                    human_comment=human_comment.strip(),
                    correction_type=correction_type,
                    lesson_summary=lesson_summary.strip() or human_comment.strip(),
                    status="submitted" if submit else "draft",
                )
                st.success("已提交审批。" if submit else "草稿已保存。")
                st.rerun()


def _render_review_workspace(task):
    reports, json_path, error = _load_reports(task)
    if error:
        st.error(error)
        if json_path:
            st.caption(f"路径：`{json_path}`")
        return
    issues, runtime = load_review_state(task["task_id"], task["project_name"], reports)
    if not issues:
        st.warning("本任务没有可复审的审核意见。")
        return

    stats = review_stats(issues)
    st.markdown(_status_summary(stats), unsafe_allow_html=True)
    if stats.get("pending", 0):
        st.warning("还有待复审意见未处理；可先批量补全，再导出最终批文。")
    if runtime:
        with st.expander("审核运行信息", expanded=False):
            for item in runtime:
                st.markdown(item.get("result", ""))
    st.divider()

    left, right = st.columns([0.31, 0.69], gap="large")
    with left:
        _render_issue_list(task, issues)
        st.divider()
        exportable = [i for i in issues if i.get("status") in {"accepted", "edited", "needs_more_info"}]
        pending = [i for i in issues if i.get("status") == "pending"]
        st.caption(f"可导出 {len(exportable)} 条；待复审 {len(pending)} 条；误判剔除不会进入 Word。")
        if pending:
            if st.button("一键将待复审标为保留", use_container_width=True):
                for item in pending:
                    save_issue_review(
                        task["task_id"],
                        item["issue_id"],
                        "accepted",
                        item.get("reviewed_result") or item.get("original_result", ""),
                    )
                st.success("已补全全部待复审意见。")
                st.rerun()
        if st.button("确认并导出 Word", type="primary", use_container_width=True):
            if pending:
                st.error("还有待复审意见，先补全再导出。")
                st.stop()
            md_content = build_review_markdown(task["project_name"], issues)
            os.makedirs(RESULTS_DIR, exist_ok=True)
            safe_name = safe_artifact_stem(task["project_name"])
            doc_filename = f"{task['task_id']}_{safe_name}_最终审查批文.docx"
            out_path = os.path.join(RESULTS_DIR, doc_filename)
            buff = markdown_to_docx(md_content, f"{task['project_name']} 审查批文")
            with open(out_path, "wb") as f:
                f.write(buff.getbuffer())
            update_task_status(task["task_id"], "COMPLETED", result_docx_path=out_path)
            st.success("已导出 Word 并归档任务。")
            st.rerun()

    with right:
        state_key = f"selected_issue_{task['task_id']}"
        selected_id = st.session_state.get(state_key) or issues[0]["issue_id"]
        selected = next((item for item in issues if item["issue_id"] == selected_id), issues[0])
        _render_issue_detail(task, selected)


def _render_task_inbox():
    tasks = get_all_tasks()
    if not tasks:
        st.info("尚未投递审核任务。")
        return
    st.markdown("<div class='review-toolbar'>", unsafe_allow_html=True)
    col_status, col_task, col_meta = st.columns([0.25, 0.52, 0.23], gap="medium")
    with col_status:
        status_filter = st.multiselect("任务状态", TASK_STATUS_OPTIONS, default=["REVIEW_PENDING", "COMPLETED", "RUNNING", "PENDING"])
    filtered_tasks = [t for t in tasks if not status_filter or t["status"] in status_filter]
    if not filtered_tasks:
        st.warning("当前筛选下没有任务。")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    task_ids = [t["task_id"] for t in filtered_tasks]
    with col_task:
        selected_task_id = st.selectbox(
            "选择工单",
            task_ids,
            index=0,
            format_func=lambda tid: _task_label(next(t for t in filtered_tasks if t["task_id"] == tid)),
        )
    task = next(t for t in filtered_tasks if t["task_id"] == selected_task_id)
    with col_meta:
        st.markdown("**当前任务**")
        st.caption(f"`{task['task_id']}`")
        st.caption(f"更新 {task['updated_at']}")
    _render_task_controls(task)
    st.markdown("</div>", unsafe_allow_html=True)

    if task["status"] == "REVIEW_PENDING":
        st.markdown(f"## {task['project_name']}")
        _render_review_workspace(task)
    elif task["status"] == "COMPLETED":
        st.success("该任务已完成导出。")
        _render_completed_download(task)
    elif task["status"] == "FAILED":
        st.error("任务失败")
        st.code(task.get("error_log") or "")
    elif task["status"] in ["RUNNING", "PENDING", "PAUSED"]:
        st.info(f"当前状态：{task['status']}。任务完成后会进入人工复审。")
    else:
        st.warning(f"当前状态：{task['status']}")


def _render_approval_queue():
    reviewer = current_reviewer()
    submitted = list_corrections(status="submitted")
    approved = list_corrections(status="approved")[:8]
    left, right = st.columns([0.35, 0.65], gap="large")
    with left:
        st.markdown("### 待审批纠偏")
        st.caption(f"当前审批人：`{reviewer}`")
        if not submitted:
            st.info("暂无待审批记录。")
            if approved:
                st.markdown("#### 最近已通过")
                for item in approved:
                    st.caption(f"{item['work_item']} · {item.get('reviewed_at') or item.get('updated_at')}")
            return
        correction_ids = [item["correction_id"] for item in submitted]
        selected_id = st.selectbox(
            "选择纠偏记录",
            correction_ids,
            format_func=lambda cid: next(
                f"{item['work_item']}｜{item['correction_type']}｜{item['submitted_by']}"
                for item in submitted
                if item["correction_id"] == cid
            ),
        )
        selected = next(item for item in submitted if item["correction_id"] == selected_id)
    with right:
        st.markdown(f"### {selected.get('work_item')}")
        st.caption(f"{selected.get('project_name')} · {selected.get('dimension')} · 提交人 {selected.get('submitted_by')}")
        st.markdown('<div class="soft-panel">', unsafe_allow_html=True)
        st.markdown(f"**AI 原判断**：{selected.get('ai_finding')}")
        if selected.get("scheme_evidence"):
            st.markdown("**方案证据**")
            for line in selected["scheme_evidence"]:
                st.markdown(f"- {line}")
        st.markdown(f"**人工纠偏**：{selected.get('human_comment')}")
        st.markdown(f"**经验摘要**：{selected.get('lesson_summary')}")
        st.markdown('</div>', unsafe_allow_html=True)
        with st.form(f"approval_{selected['correction_id']}"):
            review_comment = st.text_area("审批意见", height=100, placeholder="说明通过或驳回理由。")
            c1, c2 = st.columns(2)
            approve = c1.form_submit_button("审批通过，进入经验候选池", type="primary", use_container_width=True)
            reject = c2.form_submit_button("驳回", use_container_width=True)
            if approve or reject:
                rebuild_result = review_correction(
                    selected["correction_id"],
                    "approved" if approve else "rejected",
                    reviewer=reviewer,
                    review_comment=review_comment.strip(),
                )
                st.success("已审批通过。" if approve else "已驳回。")
                if approve and rebuild_result:
                    if rebuild_result.get("ok"):
                        st.caption(f"经验手册已自动重建：{rebuild_result.get('mtime', '')}")
                    else:
                        st.warning(f"经验手册重建失败：{rebuild_result.get('error', '未知错误')}")
                st.rerun()


st.title("审核收发室")
st.caption("逐条复审 AI 审核意见，确认后导出 Word；误判和经验补充需审批后才进入经验候选池。")

tab_review, tab_approval = st.tabs(["人工复审工作台", "经验审批"])
with tab_review:
    _render_task_inbox()
with tab_approval:
    _render_approval_queue()
