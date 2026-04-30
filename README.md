# 万科工程方案自动审核系统

这是一个 Streamlit + SQLite 队列 + RAG + 零星工程审核引擎的工程方案/报价清单审核系统。默认配置已经按“均衡省钱”模式优化：本地完成切片过滤、RAG 重排、WBS 补标和经验规则匹配，只把大模型调用留给真正需要生成综合结论的步骤。

## 启动

```bash
cd auto_review_system
./start_all.sh
```

服务组成：
- Streamlit 前台：上传资料、查看任务、人工复核导出 Word。
- Worker：消费 `data/audit_queue.db` 中的任务。
- Vector API：提供知识库检索接口。

## 零星工程 v2 审核

默认审核引擎为 `AUDIT_ENGINE=v2_repair`，重点面向劳务班组长编写的零星维修、改造、翻新方案。它不按“大而全”的安全/合同/造价 Agent 抢主导，而是围绕四个核心维度输出可直接修改的意见：

- 描述完整性：材料、参数、基层条件、验收指标是否写到可施工、可计价、可复核。
- 工艺合理性：材料和做法是否适合现场场景，是否有明显经验性风险。
- 分项拆分：施工动作、部位、报价/白单口径是否应拆开描述。
- 逻辑自洽：工序顺序、参数搭配、方案与清单是否互相矛盾。

结果字段包含 `dimension`、`work_item`、`finding`、`reason`、`evidence_type`、`evidence_ref`、`recommendation`、`confidence`，报告按分项工程组织，而不是按泛化 Agent 组织。

## 目录说明

```text
auto_review_system/
├── app.py                 # Streamlit 首页入口
├── pages/                 # 上传审阅、收发室、知识库管理
├── agent_worker.py        # 后台审核队列 worker
├── auditors/              # 多 Agent 审核逻辑
├── rag_engine/            # 知识库、检索、队列、WBS 分类
├── parsers/               # Word / Excel / PDF 解析
├── llm/                   # LLM 客户端、缓存、配置
├── utils/                 # 路径、导出、日志等通用工具
├── scripts/               # 维护脚本
└── data/                  # SQLite、JSON 备份、结果文件
```

## 低成本默认配置

可在根目录 `.env` 或 `auto_review_system/.env` 配置：

```bash
AUDIT_COST_PROFILE=balanced
LLM_CACHE_ENABLED=true
LLM_CACHE_TTL_DAYS=30
LLM_FAILURE_CACHE_TTL_SECONDS=600
LLM_MAX_RETRIES=2
RAG_RERANK_MODE=local
TRIAGE_MODE=local
AUDIT_ENGINE=v2_repair
REVIEW_EXPERIENCE_ENABLED=true
COST_REVIEW_MODE=explicit
REPAIR_AI_REVIEW_ENABLED=true
REPAIR_TOOL_QUERY_LIMIT=4
REPAIR_CROSS_PROJECT_EXPERIENCE=true
REPAIR_CROSS_PROJECT_MIN_OVERLAP=2
REPAIR_CROSS_PROJECT_MATCH_LIMIT=4
REPAIR_EXPERIENCE_MATCH_LIMIT=8
INCLUDE_EXPERIENCE_IN_STANDARD_RAG=false
LLM_THINKING_ENABLED=true
LLM_THINKING_BUDGET_TOKENS=1024
LLM_REASONING_EFFORT=medium
```

说明：
- `balanced` 默认关闭 LLM 哨兵和 LLM RAG reranker。
- 同一 prompt 的成功响应会缓存 30 天。
- 模型异常响应短缓存 10 分钟，避免超时期间反复扣费。
- v2 零星工程引擎默认本地匹配历史经验、主动查询规范片段，然后只做一次 AI 归因泛化判断。
- `COST_REVIEW_MODE=explicit` 表示只有明确上传报价/清单或方案内出现清单交叉点时，才触发方案清单一致性检查。
- `REPAIR_CROSS_PROJECT_EXPERIENCE=true` 会启用跨项目经验泛化；`REPAIR_CROSS_PROJECT_MIN_OVERLAP=2` 要求至少命中核心工程词，`REPAIR_CROSS_PROJECT_MATCH_LIMIT=4` 限制跨项目补充数量，并会按当前方案重新判断控制点，避免照搬历史源方案结论。
- `LLM_THINKING_ENABLED=true` 会向支持的模型传递 thinking/reasoning 参数；最终报告不会输出思维链。

如果需要恢复深度审查，可临时设置：

```bash
AUDIT_COST_PROFILE=quality
RAG_RERANK_MODE=llm
TRIAGE_MODE=llm
AGENT_ROUTING_ENABLED=false
AUDIT_ENGINE=legacy
```

## 数据目录

所有运行态路径固定在 `auto_review_system/` 内：
- `temp_uploads/`：上传的方案、清单、图片和知识库源文件。
- `data/results/`：初审 JSON 和最终 Word 报告。
- `data/knowledge_base.db`：知识库主库。
- `data/knowledge_base.json`：知识库 JSON 备份。
- `data/llm_cache.db`：大模型响应缓存与调用统计。
- `data/analysis/`：本地原始材料分析报告、经验卡和基准案例，默认被 Git 忽略。
- `vector_db_storage/`：ChromaDB 向量库。

历史队列中的绝对路径和旧相对路径仍会被兼容解析。

## 原始材料经验库

系统已支持从 `原始材料/审核意见.xlsx` 和对应方案样本中整理“人类审核方法论”，并将审核意见拆成结构化经验卡。原始业务材料和完整明细不提交 GitHub，生成物保存在本地 ignored 目录：

- `auto_review_system/data/analysis/raw_material_review_report.md`
- `auto_review_system/data/analysis/review_experience_cards.json`
- `auto_review_system/data/analysis/review_benchmark_cases.json`
- `auto_review_system/data/analysis/review_methodology.json`
- `auto_review_system/data/analysis/review_deep_attribution_cases.json`
- `auto_review_system/data/analysis/repair_v2_benchmark_report.md`
- `auto_review_system/data/analysis/unresolved_review_sources.md`
- `auto_review_system/data/analysis/unresolved_review_source_manifest.csv`

先 dry-run 查看拆解数量和维度分布：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/build_review_experience_kb.py --dry-run
```

确认后写入知识库：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/build_review_experience_kb.py --apply
```

该脚本会停用旧的“城市公司检查结果”整表规则，重新写入逐条 `review_experience` 经验规则，并同步 SQLite、JSON 备份和 Chroma 向量库。经验卡会保留专业归因、工程师追问、应补资料、泛化规则以及从对应方案/白单中抽取到的原文证据片段。

运行 v2 零星工程基准案例：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/run_repair_benchmark.py
```

该脚本默认关闭 AI，只验证本地规则、历史经验泛化、控制点判断和补写建议，不产生大模型调用费用。脚本会从本地 ignored 的 `原始材料/` 自动发现代表样本；如需固定样本，可传入 `--cases-file auto_review_system/data/analysis/repair_benchmark_cases.local.json`。

分析仍无法对齐原方案的审核意见：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/analyze_unresolved_review_sources.py
```

### 无法判断来源分析与补件闭环

`analyze_unresolved_review_sources.py` 用来回答“哪些审核意见还没有真正对照到原始方案”。它不会调用大模型，只读取 `审核意见.xlsx`、本地 `原始材料/方案评审/` 和已有解析规则，输出三份本地 ignored 文件：

- `unresolved_review_sources.md`：给人看的缺口报告，按项目列出无法判断数量、当前匹配文件、匹配质量和需要补充的资料。
- `unresolved_review_sources.json`：完整结构化明细，包含每条意见的行号、意见原文、维度、工程类别、缺口原因和应补资料。
- `unresolved_review_source_manifest.csv`：补件工作表，给每个项目预留 `user_supplied_path` 和 `notes`。

当前基线结果是：`432` 条原子审核意见中，`243` 条仍为“无法判断”；其中 `205` 条未匹配到本地原始材料文件，`38` 条属于低质量模糊匹配、疑似错配。高/中质量已匹配文件已经完成全文控制点检索并进入 `已补齐/部分补齐/仍缺失/无需处理`，不再残留“已匹配但未定位触发片段”的意见。

补充原始方案后，在 `unresolved_review_source_manifest.csv` 的 `user_supplied_path` 填入文件路径。路径支持三种写法：

- 相对 `原始材料/方案评审/` 的文件名，例如 `某项目施工方案.xlsx`。
- 相对项目根目录的路径，例如 `原始材料/补充方案/某项目施工方案.xlsx`。
- 绝对路径，例如 `/home/xionglei/.../某项目施工方案.xlsx`。

`source_manifest` 也支持 JSON：可以是 `{项目名称: 文件路径}`，也可以是包含 `project_name`、`user_supplied_path` 字段的数组。CSV 或 JSON 中不存在的文件会被忽略，所以可以分批补资料、分批重跑。

补件后的复跑顺序：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/analyze_unresolved_review_sources.py --source-manifest auto_review_system/data/analysis/unresolved_review_source_manifest.csv
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/build_review_experience_kb.py --source-manifest auto_review_system/data/analysis/unresolved_review_source_manifest.csv --dry-run
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/build_review_experience_kb.py --source-manifest auto_review_system/data/analysis/unresolved_review_source_manifest.csv --apply
```

复跑后先看 `unresolved_review_sources.md` 中“无法判断”数量是否下降，再看 `deep_alignment_benchmark_report.md` 和 `review_experience_cards.json` 是否新增了 `部分补齐/仍缺失/已补齐` 的证据对齐结果。确认无误后再执行 `--apply` 写入 SQLite、JSON 备份和 Chroma 向量库。

## WBS 本地补标

先 dry-run 查看分布：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/backfill_wbs_heuristic.py --dry-run
```

确认后写入：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/backfill_wbs_heuristic.py --apply
```

该脚本不调用大模型，只对 active 且仍为“通用”的规则做高置信度关键词补标。

当前项目已经执行过一次本地补标：active 规则中 `1285` 条已从“通用”迁移到具体 WBS，剩余低置信度规则保留“通用”以避免误挂载。

## 知识库质量清洗

先 dry-run 观察会停用哪些明显噪声：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/audit_kb_quality.py --dry-run
```

确认后写入质量分、停用明显噪声并刷新向量库：

```bash
PYTHONPATH=auto_review_system .venv/bin/python auto_review_system/scripts/audit_kb_quality.py --apply
```

该脚本只停用明显无审查价值的封面/版权/目录残片/人员名单/占位符内容，不删除规则；历史仍保留在 SQLite 和 JSON 备份中。

## 常见故障

- 页面能打开但任务一直不跑：检查 `agent_worker.py` 是否运行，或看 `auto_review_system/logs/agent_worker.log`。
- 报告无法下载：先确认任务状态是 `REVIEW_PENDING` 或 `COMPLETED`，系统会兼容历史路径并解析到 `data/results/`。
- 大模型费用异常升高：确认 `.env` 中 `AUDIT_COST_PROFILE=balanced`、`RAG_RERANK_MODE=local`、`TRIAGE_MODE=local`、`LLM_CACHE_ENABLED=true`。
- 大模型频繁超时：降低 `AGENT_MAX_SCHEME_AGENTS` 或 `LLM_MAX_CALLS_PER_MINUTE`，并保持 `LLM_MAX_RETRIES=2`。
- 知识库看起来没有更新：SQLite 是主库，JSON 只是备份；重新启动服务或运行验证脚本确认 `knowledge_base.db`、`knowledge_base.json` 和 Chroma 数量一致。
- OCR 不可用：没有 `PADDLE_API_TOKEN` 时在线 PaddleOCR 会不可用，系统会尝试本地 OCR 或 PDF 文本层解析。

## 验证

```bash
bash -n auto_review_system/start_all.sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=auto_review_system .venv/bin/python -m compileall -q auto_review_system
PYTHONPATH=auto_review_system:auto_review_system/scripts .venv/bin/python tests/test_rag.py
```

项目当前采用标准脚本测试，不依赖 pytest；`tests/` 仅保留可重复运行的核心 RAG/路径/缓存/WBS 验证脚本。
