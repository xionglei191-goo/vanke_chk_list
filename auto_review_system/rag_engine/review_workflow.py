"""Human review workflow for v3 audit issues and correction approvals."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sqlite3
import uuid
from collections import defaultdict

from utils.paths import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "review_feedback.db")

ISSUE_STATUSES = {"pending", "accepted", "edited", "rejected", "needs_more_info"}
CORRECTION_STATUSES = {"draft", "submitted", "approved", "rejected"}
EXPORTABLE_STATUSES = {"accepted", "edited", "needs_more_info"}


def current_reviewer() -> str:
    return os.getenv("REVIEW_USER_NAME", "local_reviewer").strip() or "local_reviewer"


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value, default):
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reviewed_issues (
            task_id TEXT NOT NULL,
            issue_id TEXT NOT NULL,
            project_name TEXT,
            work_item TEXT,
            dimension TEXT,
            ai_finding TEXT,
            original_result TEXT,
            reviewed_result TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            updated_by TEXT,
            updated_at TEXT,
            raw_issue_json TEXT,
            PRIMARY KEY (task_id, issue_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS correction_approvals (
            correction_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            issue_id TEXT NOT NULL,
            project_name TEXT,
            work_item TEXT,
            dimension TEXT,
            ai_finding TEXT,
            scheme_evidence TEXT,
            human_comment TEXT,
            correction_type TEXT,
            lesson_summary TEXT,
            status TEXT NOT NULL,
            submitted_by TEXT,
            submitted_at TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_comment TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def stable_issue_id(task_id: str, work_item: str, dimension: str, index: int) -> str:
    raw = f"{task_id}|{work_item}|{dimension}|{index}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()
    return f"ISSUE_{digest}"


def _first_line_value(markdown: str, label: str) -> str:
    prefix = f"**{label}**："
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.removeprefix(prefix).strip()
    return ""


def split_reports(reports: dict, task_id: str) -> tuple[list[dict], list[dict]]:
    """Flatten raw v3 report groups into issue rows and runtime rows."""
    issues = []
    runtime = []
    idx = 0
    if not isinstance(reports, dict):
        return issues, runtime
    for group, reps in reports.items():
        if not isinstance(reps, list):
            continue
        for rep in reps:
            if not isinstance(rep, dict):
                continue
            if group == "审核运行信息" or rep.get("origin") == "runtime_info":
                runtime.append(dict(rep, group=group))
                continue
            idx += 1
            work_item = str(rep.get("work_item") or rep.get("heading") or group or "整体复核")
            dimension = str(rep.get("dimension") or rep.get("agent") or "描述完整性").replace("审核", "")
            issue_id = rep.get("issue_id") or stable_issue_id(task_id, work_item, dimension, idx)
            result = str(rep.get("result") or "")
            finding = str(rep.get("finding") or _first_line_value(result, "问题") or result[:120])
            evidence = rep.get("scheme_evidence") or []
            if isinstance(evidence, str):
                evidence = [evidence] if evidence.strip() else []
            issue = {
                "issue_id": issue_id,
                "index": idx,
                "group": group,
                "agent": rep.get("agent", ""),
                "work_item": work_item,
                "dimension": dimension,
                "finding": finding,
                "scheme_evidence": evidence,
                "reason": rep.get("reason") or _first_line_value(result, "背景/原因"),
                "evidence_type": rep.get("evidence_type") or _first_line_value(result, "依据类型"),
                "evidence_ref": rep.get("evidence_ref") or _first_line_value(result, "依据出处"),
                "recommendation": rep.get("recommendation") or _first_line_value(result, "修改建议"),
                "confidence": rep.get("confidence") or _first_line_value(result, "置信度"),
                "needs_followup": bool(rep.get("needs_followup")),
                "origin": rep.get("origin", "v3"),
                "original_result": result,
                "raw_issue": rep,
            }
            issues.append(issue)
    return issues, runtime


def ensure_review_rows(task_id: str, project_name: str, issues: list[dict]) -> None:
    now = _now()
    conn = _get_conn()
    cur = conn.cursor()
    for issue in issues:
        cur.execute(
            """
            INSERT OR IGNORE INTO reviewed_issues
            (task_id, issue_id, project_name, work_item, dimension, ai_finding,
             original_result, reviewed_result, status, updated_by, updated_at, raw_issue_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?)
            """,
            (
                task_id,
                issue["issue_id"],
                project_name,
                issue.get("work_item", ""),
                issue.get("dimension", ""),
                issue.get("finding", ""),
                issue.get("original_result", ""),
                issue.get("original_result", ""),
                now,
                _json_dumps(issue.get("raw_issue", {})),
            ),
        )
    conn.commit()
    conn.close()


def get_issue_reviews(task_id: str) -> dict[str, dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM reviewed_issues WHERE task_id = ?", (task_id,)).fetchall()
    conn.close()
    return {row["issue_id"]: dict(row) for row in rows}


def delete_review_state(task_id: str) -> None:
    """Remove review workflow rows for one task. Used by maintenance/tests."""
    conn = _get_conn()
    conn.execute("DELETE FROM reviewed_issues WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM correction_approvals WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


def load_review_state(task_id: str, project_name: str, reports: dict) -> tuple[list[dict], list[dict]]:
    issues, runtime = split_reports(reports, task_id)
    ensure_review_rows(task_id, project_name, issues)
    review_map = get_issue_reviews(task_id)
    correction_map = get_correction_counts(task_id)
    for issue in issues:
        review = review_map.get(issue["issue_id"], {})
        issue["status"] = review.get("status", "pending")
        issue["reviewed_result"] = review.get("reviewed_result") or issue.get("original_result", "")
        issue["updated_by"] = review.get("updated_by", "")
        issue["updated_at"] = review.get("updated_at", "")
        issue["correction_counts"] = correction_map.get(issue["issue_id"], {})
    return issues, runtime


def save_issue_review(task_id: str, issue_id: str, status: str, reviewed_result: str, reviewer: str | None = None) -> None:
    if status not in ISSUE_STATUSES:
        raise ValueError(f"invalid review status: {status}")
    conn = _get_conn()
    conn.execute(
        """
        UPDATE reviewed_issues
        SET status = ?, reviewed_result = ?, updated_by = ?, updated_at = ?
        WHERE task_id = ? AND issue_id = ?
        """,
        (status, reviewed_result, reviewer or current_reviewer(), _now(), task_id, issue_id),
    )
    conn.commit()
    conn.close()


def review_stats(issues: list[dict]) -> dict[str, int]:
    stats = {status: 0 for status in ISSUE_STATUSES}
    for issue in issues:
        stats[issue.get("status", "pending")] = stats.get(issue.get("status", "pending"), 0) + 1
    return stats


def get_correction_counts(task_id: str) -> dict[str, dict]:
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT issue_id, status, COUNT(*) AS cnt
        FROM correction_approvals
        WHERE task_id = ?
        GROUP BY issue_id, status
        """,
        (task_id,),
    ).fetchall()
    conn.close()
    result: dict[str, dict] = {}
    for row in rows:
        result.setdefault(row["issue_id"], {})[row["status"]] = row["cnt"]
    return result


def list_corrections(task_id: str | None = None, issue_id: str | None = None, status: str | None = None) -> list[dict]:
    clauses = []
    params = []
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id)
    if issue_id:
        clauses.append("issue_id = ?")
        params.append(issue_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = _get_conn()
    rows = conn.execute(
        f"SELECT * FROM correction_approvals {where} ORDER BY updated_at DESC, created_at DESC",
        params,
    ).fetchall()
    conn.close()
    items = [dict(row) for row in rows]
    for item in items:
        item["scheme_evidence"] = _json_loads(item.get("scheme_evidence"), [])
    return items


def save_correction(
    *,
    task_id: str,
    issue_id: str,
    project_name: str,
    work_item: str,
    dimension: str,
    ai_finding: str,
    scheme_evidence,
    human_comment: str,
    correction_type: str,
    lesson_summary: str,
    status: str = "draft",
    correction_id: str | None = None,
    submitted_by: str | None = None,
) -> str:
    if status not in CORRECTION_STATUSES:
        raise ValueError(f"invalid correction status: {status}")
    now = _now()
    submitter = submitted_by or current_reviewer()
    conn = _get_conn()
    if correction_id:
        conn.execute(
            """
            UPDATE correction_approvals
            SET human_comment = ?, correction_type = ?, lesson_summary = ?, status = ?,
                submitted_by = ?, submitted_at = CASE WHEN ? = 'submitted' THEN ? ELSE submitted_at END,
                updated_at = ?
            WHERE correction_id = ?
            """,
            (human_comment, correction_type, lesson_summary, status, submitter, status, now, now, correction_id),
        )
    else:
        correction_id = f"CORR_{uuid.uuid4().hex[:10].upper()}"
        submitted_at = now if status == "submitted" else ""
        conn.execute(
            """
            INSERT INTO correction_approvals
            (correction_id, task_id, issue_id, project_name, work_item, dimension,
             ai_finding, scheme_evidence, human_comment, correction_type, lesson_summary,
             status, submitted_by, submitted_at, reviewed_by, reviewed_at, review_comment,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', '', ?, ?)
            """,
            (
                correction_id,
                task_id,
                issue_id,
                project_name,
                work_item,
                dimension,
                ai_finding,
                _json_dumps(scheme_evidence or []),
                human_comment,
                correction_type,
                lesson_summary,
                status,
                submitter,
                submitted_at,
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()
    return correction_id


def review_correction(correction_id: str, decision: str, reviewer: str | None = None, review_comment: str = "") -> None:
    if decision not in {"approved", "rejected"}:
        raise ValueError(f"invalid correction decision: {decision}")
    conn = _get_conn()
    conn.execute(
        """
        UPDATE correction_approvals
        SET status = ?, reviewed_by = ?, reviewed_at = ?, review_comment = ?, updated_at = ?
        WHERE correction_id = ?
        """,
        (decision, reviewer or current_reviewer(), _now(), review_comment, _now(), correction_id),
    )
    conn.commit()
    conn.close()


def approved_lessons(limit: int = 30) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT * FROM correction_approvals
        WHERE status = 'approved'
        ORDER BY reviewed_at DESC, updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    result = [dict(row) for row in rows]
    for item in result:
        item["scheme_evidence"] = _json_loads(item.get("scheme_evidence"), [])
    return result


def approved_lessons_markdown(limit: int = 24) -> str:
    lessons = approved_lessons(limit=limit)
    if not lessons:
        return ""
    lines = ["## 已审批纠偏经验", "- 以下经验均来自人工提交并审批通过的逐条审核意见。"]
    for item in lessons:
        evidence = "；".join(str(x) for x in (item.get("scheme_evidence") or [])[:2])
        lines.append(
            "- "
            f"触发分项：{item.get('work_item') or '未标注'}；"
            f"问题类型：{item.get('correction_type') or '纠偏'}；"
            f"AI原判断：{item.get('ai_finding') or ''}；"
            f"人工判断：{item.get('lesson_summary') or item.get('human_comment') or ''}；"
            f"适用证据：{evidence or '需结合当前方案证据'}。"
        )
    return "\n".join(lines).strip() + "\n"


def build_review_markdown(project_name: str, issues: list[dict]) -> str:
    grouped = defaultdict(list)
    for issue in issues:
        status = issue.get("status", "pending")
        if status not in EXPORTABLE_STATUSES:
            continue
        grouped[issue.get("work_item") or "整体复核"].append(issue)
    lines = [f"# {project_name} 自动化审核结论批文", ""]
    if not grouped:
        lines += ["本次人工复审未确认任何可导出的审核意见。", ""]
        return "\n".join(lines)
    for work_item, rows in grouped.items():
        lines += [f"## {work_item}", ""]
        for idx, issue in enumerate(rows, 1):
            status_label = {
                "accepted": "保留",
                "edited": "已修改",
                "needs_more_info": "需补资料",
            }.get(issue.get("status"), issue.get("status", ""))
            lines += [
                f"### {idx}. {issue.get('dimension') or '审核意见'}（{status_label}）",
                issue.get("reviewed_result") or issue.get("original_result") or "",
                "",
            ]
    return "\n".join(lines).strip() + "\n"


# Backward-compatible wrappers for older imports. New v3 UI uses approval rows.
def record_correction(agent_name, chunk_heading, wrong_result, correction_text):
    return bool(save_correction(
        task_id="legacy",
        issue_id=f"LEGACY_{uuid.uuid4().hex[:8].upper()}",
        project_name="legacy",
        work_item=chunk_heading,
        dimension=agent_name,
        ai_finding=wrong_result,
        scheme_evidence=[],
        human_comment=correction_text,
        correction_type="legacy",
        lesson_summary=correction_text,
        status="submitted",
    ))


def get_correction_cases(agent_name=None):
    items = list_corrections(status="approved")
    if agent_name:
        items = [item for item in items if item.get("dimension") == agent_name]
    return [{
        "id": item.get("correction_id"),
        "agent": item.get("dimension"),
        "heading": item.get("work_item"),
        "wrong_result": item.get("ai_finding"),
        "correction_text": item.get("lesson_summary") or item.get("human_comment"),
    } for item in items]


def format_few_shot_prompt(agent_name):
    cases = get_correction_cases(agent_name)[-3:]
    if not cases:
        return ""
    lines = ["\n【已审批人工纠偏经验】"]
    for idx, case in enumerate(cases, 1):
        lines.append(f"经验 {idx}：避免类似判断：{case.get('wrong_result', '')[:120]}；人工修正：{case.get('correction_text', '')}")
    return "\n".join(lines) + "\n"
