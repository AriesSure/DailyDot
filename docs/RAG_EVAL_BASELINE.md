# RAG Evaluation Baseline

Generated: 2026-07-28 00:43:29

Embedding model: paraphrase-multilingual-MiniLM-L12-v2

Templates: 38

Knowledge hash: `498762ebbfbc4022227640ed41d8090cbe80bf4037275c4f4443f62a07b6cad8`

Eval cases: 16 (relevant=13, unrelated=3)

Scoring methods: raw dot product (current production) vs cosine similarity

Threshold: not enabled (DD-TASK-005B)

Python: 3.12.10

NumPy: 2.5.1

Command: `python scripts/eval_rag.py`


## Raw dot product

- Hit@1: 53.8%
- Recall@1: 0.410
- Hit@3: 76.9%
- Recall@3: 0.654
- Hit@5: 92.3%
- Recall@5: 0.846
- MRR: 0.6885
- Pos Top-1 score range: [6.0657, 20.4233]
- Neg Top-1 score range: [2.6393, 5.2906]

## Cosine similarity

- Hit@1: 61.5%
- Recall@1: 0.449
- Hit@3: 76.9%
- Recall@3: 0.654
- Hit@5: 92.3%
- Recall@5: 0.872
- MRR: 0.7308
- Pos Top-1 score range: [0.4072, 0.8554]
- Neg Top-1 score range: [0.1123, 0.1812]

Note: No production code was modified. Threshold remains disabled pending DD-TASK-005B.