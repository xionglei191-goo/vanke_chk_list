# 万科零星工程审核系统 v3 维护说明

v3 已硬切为 AI 主审架构。旧多 Agent 管线、v2 repair 规则引擎、关键词路由和历史经验卡运行期匹配不再维护。

## 当前核心
- `agent_worker.py`：解析上传文件并调用 v3 审核。
- `auditors/v3_ai_review.py`：三阶段 AI 主审、本地 tools、结果分组和运行信息。
- `auditors/v3_prompts.py`：初审、终审、质量复核 prompt。
- `rag_engine/review_workflow.py`：逐条人工复审、纠偏提交、审批和导出规则。
- `rag_engine/playbook_manager.py`：经验手册构建、读取和重建。
- `data/analysis/repair_review_playbook.md`：运行期经验教训手册。

## 维护原则
- 经验来自 `deep_alignment_benchmark_report.md` 和 `unresolved_review_sources.md`，先沉淀为 playbook，再注入 prompt。
- 本地程序只负责解析、证据检索、调用预算、缓存、格式校验和报告导出。
- 审核判断交给 AI，但每条意见必须有当前方案证据；无证据只能标记需复核/需补资料。
- 人工纠偏不直接入库，必须先提交审批；只有审批通过的经验才会被自动重建流程纳入。

## 常用命令
```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/build_repair_playbook.py
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/run_v3_smoke_review.py
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/cleanup_legacy_audit_state.py --apply
```

经验手册也可在【📚 经验手册维护】页面查看和重建。
