# 架构决策记录


## 决策：保持 NumPy 向量检索

日期：2026-07

原因：

当前知识库包含 **38 条**短文本习惯模板。使用 NumPy 在内存中计算 cosine similarity，能够保持检索实现轻量、透明并易于测试。当前规模没有引入外部向量数据库的工程必要性。若模板数量显著增长，可重新评估 FAISS、ChromaDB 等方案。


## 决策：采用有边界的 AI 工作流

日期：2026-07

原因：

当前业务场景仅需有边界的单步骤工作流：

- RAG 推荐（召回 → 生成）
- 受控 Function Calling（解析 → 校验 → 确认 → 写入）
- AI 报告生成（统计 → 生成）

各流程相互独立，有明确的校验、降级和用户确认边界。增加多步骤 Agent 不会在当前场景中带来可证明的用户价值，同时会增加测试和维护成本。


## 决策：切换到 Cosine Similarity + 初始 Relevance Threshold

日期：2026-07-28

原因：

项目内置离线评估结果（16 cases，38 templates）显示：

- Cosine Hit@1：61.5%，优于 Raw Dot 的 53.8%
- Cosine MRR：0.7308，优于 Raw Dot 的 0.6885
- 其他 Top-K 指标持平或略优

Cosine 同时在相关查询（positive Top-1 min ≈ 0.407）与无关查询（negative Top-1 max ≈ 0.181）之间提供了清晰的分数分界，使得基于 threshold 的 query-level gate 成为可能。

Initial threshold 0.25 基于当前评估集的分数分布，位于正负区间之间，为初始值而非最优值。知识库或 embedding model 变化后应重新评估。

该 threshold 仅用于 Top-1 检索分数的 query-level relevance gate。query 通过后完整保留 Top-5 候选列表，不逐项过滤。

实现方式：`search()` 中运行时 L2 normalisation，不修改 cache 格式，无需 cache migration。
