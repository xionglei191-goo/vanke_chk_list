# 万科全维度工程审计系统 - 核心业务流转蓝图 (V9.0)

**此文档系工程评审最高执行标准，系统内所有智能体 (Agents) 的算力运转必须严格遵照此 DAG 推理框架执行。本版本为 V9.0 PageIndex 树索引集成版。**

## 🏗️ 核心循环审查模型 (Cyclic Cross-Check Model)

```mermaid
graph TD
    classDef mainNode fill:#f4f6f9,stroke:#2c3e50,stroke-width:2px,font-weight:bold,font-size:15px;
    classDef subNode fill:#eaf2f8,stroke:#5499c7,stroke-width:1px,color:#333;
    classDef highlightNode fill:#fdf2e9,stroke:#e67e22,stroke-width:2px,font-weight:bold,color:#d35400;
    classDef rulerNode fill:#f9e79f,stroke:#d4ac0d,stroke-width:2px,font-weight:bold,font-size:15px,color:#7d3c98;
    classDef endNode fill:#e8f8f5,stroke:#1abc9c,stroke-width:2px,font-weight:bold,font-size:15px;
    classDef asyncNode fill:#fadbd8,stroke:#c0392b,stroke-width:2px,font-style:italic,color:#900C3F;
    classDef treeNode fill:#d5f5e3,stroke:#27ae60,stroke-width:2px,font-weight:bold,font-size:15px,color:#1e8449;

    N_Async["📥 异步任务派发中心<br/>(任务落库排期)"]:::asyncNode
    
    N1["1. 图文多模态特征抓取<br/>(实景图片视网膜识别)"]:::mainNode
    N2["2. 文档工程解析<br/>(OCR表格切片处理)"]:::mainNode
    N3["3. 施工方案智能体<br/>(8大技术维度审核)"]:::mainNode
    N4["4. 清单与造价智能体<br/>(全局资金下限把控)"]:::mainNode
    N5["5. RAG 知识法官 (LLM-Reranker)<br/>【强制消减幻觉的最高准则】"]:::rulerNode
    N5T["5T. PageIndex 树索引层<br/>【V9.0 语义完整条款节点】"]:::treeNode
    N6(["6. 离线综合审计批文 (Inbox)"]):::endNode

    N_Async ==> N1
    N_Async ==> N2
    N1 ==>|汇聚视觉证据| N6
    N2 ==> N3
    N2 ==>|提取Excel/底线长文| N4

    %% V9.0 树索引通路
    N5T ==>|节点摘要→精准embedding命中| N5
    N5T ==>|节点原文→完整条款投喂| N3

    %% 循环交叉比对
    N3 ==>|【正向追踪·Agent 11】方案提及高危昂贵工艺，清单是否全额给钱？| N4
    N5 ==>|【反向核规·Agent 13】检验受审清单特征是否跌破行业合规极值？| N4
    N5 ==>|【LLM 过滤】切断无关标准，仅投靠受审段落相关的军规| N3
    
    N3 -.-> N6
    N4 -.-> N6

    %% 子节点
    subgraph Sub_方案审核 [" "]
        direction TB
        N3_1["1. 施工准备"]:::subNode
        N3_2["2. ⭐ 施工工艺与参数"]:::highlightNode
        N3_3["3. ⭐ 验收标准"]:::highlightNode
        N3_4["4. 安全管理"]:::subNode
        N3_5["5. 保修条款与防卫"]:::subNode
        N3_6["6. 工期折算核对"]:::subNode
        N3_7["7. ⭐ 合同界面划分"]:::highlightNode
        N3_8["8. 标准反查方案"]:::subNode
    end
    N3 -.-> Sub_方案审核

    subgraph Sub_清单执行 [" "]
        direction TB
        N4_1["9. 清单项齐备度检查"]:::subNode
        N4_2["10. 清单特征核验匹配"]:::subNode
        N4_3["11. 品牌与集采违约核查"]:::subNode
    end
    N4 -.-> Sub_清单执行

    subgraph Sub_标准法官 [" "]
        direction TB
        N5_1["基于 BM25+ChromaDB 的 Top-N 召回"]:::subNode
        N5_1T["V9.0 PageIndex 树节点摘要 embedding 召回"]:::treeNode
        N5_2["基于 gpt-5.4 的 LLM_Rerank 幻觉抹杀"]:::highlightNode
        N5_3["将完全相关的规矩作为金线底线提供给大模型"]:::highlightNode
    end
    N5 -.-> Sub_标准法官
```

## 🧠 多智能体协同作战守则
为落实该蓝图，系统使用 `gpt-5.4` 与微型守卫编队：
1. **拦截哨兵**：(Agent 0) 预处理切片，纯无意义废话（公司简介等）直接丢弃，不消耗推理成本。
2. **方案八座守卫 (Agent 1-8)**：分别主攻质量、工期、安全等维度，任何不合格直接报红。
3. **造价三维防线 (Agent 9-11)**：基于表格强制特征审查报价明细与漏项。
4. **十字交叉刺客 (Cross-Check Agents)**：利用 `方案->清单` 的倒逼逻辑，追杀图纸提到了但不给钱的漏洞，彻底防范未报备的签证单。

## 🌳 V9.0 知识底座升维：PageIndex 树索引
5. **树索引知识节点**：国标文档不再被 OCR 按页硬切，而是由 [PageIndex](https://github.com/VectifyAI/PageIndex) 框架通过 LLM 推理提取层级目录树，每个叶节点 = 一个语义完整的条款。节点**摘要**用于 ChromaDB/BM25 精准命中，节点**原文**用于 Agent 审查投喂；若树节点缺少 LLM summary，灌入层会生成本地结构化短摘要兜底，避免 embedding 退化为长原文截断。彻底消灭"断头规范"导致的误判和幻觉。
6. **OCR 前置兜底**：PageIndex 生成前会先评估 PDF 自带文本层；若文本层为空或质量不足，则调用项目统一 `ocr_engine` 生成逐页文本（PaddleOCR 在线引擎优先，未配置时回退 RapidOCR），再把逐页文本交给 PageIndex 组织成树节点。在线 PaddleOCR 单次提交上限按 `PADDLE_MAX_PAGES_PER_REQUEST=100` 控制，超长 PDF 自动分段提交后再合并页文本。
7. **PageIndex-first 去重**：国标规范一旦完成 PageIndex 灌入，同源旧版 OCR 按页/固定切片 legacy 条目默认标记为 `inactive`，只保留审计历史，不再参与 Chroma/BM25 active 召回，避免同一 PDF 重复 OCR、重复入库、重复命中。
