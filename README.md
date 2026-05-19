# 万科零星工程 v3 AI 主审系统

这是一个面向小型维修、改造、翻新方案的 Streamlit + SQLite 队列 + AI 主审系统。v3 已硬切旧关键词规则、多 Agent 路由和 v2 经验卡匹配，默认由大模型完整阅读小方案，注入审核经验手册，再通过本地工具复核后输出分项修改意见。

## 启动

```bash
cd auto_review_system
./start_all.sh start
```

常用命令：
- `./start_all.sh start`：后台启动前台和 Worker，并自动拉起守护巡检。
- `./start_all.sh status`：查看前台、Worker、守护巡检状态。
- `./start_all.sh stop`：停止全部服务。
- `./start_all.sh restart`：重启全部服务。
- `./start_all.sh foreground`：前台运行，便于本地观察日志。

说明：
- 脚本已默认关闭 Streamlit 的文件监听模式，避免当前机器上出现 `inotify watch limit reached` 后前台秒退。

服务组成：
- Streamlit 前台：上传方案/清单、查看任务、人工复审并导出 Word。
- Worker：消费 `data/audit_queue.db` 中的异步审核任务。

## v3 审核方式

默认引擎为 `AUDIT_ENGINE=v3_ai_review`，不提供旧引擎回退。v3 默认三阶段：

- `v3.initial_review`：完整阅读方案、报价/白单上下文和经验手册，提出初审候选问题。
- 本地 tools：只做证据检索，包括方案片段、清单片段、规范提示和未决经验台账。
- `v3.final_review` + `v3.critic`：结合工具证据终审并复核，删除无证据、偏题、机械照搬历史经验的问题。

每条审核意见固定包含：
`work_item`、`dimension`、`finding`、`scheme_evidence`、`reason`、`evidence_type`、`evidence_ref`、`recommendation`、`confidence`、`needs_followup`。

没有当前方案证据的问题只能标记为“需复核/需补资料”，不得直接判定缺陷。

## 配置

```bash
AUDIT_ENGINE=v3_ai_review
V3_AI_CALL_BUDGET=3
V3_TOOL_QUERY_LIMIT=8
V3_TOOL_RESULT_CHARS=3000
V3_REVIEW_TIMEOUT=240
V3_REVIEW_MAX_TOKENS=4096
V3_PLAYBOOK_PATH=auto_review_system/data/analysis/repair_review_playbook.md
REVIEW_USER_NAME=local_reviewer

LLM_CACHE_ENABLED=true
LLM_CACHE_TTL_DAYS=30
LLM_FAILURE_CACHE_TTL_SECONDS=600
LLM_MAX_QPS=1
LLM_MAX_CALLS_PER_MINUTE=15
LLM_MAX_RETRIES=2
LLM_THINKING_ENABLED=true
LLM_THINKING_BUDGET_TOKENS=1024
LLM_REASONING_EFFORT=medium
```

LLM 接口仍通过 `.env` 中的 `LLM_API_TYPE`、`LLM_API_URL`、`LLM_API_KEY`、`LLM_MODEL` 配置。v3 每次审核默认最多 3 次非缓存模型调用，缓存命中会记录在 `审核运行信息` 分组。

如果代理经常返回 `429 Too Many Requests`，建议先改成更保守的节流配置：

```bash
V3_AI_CALL_BUDGET=2
LLM_MAX_QPS=0.5
LLM_MAX_CALLS_PER_MINUTE=6
LLM_MAX_RETRIES=3
LLM_THINKING_ENABLED=false
```

这会把单任务 AI 调用从最多 3 次降到最多 2 次，并把全局请求速率压到每 2 秒最多 1 次、每分钟最多 6 次，优先保证任务稳定完成。

## 数据与经验

运行态目录：
- `temp_uploads/`：当前上传文件。
- `data/results/`：初审 JSON 和最终 Word。
- `data/llm_cache.db`：LLM 缓存与调用日志。
- `data/review_feedback.db`：逐条人工复审、纠偏提交、审批记录。
- `data/analysis/`：本地分析成果；默认不提交 Git，脱敏后的 `repair_review_playbook.md` 会进入仓库作为运行期经验手册。

新方向保留的核心经验文件：
- `auto_review_system/data/analysis/deep_alignment_benchmark_report.md`
- `auto_review_system/data/analysis/unresolved_review_sources.md`
- `auto_review_system/data/analysis/repair_review_playbook.md`

其中 `deep_alignment_benchmark_report.md` 和 `unresolved_review_sources.md` 保留在本地用于持续分析；`repair_review_playbook.md` 是从分析报告提炼后的运行期 prompt 手册。

`原始材料/` 必须保留，后续补齐历史审核文件时继续用于分析和更新经验手册。

人工复审回流规则：
- 每条 AI 意见独立复审，可标记为保留、已修改、误判剔除或需补资料。
- 误判和经验补充先提交审批，只有审批通过的记录会被自动重建流程纳入经验手册。
- `REVIEW_USER_NAME` 用于记录提交人/审批人；暂不接入完整账号系统。

生成/刷新经验手册：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/build_repair_playbook.py
```

也可以直接打开【📚 经验手册维护】页面查看和重建。

清理旧方向无关数据：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/cleanup_legacy_audit_state.py --apply
```

该清理会保留 `原始材料/` 和上述核心经验文件，删除旧结果、旧缓存、旧向量库、旧经验卡和旧 benchmark 产物。

## 目录

```text
auto_review_system/
├── agent_worker.py        # 后台 v3 审核 worker
├── auditors/              # v3 AI 主审与 LLM 审核提示词
├── llm/                   # LLM 客户端、缓存、配置
├── pages/                 # 上传工作台、审核收发室、经验手册维护
├── parsers/               # Word / Excel / PDF 解析
├── rag_engine/            # 队列、历史材料分析工具、可选标准检索基础设施
├── scripts/               # playbook、清理、smoke review 等维护脚本
└── utils/                 # 路径、导出、日志等通用工具
```

## 验证

```bash
bash -n auto_review_system/start_all.sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=auto_review_system .venv/bin/python -m compileall -q auto_review_system
PYTHONPATH=auto_review_system:auto_review_system/scripts .venv/bin/python tests/test_rag.py
```

真实样本 smoke：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/run_v3_smoke_review.py
```

## 常见故障

- 任务一直不跑：检查 `agent_worker.py` 是否运行，或看 `auto_review_system/logs/agent_worker.log`。
- AI 输出为空或格式错误：任务会进入 `REVIEW_PENDING`，卷宗中会显示“需复核/需补资料”，可人工复审。
- 调用次数异常：检查 `V3_AI_CALL_BUDGET=3`、`LLM_CACHE_ENABLED=true`，并查看 `审核运行信息`。
- 如果频繁出现 `429 Too Many Requests`：降低 `V3_AI_CALL_BUDGET`、`LLM_MAX_QPS`、`LLM_MAX_CALLS_PER_MINUTE`，并暂时关闭 `LLM_THINKING_ENABLED`。
- 经验手册缺失：运行 `scripts/build_repair_playbook.py`，或确认 `deep_alignment_benchmark_report.md` 仍在本地 analysis 目录。
