"""Pure-behaviour tests for HabitVectorStore.search().

DD-TASK-005A: Uses fixed 2-d vectors to test similarity, Top-K, and edge
cases without loading a real SentenceTransformer model.
"""

import math
import numpy as np
import pytest
from app.ai.vector_store import HabitVectorStore


# ── Fixed 2-d test vectors ────────────────────────────────────────
# Dot products are fully deterministic given these values.

_SMALL_TEMPLATES = [
    {"name": "a", "frequency": "Every day", "time_period": "Morning",
     "icon": "fas fa-sun", "category": "health", "tags": ["a"]},
    {"name": "b", "frequency": "Every day", "time_period": "Evening",
     "icon": "fas fa-moon", "category": "mindfulness", "tags": ["b"]},
    {"name": "c", "frequency": "Once a week", "time_period": "Noon",
     "icon": "fas fa-star", "category": "fitness", "tags": ["c"]},
]

# Embeddings arranged so that:
#   query_good → scores ≈ [1.0, 0.8, 0.0] (a ≈ cosine, b ≈ 0.8, c ≈ 0)
#   query_zero → scores = [0.0, 0.0, 0.0]
_SMALL_EMBEDDINGS = np.array([
    [1.0, 0.0],  # template a
    [0.8, 0.2],  # template b
    [0.0, 1.0],  # template c
], dtype=np.float64)

_QUERY_GOOD = np.array([[1.0, 0.0]], dtype=np.float64)      # scores: 1.0, 0.8, 0.0
_QUERY_ZERO = np.array([[0.0, 0.0]], dtype=np.float64)       # scores: 0.0, 0.0, 0.0
_QUERY_UNREL = np.array([[-0.9, -0.8]], dtype=np.float64)    # scores: -0.9, -0.88, -0.8


class FakeModel:
    """Minimal model stub that returns preset 2-d embeddings."""
    def encode(self, texts, show_progress_bar=False):
        if isinstance(texts, str):
            texts = [texts]
        return np.array([
            [1.0, 0.0],     # matches _QUERY_GOOD
            [0.0, 0.0],
        ][:len(texts)] * len(texts), dtype=np.float64)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def store():
    """A ``HabitVectorStore`` instance with fake 2-d data.

    Bypasses the real singleton by using ``object.__new__()`` and sets
    internal attributes directly so that no SentenceTransformer, pickle
    cache, or Hugging Face resource is accessed.
    """
    s = object.__new__(HabitVectorStore)
    s.model = FakeModel()
    s.templates = _SMALL_TEMPLATES
    s._embeddings = _SMALL_EMBEDDINGS
    s._initialized = True
    return s


# ── Tests ─────────────────────────────────────────────────────────


class TestSearchBasic:
    """Core search behaviour with deterministic 2-d vectors."""

    def test_search_returns_top_k(self, store):
        """``k=2`` returns exactly 2 results."""
        results = store.search("x", k=2)
        assert len(results) == 2

    def test_search_sorted_descending(self, store):
        """Results are ordered by score descending."""
        results = store.search("x", k=3)
        for i in range(len(results) - 1):
            assert results[i]["score"] >= results[i + 1]["score"]

    def test_search_scores_are_finite_float(self, store):
        """Each result has a finite float ``score``."""
        results = store.search("x", k=3)
        for r in results:
            assert isinstance(r["score"], float)
            assert math.isfinite(r["score"])

    def test_search_includes_template_fields(self, store):
        """Result dicts contain all expected template fields."""
        results = store.search("x", k=1)
        r = results[0]
        assert "name" in r
        assert "frequency" in r
        assert "time_period" in r
        assert "icon" in r
        assert "category" in r
        assert "tags" in r
        assert "score" in r


class TestSearchEdgeCases:
    """Boundary and edge-case behaviours (characterization)."""

    def test_search_k_larger_than_templates(self, store):
        """``k > len(templates)`` safely returns all available."""
        total = len(store.templates)
        results = store.search("x", k=total + 99)
        assert len(results) == total

    def test_search_k_zero(self, store):
        """``k=0`` returns an empty list (current behaviour)."""
        results = store.search("x", k=0)
        assert results == []

    def test_search_k_negative(self, store):
        """``k=-1`` — current numpy behaviour: returns 1 result from tailored slice.

        ``np.argsort(scores)[::-1][:k]`` with ``k=-1`` acts as
        ``scores[:-1]``, dropping the last element.  This is unlikely to be
        the intended behaviour; DD-TASK-005B should add an explicit guard.
        """
        results = store.search("x", k=-1)
        assert len(results) == len(store.templates) - 1

    def test_search_empty_templates(self):
        """Zero templates → no crash, empty result list."""
        s = object.__new__(HabitVectorStore)
        s.model = FakeModel()
        s.templates = []
        s._embeddings = np.empty((0, 2), dtype=np.float64)
        s._initialized = True
        results = s.search("x", k=5)
        assert results == []

    def test_search_zero_query_vector(self, store):
        """Zero query vector → all scores are 0.0."""
        s = object.__new__(HabitVectorStore)
        s.model = FakeModel()
        s.templates = _SMALL_TEMPLATES
        s._embeddings = _SMALL_EMBEDDINGS
        s._initialized = True
        # Override encode for this test
        def _fake_encode(texts, show_progress_bar=False):
            return _QUERY_ZERO
        s.model.encode = _fake_encode
        results = s.search("x", k=3)
        for r in results:
            assert r["score"] == pytest.approx(0.0)

    def test_search_equal_scores_does_not_assume_tie_order(self, store):
        """When scores are identical, any order is acceptable.

        ``np.argsort`` does not guarantee stable tie-breaking without
        ``kind='stable'``.  This test only asserts count and score
        equality, not a specific ranking.
        """
        s = object.__new__(HabitVectorStore)
        # All embeddings are the same => all scores equal
        tie_embs = np.array([[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]], dtype=np.float64)
        s.model = FakeModel()
        s.templates = _SMALL_TEMPLATES
        s._embeddings = tie_embs
        s._initialized = True
        results = s.search("x", k=3)
        assert len(results) == 3
        scores = [r["score"] for r in results]
        assert all(s == pytest.approx(0.5) for s in scores)

    def test_search_nan_embedding_current_behavior(self):
        """NaN embeddings propagate through dot product → NaN score."""
        s = object.__new__(HabitVectorStore)
        nan_embs = np.array([[float("nan"), 0.0]], dtype=np.float64)
        s.model = FakeModel()
        s.templates = [_SMALL_TEMPLATES[0]]
        s._embeddings = nan_embs
        s._initialized = True
        results = s.search("x", k=1)
        assert math.isnan(results[0]["score"])

    def test_search_dimension_mismatch_current_behavior(self):
        """Query with wrong dimension → ``np.dot`` raises ``ValueError``."""
        s = object.__new__(HabitVectorStore)
        s.model = FakeModel()
        s.templates = _SMALL_TEMPLATES
        s._embeddings = _SMALL_EMBEDDINGS
        s._initialized = True
        # Override encode to return wrong-shape query
        def _bad_encode(texts, show_progress_bar=False):
            return np.array([[1.0, 0.0, 0.5]], dtype=np.float64)  # 3-d vs 2-d
        s.model.encode = _bad_encode
        with pytest.raises(ValueError, match="dim "):
            s.search("x", k=1)
