# Decision Records


## Decision: Keep NumPy Vector Search

Date:
2026-07

Reason:

Knowledge base contains around 50 short habit templates.

Current scale does not justify FAISS/ChromaDB.

Evaluation quality improvement has higher priority.


## Decision: No Multi-Agent

Reason:

Current business scenario only requires bounded workflow.

Additional agents increase complexity without improving user value.


## Decision: Switch to Cosine Similarity + Initial Relevance Threshold

Date:
2026-07-28

Reason:

DD-TASK-005A evaluation (16 cases, 38 templates) showed:
- Cosine Hit@1: 61.5% vs Raw Dot Hit@1: 53.8%
- Cosine MRR: 0.7308 vs Raw Dot MRR: 0.6885
- Other Top-K metrics were similar or slightly better with cosine

Cosine also provides a clear score gap between relevant queries
(positive Top-1 min ~0.407) and unrelated queries (negative Top-1 max ~0.181),
making threshold-based filtering feasible without complex reranking.

Initial threshold 0.25 is derived from the current eval dataset distribution.
It sits between the positive and negative score ranges with comfortable margin.
This is not claimed as optimal or universal; re-evaluate when the knowledge
base or embedding model changes.

The threshold is applied only to the Top-1 retrieval score as a query-level
relevance gate. If the query passes, the complete Top-5 candidate list is
preserved for the LLM or vector fallback; no candidate-level filtering is
performed.
Implementation: runtime L2 normalisation in search() — no cache migration.
