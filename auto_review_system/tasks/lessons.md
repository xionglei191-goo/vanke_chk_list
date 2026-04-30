# 教训沉淀

## 2026-04-08: V9.1 架构审查

### 教训 1：合并函数时必须全文搜索变量引用
合并 `_display_rule_content` 和 `_select_rule_content` 后，遗漏了 `build_bm25_index()` 中对 `search_content` 的引用，导致 `NameError`。
**规则**：删除/重命名任何函数或变量后，必须 `grep_search` 全文件所有引用点。

### 教训 2：re-export 策略是大型拆分的安全网
拆分 `engineering_auditor.py` → `llm/` 时，10+ 个文件直接导入它的符号。通过 re-export 实现零下游修改，避免了风险爆炸。
**规则**：拆分大模块时，旧模块应 re-export 所有公开符号，仅在后续迭代逐步迁移下游导入。

### 教训 3：模块级副作用需要注意
`vector_store.py` 在模块加载时执行 `init_vector_db()` → `build_bm25_index()`，任何该模块的 import 都会触发完整的 BM25 构建。这使得简单的 import 测试也需要等待 jieba 分词完成。
**规则**：将模块级副作用移至显式的初始化函数，或使用 lazy-init 模式。
