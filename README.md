# 万科工程方案自动审核系统

这是一个 Streamlit + SQLite 队列 + RAG + Multi-Agent 的工程方案/报价清单审核系统。默认配置已经按“均衡省钱”模式优化：本地完成切片过滤、RAG 重排、WBS 补标和 Agent 路由，只把大模型调用留给最终审核结论。

## 启动

```bash
cd auto_review_system
./start_all.sh
```

服务组成：
- Streamlit 前台：上传资料、查看任务、人工复核导出 Word。
- Worker：消费 `data/audit_queue.db` 中的任务。
- Vector API：提供知识库检索接口。

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
```

说明：
- `balanced` 默认关闭 LLM 哨兵和 LLM RAG reranker。
- 同一 prompt 的成功响应会缓存 30 天。
- 模型异常响应短缓存 10 分钟，避免超时期间反复扣费。
- 方案 Agent 默认启发式路由，通常只跑 2-5 个相关 Agent。

如果需要恢复深度审查，可临时设置：

```bash
AUDIT_COST_PROFILE=quality
RAG_RERANK_MODE=llm
TRIAGE_MODE=llm
AGENT_ROUTING_ENABLED=false
```

## 数据目录

所有运行态路径固定在 `auto_review_system/` 内：
- `temp_uploads/`：上传的方案、清单、图片和知识库源文件。
- `data/results/`：初审 JSON 和最终 Word 报告。
- `data/knowledge_base.db`：知识库主库。
- `data/knowledge_base.json`：知识库 JSON 备份。
- `data/llm_cache.db`：大模型响应缓存与调用统计。
- `vector_db_storage/`：ChromaDB 向量库。

历史队列中的绝对路径和旧相对路径仍会被兼容解析。

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
