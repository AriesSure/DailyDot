#!/usr/bin/env python
"""Local RAG evaluation — runs with the real SentenceTransformer model.

DD-TASK-005A: This script:
  - Loads the real embedding model (NOT run in CI)
  - Re-encodes all knowledge-base templates in memory
  - Encodes each eval query in memory
  - Reports raw-dot-product and cosine metrics
  - Writes ``docs/RAG_EVAL_BASELINE.md``

Usage:
    python scripts/eval_rag.py
"""

import hashlib
import json
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sentence_transformers import SentenceTransformer
from app.ai.knowledge_base import HABIT_TEMPLATES
from tests.fixtures.rag_eval_cases import RAG_EVAL_CASES


# ── Knowledge hash ────────────────────────────────────────────────


def compute_knowledge_hash(templates):
    """Full SHA-256 hexdigest of the template list."""
    payload = json.dumps(templates, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Embedding helpers ─────────────────────────────────────────────


def build_template_texts(templates):
    return [f"{t['name']} {' '.join(t['tags'])} {t['category']}" for t in templates]


def safe_normalize(embeddings):
    """L2-normalise; zero-norm rows remain zero.

    Raises ``ValueError`` if NaN or Inf values are present.
    """
    if not np.all(np.isfinite(embeddings)):
        raise ValueError("Embeddings contain NaN or Inf values")
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)  # avoid division by zero
    result = embeddings / norms
    if not np.all(np.isfinite(result)):
        raise ValueError("Normalised embeddings contain NaN or Inf values")
    return result


# ── Metrics (per-case) ────────────────────────────────────────────


def hit_at_k(top_names, expected_names, k):
    return 1 if any(n in top_names[:k] for n in expected_names) else 0


def recall_at_k(top_names, expected_names, k):
    if not expected_names:
        return 0.0
    hits = sum(1 for n in expected_names if n in top_names[:k])
    return hits / len(expected_names)


def mrr(top_names, expected_names):
    for rank, name in enumerate(top_names, start=1):
        if name in expected_names:
            return 1.0 / rank
    return 0.0


# ── Aggregation (positive-only denominator) ───────────────────────


def aggregate_positive_metrics(case_results, ks=(1, 3, 5)):
    """Aggregate Hit@K, macro-Recall@K and MRR over relevant cases only.

    *case_results* is a list of dicts with keys:
      - ``should_retrieve`` (bool)
      - ``expected_names`` (list of str)
      - ``top_names_dot`` (list of str, top-5 raw-dot results)
      - ``top_names_cos`` (list of str, top-5 cosine results)

    Returns a nested dict with ``"raw"`` and ``"cosine"`` sub-dicts::

        {
            "raw":    {"hit_at_1": ..., "recall_at_1": ..., "mrr": ...},
            "cosine": {"hit_at_1": ..., "recall_at_1": ..., "mrr": ...},
        }

    All values are floats (fractions of the positive-case count).
    """
    positive = [r for r in case_results if r["should_retrieve"]]
    if not positive:
        raise ValueError("No positive cases to aggregate")
    n_pos = len(positive)

    def _agg(top_key):
        mrr_total = 0.0
        hits = {k: 0 for k in ks}
        recs = {k: 0.0 for k in ks}
        for r in positive:
            exp = r["expected_names"]
            top = r[top_key]
            for k in ks:
                hits[k] += hit_at_k(top, exp, k)
                recs[k] += recall_at_k(top, exp, k)
            mrr_total += mrr(top, exp)
        return {
            **{f"hit_at_{k}": hits[k] / n_pos for k in ks},
            **{f"recall_at_{k}": recs[k] / n_pos for k in ks},
            "mrr": mrr_total / n_pos,
        }

    return {
        "raw":    _agg("top_names_dot"),
        "cosine": _agg("top_names_cos"),
    }


# ── Main ──────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("RAG Evaluation — DD-TASK-005A")
    print("=" * 60)

    model_name = "paraphrase-multilingual-MiniLM-L12-v2"
    print(f"\nLoading model: {model_name} ...")
    model = SentenceTransformer(model_name)

    kh = compute_knowledge_hash(HABIT_TEMPLATES)
    n_templates = len(HABIT_TEMPLATES)
    print(f"Templates: {n_templates}, knowledge_hash: {kh}")

    # Encode documents once — raw; cosine derived by safe_normalize
    texts = build_template_texts(HABIT_TEMPLATES)
    print(f"Encoding {n_templates} templates ...")
    doc_embs = model.encode(texts, show_progress_bar=False, normalize_embeddings=False)
    doc_embs_cos = safe_normalize(doc_embs)

    norms = np.linalg.norm(doc_embs, axis=1)
    print(f"  Document norm: mean={norms.mean():.4f}, min={norms.min():.4f}, max={norms.max():.4f}")

    # Build per-case results
    case_results = []
    score_data = {}  # for sep. pos/neg score tracking

    for case in RAG_EVAL_CASES:
        query = case["query"]
        expected = case["expected_names"]
        is_positive = case["should_retrieve"]

        q_raw = model.encode([query], show_progress_bar=False, normalize_embeddings=False)
        q_cos = safe_normalize(q_raw)

        # Raw dot
        scores_dot = np.dot(doc_embs, q_raw.T).flatten()
        if not np.all(np.isfinite(scores_dot)):
            raise ValueError("Raw dot scores contain NaN or Inf values")
        top_idx_dot = np.argsort(scores_dot)[::-1][:5]
        top_names_dot = [HABIT_TEMPLATES[i]["name"] for i in top_idx_dot]

        # Cosine (from same raw embeddings)
        scores_cos = np.dot(doc_embs_cos, q_cos.T).flatten()
        if not np.all(np.isfinite(scores_cos)):
            raise ValueError("Cosine scores contain NaN or Inf values")
        top_idx_cos = np.argsort(scores_cos)[::-1][:5]
        top_names_cos = [HABIT_TEMPLATES[i]["name"] for i in top_idx_cos]

        # Record the full result
        case_results.append({
            "should_retrieve": is_positive,
            "expected_names": expected,
            "top_names_dot": top_names_dot,
            "top_names_cos": top_names_cos,
        })

        # Track score ranges
        if is_positive:
            score_data.setdefault("pos_dot", []).append(float(scores_dot[top_idx_dot[0]]))
            score_data.setdefault("pos_cos", []).append(float(scores_cos[top_idx_cos[0]]))
        else:
            score_data.setdefault("neg_dot", []).append(float(scores_dot[top_idx_dot[0]]))
            score_data.setdefault("neg_cos", []).append(float(scores_cos[top_idx_cos[0]]))

        tag = "P" if is_positive else "N"
        print(f"[{tag}] {query:<34} → dot:{top_names_dot[0] or '-'}")

    # Aggregate using the shared function
    metrics = aggregate_positive_metrics(case_results, ks=(1, 3, 5))

    n = len(RAG_EVAL_CASES)
    n_pos = sum(1 for c in RAG_EVAL_CASES if c["should_retrieve"])

    print("\n" + "=" * 60)
    print("AGGREGATE RESULTS")
    print("=" * 60)
    print(f"Embedding model:   {model_name}")
    print(f"Templates:         {n_templates}")
    print(f"Eval cases (total):{n}  (relevant={n_pos}, unrelated={n-n_pos})")
    print(f"Knowledge hash:    {kh}")
    print(f"Threshold:         not enabled (DD-TASK-005B)")
    print(f"Python:            {sys.version.split()[0]}")
    print(f"NumPy:             {np.__version__}")
    print()

    _M = {"dot": "raw", "cos": "cosine"}
    for label, method in [("Raw dot product", "dot"), ("Cosine similarity", "cos")]:
        sub = metrics[_M[method]]
        print(f"--- {label} ---")
        for k in (1, 3, 5):
            print(f"  Hit@{k}:    {sub[f'hit_at_{k}']:.1%}  ({sub[f'hit_at_{k}']*n_pos:.0f}/{n_pos})")
            print(f"  Recall@{k}: {sub[f'recall_at_{k}']:.3f}")
        print(f"  MRR:       {sub['mrr']:.4f}")
        pos_scores = score_data.get(f"pos_{method}", [])
        neg_scores = score_data.get(f"neg_{method}", [])
        if pos_scores:
            print(f"  Pos Top-1: min={min(pos_scores):.4f}, max={max(pos_scores):.4f}")
        if neg_scores:
            print(f"  Neg Top-1: min={min(neg_scores):.4f}, max={max(neg_scores):.4f}")
        print()

    # Write baseline report
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# RAG Evaluation Baseline\n",
        f"Generated: {timestamp}  \n",
        f"Embedding model: {model_name}  \n",
        f"Templates: {n_templates}  \n",
        f"Knowledge hash: `{kh}`  \n",
        f"Eval cases: {n} (relevant={n_pos}, unrelated={n-n_pos})  \n",
        f"Scoring methods: raw dot product (current production) vs cosine similarity  \n",
        f"Threshold: not enabled (DD-TASK-005B)  \n",
        f"Python: {sys.version.split()[0]}  \n",
        f"NumPy: {np.__version__}  \n",
        f"Command: `python scripts/eval_rag.py`  \n",
        "",
    ]

    for label, method in [("Raw dot product", "dot"), ("Cosine similarity", "cos")]:
        sub = metrics[_M[method]]
        lines.append(f"## {label}")
        lines.append("")
        for k in (1, 3, 5):
            lines.append(f"- Hit@{k}: {sub[f'hit_at_{k}']:.1%}")
            lines.append(f"- Recall@{k}: {sub[f'recall_at_{k}']:.3f}")
        lines.append(f"- MRR: {sub['mrr']:.4f}")
        pos_scores = score_data.get(f"pos_{method}", [])
        neg_scores = score_data.get(f"neg_{method}", [])
        if pos_scores:
            lines.append(f"- Pos Top-1 score range: [{min(pos_scores):.4f}, {max(pos_scores):.4f}]")
        if neg_scores:
            lines.append(f"- Neg Top-1 score range: [{min(neg_scores):.4f}, {max(neg_scores):.4f}]")
        lines.append("")

    lines.append("Note: No production code was modified. Threshold remains disabled pending DD-TASK-005B.")
    lines.append("\n")

    report = "\n".join(lines)
    report_path = os.path.join(os.path.dirname(__file__), "..", "docs", "RAG_EVAL_BASELINE.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Baseline written to: {report_path}")
    print("Done.")


if __name__ == "__main__":
    main()
