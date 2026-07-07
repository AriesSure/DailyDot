"""Vector store for RAG — uses sentence-transformers + numpy cosine similarity.

For production scale, replace with FAISS or ChromaDB.
"""

import os
import pickle
import numpy as np
from pathlib import Path

from sentence_transformers import SentenceTransformer

from app.ai.knowledge_base import HABIT_TEMPLATES

# Lightweight multilingual model — good for Chinese + English queries
_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "habit_embeddings.pkl"


class HabitVectorStore:
    """In-memory vector store with cosine-similarity search.

    Singleton pattern: the model is loaded once and reused across requests.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.model = SentenceTransformer(_MODEL_NAME)
        self.templates = HABIT_TEMPLATES
        self._embeddings = None
        self._load_or_build()

    def _load_or_build(self):
        """Load pre-computed embeddings from disk, or compute + cache them."""
        if _INDEX_PATH.exists():
            with open(_INDEX_PATH, "rb") as f:
                self._embeddings = pickle.load(f)
            return

        texts = [
            f"{t['name']} {' '.join(t['tags'])} {t['category']}"
            for t in self.templates
        ]
        self._embeddings = self.model.encode(texts, show_progress_bar=False)
        # Cache to disk
        _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_INDEX_PATH, "wb") as f:
            pickle.dump(self._embeddings, f)

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Return top-*k* most similar habit templates for *query*."""
        query_emb = self.model.encode([query], show_progress_bar=False)
        scores = np.dot(self._embeddings, query_emb.T).flatten()
        top_indices = np.argsort(scores)[::-1][:k]

        results = []
        for idx in top_indices:
            t = dict(self.templates[idx])
            t["score"] = float(scores[idx])
            results.append(t)
        return results
