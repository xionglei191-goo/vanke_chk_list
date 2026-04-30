# 万科工程方案自动审核系统 — 架构审查与优化规划

> **审查日期**: 2026-04-08
> **审查范围**: 全项目代码 (`auto_review_system/` + 根目录脚本)
> **版本**: V9.0 (PageIndex 树索引集成后)

---

## 一、项目整体架构剖析

### 当前架构组成

```
┌─────────────────────────────────────────────────────────────────┐
│ 前端层: app.py (Streamlit)                                      │
│   - 专家审阅面板 / 审核结果收发室 / 知识库管理                     │
├─────────────────────────────────────────────────────────────────┤
│ 任务队列层: rag_engine/queue_manager.py (SQLite)                 │
│   - 异步任务投递/取件，支持 PAUSE/CANCEL/RESUME                  │
├─────────────────────────────────────────────────────────────────┤
│ 后台工作器: agent_worker.py (Daemon Loop)                        │
│   - 文件解析 → 规则检索 → 多智能体审查 → 结果 JSON 封存          │
├─────────────────────────────────────────────────────────────────┤
│ 文档解析层: parsers/ (word/pdf/excel)                             │
│   - ODL/OCR/PageIndex 三路由 PDF 解析                            │
├─────────────────────────────────────────────────────────────────┤
│ RAG 知识引擎: rag_engine/ (ChromaDB + BM25 + JSON)               │
│   - 双路召回 → RRF 融合 → LLM Reranker → 邻居扩展/PageIndex     │
├─────────────────────────────────────────────────────────────────┤
│ 审计智能体: auditors/ (13-Agent Pipeline)                        │
│   - 8 方案特工 + 3 造价特工 + 2 交叉特工 + 哨兵 + Vision         │
├─────────────────────────────────────────────────────────────────┤
│ 工具/脚本层: scripts/, utils/, ocr_engine/                       │
│   - 树索引生成器 / Word 导出 / OCR 引擎注册                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、发现的冲突与重复

### 🔴 CONFLICT-1: `_filter_rule_objects` 函数重复定义

**位置**:
- `rag_engine/kb_manager.py` L22-25
- `rag_engine/vector_store.py` L82-85

**问题**: 两个文件各自定义了完全相同的函数，违反 DRY 原则。`kb_manager.py` 已经从 `vector_store.py` 导入了多个符号（L8-13），但 `_filter_rule_objects` 未被导入而是重新定义。

**修复方案**:
- [ ] 从 `vector_store.py` 导出，`kb_manager.py` 统一导入

---

### 🔴 CONFLICT-2: PageIndex 树遍历函数重复实现

**位置**:
- `parsers/pdf_parser.py` L34-82:
  - `_pageindex_roots()` / `_pageindex_children()` / `_flatten_pageindex_leaf_nodes()`
- `rag_engine/kb_manager.py` L99-148:
  - `_tree_roots()` / `_tree_children()` / `flatten_tree_leaf_nodes()`

**问题**: 两套相同逻辑的树遍历函数，功能完全一致（遍历 PageIndex 树 JSON、提取叶节点），仅命名不同。维护时极易产生一处修了另一处没修的不一致 bug。

**修复方案**:
- [ ] 将树遍历工具函数统一抽取到 `rag_engine/tree_utils.py` 或 `utils/tree_utils.py`
- [ ] `pdf_parser.py` 和 `kb_manager.py` 统一导入

---

### 🟡 CONFLICT-3: `_select_rule_content` 和 `_display_rule_content` 功能重复

**位置**: `rag_engine/vector_store.py`
- `_select_rule_content()` L75-79
- `_display_rule_content()` L96-100

**问题**: 两个函数逻辑完全相同（优先返回 `condensed_content`，否则返回 `content`）。`_select_rule_content` 用于 BM25 索引构建，`_display_rule_content` 用于邻居索引构建，但实际代码一字不差。

**修复方案**:
- [ ] 合并为一个函数，消除隐性维护负担

---

### 🟡 CONFLICT-4: `app.py` 两处重复 `import pandas as pd`

**位置**: `app.py` L432 和 L491

**问题**: 在知识库管理的同一代码分支中，`pandas` 被导入了两次。虽然 Python 的模块缓存机制不会导致性能问题，但代码可读性和整洁度受影响。

**修复方案**:
- [ ] 将 `import pandas as pd` 移至文件顶部或分支开头统一导入一次

---

### 🟡 CONFLICT-5: Agent 编号与变量命名不一致

**位置**: `auditors/multi_agent.py` L37-48

**问题**:
- `agent8_completeness` 函数名中 Agent ID 为 8，但在 multi_agent.py 中结果变量名为 `r9`，日志标记为 `Agent 9 [造价齐备度]`
- `agent9_feature_match` → 变量 `r10`，标记 `Agent 10`
- `agent10_brand_contract` → 变量 `r11`，标记 `Agent 11`

这种"函数名编号"与"业务语义编号"的不一致，增加了理解成本和出 bug 的风险。

**修复方案**:
- [ ] 统一命名规范：要么全部使用业务语义编号，要么全部使用函数物理编号。建议以 `multi_agent.py` 中的业务编号为准，重命名 `cost_agents.py` 中的函数

---

### 🟡 CONFLICT-6: `cost_auditor.py` 成为死代码

**位置**: `auditors/cost_auditor.py`

**问题**: `audit_cost()` 函数使用硬编码的简单规则做造价校验（如限价阈值字典），但自从 V4.0+ 引入了 Multi-Agent 造价特工体系（cost_agents.py 的 agent8/9/10）后，`audit_cost()` 已不在任何业务主路径中被调用。`app.py` L10 仍有 `from auditors.cost_auditor import audit_cost` 导入，但该函数仅在 `app.py` 的旧分支中可能被触发。

**修复方案**:
- [ ] 确认 `audit_cost()` 是否仍有保留价值（如快速离线校验场景）
- [ ] 如无用则删除文件和导入；如有用则标注为 legacy 独立工具

---

### 🟡 CONFLICT-7: 根目录遗留脚本与主系统功能重叠

**位置**: 
- `match.py` (根目录) — 方案评审文件与 Excel 模糊匹配
- `process_files.py` (根目录) — 匹配后文件移动和 Excel 批注
- `process_results.py` (根目录) — 解析 result.md 后操作

**问题**: 这三个脚本是项目早期的独立工具，功能与 `auto_review_system/` 的主系统完全独立。但它们占据根目录，与主系统的 `app.py` 容易混淆。

**修复方案**:
- [ ] 归档到 `legacy_tools/` 子目录，或迁移为 `auto_review_system/scripts/` 的一部分

---

## 三、架构层面优化建议

### 🔴 ARCH-1: `app.py` 单文件 564 行——上帝文件反模式

**现状**: `app.py` 承担了全部 Streamlit UI 逻辑（三个模式的完整页面），包括文件上传处理、任务队列 UI、知识库 CRUD 管理面板、WBS 数据加载、CSS 美化等。

**问题**:
- 任何 UI 修改都要在 564 行中定位
- Session state 管理散落各处
- 无法独立测试各页面模块

**优化方案**:
- [ ] 拆分为多页面 Streamlit 应用：
  ```
  app.py                  → 主入口 + 导航
  pages/audit_panel.py    → 专家审阅面板
  pages/inbox.py          → 审核结果收发室
  pages/kb_admin.py       → 知识库管理
  ui/styles.py            → CSS/主题集中管理
  ```

---

### 🔴 ARCH-2: `engineering_auditor.py` 职责过重——LLM 通讯层 + 审计逻辑混合

**现状**: `engineering_auditor.py` (494行) 同时承担:
1. LLM API 通讯基础设施（完整的 OpenAI/Anthropic 双协议适配、流式解析、QPS 限流、重试机制）
2. 审计业务逻辑（`audit_engineering_scheme()`、`predict_wbs_code()`、`analyze_vision_wbs()`、`llm_rerank_rules()`）

**问题**: 
- `call_llm()` 被 8+ 个文件导入使用，它是全项目的基础设施，不应与特定审计逻辑耦合
- 修改 LLM 通讯参数可能意外影响审计 prompt
- `scripts/build_tree_index.py` 深度依赖此文件内的 `_build_chat_payload`、`_post_chat_completion` 等私有函数

**优化方案**:
- [ ] 拆分为:
  ```
  llm/client.py           → LLM 通讯基础设施（call_llm, streaming, throttle, retry）
  llm/config.py           → API 配置（URL, KEY, MODEL 环境变量解析）
  auditors/engineering_auditor.py → 仅保留审计业务逻辑
  ```

---

### 🟡 ARCH-3: 知识库存储架构——JSON 文件锁风险

**现状**: `knowledge_base.json`（7.2MB, 2400+ 条）作为知识库的主存储，每次读写都是 `全量加载 → 修改 → 全量回写`。

**问题**:
- `agent_worker.py` 后台进程和 `app.py` 前台都会读写同一个 JSON 文件
- 无文件锁机制，并发写入可能导致数据丢失
- 文件越大，单次 I/O 越慢
- `get_all_rules()` 被高频调用（知识库页面每次刷新、每次灌入前、每次批量编辑时），每次都是全量反序列化

**优化方案**:
- [ ] 短期：引入 `filelock` 库或 `fcntl` 文件锁保护写入
- [ ] 中期：将知识库主存储迁移到 SQLite（与 queue_manager 一致），JSON 仅作导入导出格式
- [ ] `get_all_rules()` 增加内存级缓存 + 脏标记刷新机制

---

### 🟡 ARCH-4: `queue_manager.py` 每次操作都调用 `init_db()`

**位置**: `rag_engine/queue_manager.py`

**问题**: `add_task()`、`get_pending_task()`、`get_all_tasks()` 等每个函数开头都调用 `init_db()`。虽然 `CREATE TABLE IF NOT EXISTS` 幂等无害，但每次操作都重新打开/关闭 SQLite 连接，且重复执行 DDL 语句。

**优化方案**:
- [ ] 使用模块级初始化（导入时执行一次）
- [ ] 引入连接池或者单例 Connection 管理

---

### 🟡 ARCH-5: 日志系统缺失——全局 `print()` 依赖

**现状**: 整个项目几乎没有使用 Python `logging` 模块（仅 `pdf_parser.py` 和 `ocr_engine/` 使用了 `logger`）。`agent_worker.py`、`engineering_auditor.py`、`multi_agent.py`、`kb_manager.py` 等核心模块全部使用 `print()` 输出。

**问题**:
- 无法按级别过滤日志（debug/info/warning/error）
- 无法将日志持久化到文件同时保持控制台输出
- 生产环境中 `print()` 可能被吞掉或丢失

**优化方案**:
- [ ] 建立统一 `utils/logger.py`，配置 root logger
- [ ] 逐步将 `print()` 替换为 `logger.info/warning/error`
- [ ] 配置 `RotatingFileHandler` 确保日志不会无限膨胀

---

### 🟡 ARCH-6: 错误处理过于宽泛——裸 `except` 遍布

**位置**:
- `kb_manager.py` L607: `except: return []`
- `kb_manager.py` L801: `except: pass`
- `vector_store.py` L365: `except: pass`
- `vector_store.py` L757: `except: pass`
- `queue_manager.py` 多处 `except: pass`
- `app.py` L263: `except: reports = {}`

**问题**: 裸 `except` 会捕获包括 `KeyboardInterrupt`、`SystemExit` 在内的所有异常，且不记录任何错误日志，导致生产环境中问题难以定位。

**优化方案**:
- [ ] 将 `except:` 改为 `except Exception as e:`
- [ ] 在 except 块中至少 `logger.warning()` 记录异常

---

### 🟡 ARCH-7: `correction_manager.py` 未与 Multi-Agent 管线集成

**现状**: `correction_manager.py` 提供了完整的纠偏案例记录和 Few-Shot Prompt 生成功能，`scheme_agents.py` 和 `cost_agents.py` 也确实调用了 `format_few_shot_prompt()`。但 `app.py` 中原本的人工纠偏 UI（V4.0 设计的 `[✅ 采纳]` `[✏️ 误判]` 按钮）在重构为异步队列后被简化为纯文本编辑框，**录入纠偏案例的交互入口已丢失**。

**问题**: `data/correction_cases.json` 当前内容为 `[]`，纠偏飞轮的数据入口被堵死。

**优化方案**:
- [ ] 在「审核结果收发室」的 REVIEW_PENDING 审校界面中，为每个 Agent 结论增加「❌ 标记误判」按钮
- [ ] 录入的纠偏数据通过 `record_correction()` 写入 `correction_cases.json`

---

## 四、代码质量改进建议

### 🟡 CODE-1: `_display_rule_content` 在 `build_bm25_index` 中的语义不清

`build_bm25_index` 中同时使用了 `_select_rule_content`（用于 BM25 分词）和 `_display_rule_content`（用于邻居索引），但两个函数逻辑完全相同。

- [ ] 合并或明确区分两者的职责边界

---

### 🟡 CODE-2: `agent_worker.py` 中 `import` 置于循环体内

**位置**: L54 `import base64`, L55 `from auditors.engineering_auditor import analyze_vision_wbs`

**问题**: 这两个 import 在 `for file_item in file_paths` 循环体内，每次处理文件都会执行。虽然 Python import 有缓存，但代码意图不清晰。

- [ ] 移至文件顶部

---

### 🟡 CODE-3: `rag_engine/api.py` 导入路径错误（无法独立运行）

**位置**: L7 `from auto_review_system.rag_engine.vector_store import ...`

**问题**: 使用了绝对包路径，但项目并非标准 Python 包（无 `setup.py` / `pyproject.toml`）。该文件无法在当前目录结构下被 `start_all.sh` 以 `start_vector_api.py` 启动。`start_vector_api.py` 导入路径也需要确认。

- [ ] 统一使用相对导入或修正 PYTHONPATH 设置

---

### 🟡 CODE-4: `match.py`/`process_files.py`/`process_results.py` 中 `similar()` 函数重复

根目录三个脚本中 `similar(a, b)` 函数（基于 `SequenceMatcher`）重复定义。

- [ ] 若保留这些脚本，抽取为共享工具函数

---

### ⚪ CODE-5: 测试文件散落主代码目录

**位置**: `auto_review_system/` 目录下有 13 个 `test_*.py` 文件

**问题**: 测试文件与生产代码混在一起，增加了目录噪音。

- [ ] 归集到 `tests/` 子目录

---

### ⚪ CODE-6: 临时调试文件和日志污染

**位置**:
- `auto_review_system/output.txt` (1.5MB)
- `auto_review_system/force_wash.log` (40KB)
- `auto_review_system/worker.log`
- `auto_review_system/startup.log`
- `auto_review_system/temp_debug_json/`
- `auto_review_system/debug_table.py`
- `auto_review_system/dummy.pdf`, `t.pdf`
- 根目录: `nohup.out` (80KB), `agent_worker.log`, `strace.log` (2.7MB)
- 根目录: 6 个 `test_paddle*.py`, `test_kb.py`
- 根目录: 多个 `.log` 文件

- [ ] 清理临时/调试文件
- [ ] 将日志输出目录统一到 `logs/`
- [ ] 更新 `.gitignore` 排除这些文件

---

### ⚪ CODE-7: `=2.9.1` 异常文件

**位置**: `auto_review_system/=2.9.1` (11KB)

**问题**: 文件名 `=2.9.1` 看起来是一次 `pip install` 命令错误产生的文件（如 `pip install package=2.9.1` 遗留）。

- [ ] 确认并删除

---

## 五、性能与可靠性优化

### 🟡 PERF-1: Multi-Agent 串行瓶颈

**现状**: `multi_agent.py` 的 `run_linear_pipeline` 对每个文档切片**串行**调用 8 个方案特工（每个特工一次 LLM 调用），每个切片至少 8 次 API 请求。

**影响**: 如果文档有 20 个切片，则需要 160+ 次 LLM 调用，按限流 2 QPS 计算，仅方案审查就需要 80+ 秒。

**优化方案**:
- [ ] 对同一切片的 8 个方案特工使用 `concurrent.futures.ThreadPoolExecutor` 并行调用（QPS 已由底层 `_throttle_qps` 全局控制，不会超限）
- [ ] 或引入 LLM 批处理接口（如 OpenAI Batch API），一次性提交多个 prompt

---

### 🟡 PERF-2: `get_all_rules()` 无缓存反复加载 7.2MB JSON

**位置**: `rag_engine/kb_manager.py` L596-609

多个函数在单次用户操作中会**多次**调用 `get_all_rules()`（如 `ingest_standard_doc` 先调一次获取已有规则，再调一次写入后刷新）。

- [ ] 引入简单的模块级缓存：读取时检查文件修改时间戳，仅变更后重新加载

---

### 🟡 PERF-3: BM25 索引在知识库每次修改时全量重建

每次 `save_washed_rule()`、`update_rule()`、`batch_update_rules()` 后都会调用 `build_bm25_index(rules)` 重建整个 BM25 索引。当知识库有 2400+ 条时，Jieba 分词 + BM25 构建的开销不可忽略。

- [ ] 评估增量更新 BM25 索引的可行性
- [ ] 或延迟重建：标记脏位，在下次 `retrieve_rules` 时才触发重建

---

## 六、安全与运维改进

### 🟡 SEC-1: API Key 硬编码在 `engineering_auditor.py` 默认值中

**位置**: L22-28

```python
API_URL = _configured_api_url or "https://your-llm-endpoint.example/v1/messages"
LLM_MODEL = os.getenv("LLM_MODEL") or "qwen3.5-plus"
```

**问题**: 虽然优先读取环境变量，但 IP 地址和模型名作为代码中的默认值，在开源或转交时可能泄露内部基础设施信息。

- [ ] 将所有默认 API 端点移至 `.env.example` 作为注释说明，代码中不保留硬编码 URL

---

### 🟡 SEC-2: `LLM_SSL_VERIFY=false` 存在中间人攻击风险

**位置**: `engineering_auditor.py` L32, L186-190

当 `LLM_SSL_VERIFY=false` 时，不仅跳过证书校验，还全局 suppress urllib3 的 InsecureRequestWarning，掩盖了安全风险。

- [ ] 在日志中明确警告 SSL 校验已关闭
- [ ] 建议生产环境强制使用合法证书

---

### ⚪ OPS-1: `start_all.sh` 缺乏进程健康检查

当前启动脚本仅并行拉起三个进程，没有健康检查机制。如果 `agent_worker.py` 悄悄崩溃，不会有任何通知或自动重启。

- [ ] 引入 `supervisord` 或简单的 watchdog 脚本
- [ ] 或在 `start_all.sh` 中增加进程心跳检测和自动重启逻辑

---

## 七、执行优先级排序

### P0 — 必须立即处理（影响正确性）

| # | 任务 | 涉及文件 | 状态 |
|---|------|----------|------|
| 1 | CONFLICT-1: 消除 `_filter_rule_objects` 重复定义 | `kb_manager.py`, `vector_store.py` | ✅ |
| 2 | CONFLICT-2: 统一 PageIndex 树遍历函数 | `pdf_parser.py`, `kb_manager.py` → `utils/tree_utils.py` | ✅ |
| 3 | CONFLICT-5: Agent 编号命名统一 | `cost_agents.py`, `multi_agent.py` | ✅ |
| 4 | ARCH-6: 消除裸 `except` | `kb_manager.py`, `vector_store.py`, `app.py` | ✅ |

### P1 — 短期优化（影响可维护性和性能）

| # | 任务 | 涉及文件 | 状态 |
|---|------|----------|------|
| 5 | ARCH-2: 拆分 LLM 通讯层与审计逻辑 | → `llm/config.py` + `llm/client.py` | ✅ |
| 6 | ARCH-3: 知识库 JSON 文件锁保护 | `kb_manager.py` | ✅ |
| 7 | ARCH-5: 统一日志系统替代 print() | → `utils/log.py` + 核心模块 | ✅ |
| 8 | CONFLICT-3: 合并重复内容选择函数 | `vector_store.py` | ✅ |
| 9 | CONFLICT-6: 清理死代码 `cost_auditor.py` | `auditors/cost_auditor.py`, `app.py` | ✅ |
| 10 | ARCH-7: 恢复纠偏数据录入入口 | `app.py` + `correction_manager.py` | ✅ |
| 11 | CODE-3: 修复 `api.py` 导入路径 | `rag_engine/api.py` | ✅ |

### P2 — 中期架构升级（影响扩展性）

| # | 任务 | 涉及文件 | 状态 |
|---|------|----------|------|
| 12 | ARCH-1: 拆分 app.py 为多页面 Streamlit | `app.py` → `pages/` + `ui_config.py` | ✅ |
| 13 | ARCH-3-中期: 知识库主存储迁移 SQLite | → `rag_engine/kb_store.py` (双写) | ✅ |
| 14 | ARCH-4: queue_manager 连接管理优化 | `queue_manager.py` | ✅ |
| 15 | PERF-1: Multi-Agent 并行化 | `multi_agent.py` (ThreadPoolExecutor) | ✅ |
| 16 | PERF-2: get_all_rules() 缓存机制 | `kb_manager.py` (mtime 缓存) | ✅ |
| 17 | PERF-3: BM25 索引增量更新 | `vector_store.py` (脏标记懒重建) | ✅ |

### P3 — 整理清洁（不影响功能）

| # | 任务 | 涉及文件 | 状态 |
|---|------|----------|------|
| 18 | CONFLICT-4: 清理重复 pandas import | `app.py` | ✅ |
| 19 | CONFLICT-7: 根目录遗留脚本归档 | → `legacy_tools/` | ✅ |
| 20 | CODE-2: agent_worker.py import 上移 | `agent_worker.py` | ✅ |
| 21 | CODE-5: 测试文件归集 tests/ | → `tests/` | ✅ |
| 22 | CODE-6: 清理临时文件和日志 | 全项目 + `.gitignore` | ✅ |
| 23 | CODE-7: 删除异常文件 `=2.9.1` | 已删除 | ✅ |
| 24 | SEC-1: 移除硬编码 API URL 默认值 | `engineering_auditor.py` | ✅ |
| 25 | OPS-1: 增加进程健康检查 | `start_all.sh` | ✅ |

---

## 八、已完成的历史优化记录 (V1.0 → V9.0)

> 以下条目来自历史 todo.md，已全部完成，此处存档为上下文参考。

- [x] V2.0: 结构化解析器（Step 1-2）
- [x] V3.0: Multi-Agent 路由系统（Step 3-4）
- [x] V4.0: 专家协同与自我进化飞轮（Step 5-6）
- [x] V5.0: 交叉比对刺客 + Vision Agent（Step 7）
- [x] V6.0: 三角交叉比对模型（Step 10）
- [x] V7.0: LLM Reranker + 异步队列 + QPS 防护（Step 13-14）
- [x] V8.0: 滑动窗口重叠切片 + 邻居扩展召回（Step 15-16）
- [x] V9.0: PageIndex 树索引集成 + 知识库灌入重构 + 质量校验门禁（Step 18-21）
- [x] V9.0 Code Review: BUG-1~5 + MINOR-1~2（全部修复）
- [x] V9.1: 架构审查与代码清理（25/25 项全部完成 ✅）
