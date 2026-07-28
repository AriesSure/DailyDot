# RAG 检索评估基线

生成时间：2026-07-28 00:43:29

Embedding model：paraphrase-multilingual-MiniLM-L12-v2
Templates：38
Knowledge hash：`498762ebbfbc4022227640ed41d8090cbe80bf4037275c4f4443f62a07b6cad8`
评估集：16 cases（relevant=13, unrelated=3）
评分方法：Raw Dot（修改前实现）与 Cosine Similarity（当前实现）
当前生产检索：Cosine Similarity

Threshold 状态：已启用 0.25 作为初始 Top-1 query-level relevance gate。仅当 Top-1 分数低于阈值时拒绝整个查询；通过后不逐项过滤 Top-5 候选列表。

Python：3.12.10
NumPy：2.5.1
执行命令：`python scripts/eval_rag.py`

## Raw Dot Product

- Hit@1：53.8%
- Recall@1：0.410
- Hit@3：76.9%
- Recall@3：0.654
- Hit@5：92.3%
- Recall@5：0.846
- MRR：0.6885
- Pos Top-1 score range：[6.0657, 20.4233]
- Neg Top-1 score range：[2.6393, 5.2906]

## Cosine Similarity

- Hit@1：61.5%
- Recall@1：0.449
- Hit@3：76.9%
- Recall@3：0.654
- Hit@5：92.3%
- Recall@5：0.872
- MRR：0.7308
- Pos Top-1 score range：[0.4072, 0.8554]
- Neg Top-1 score range：[0.1123, 0.1812]

---

**说明**：此报告最初用于比较 Raw Dot 与 Cosine，是项目级回归基线，非通用 benchmark。0.25 是一个基于当前小型评估集的初始阈值，不是最优或通用阈值。知识库或 embedding model 变化后应重新评估。
