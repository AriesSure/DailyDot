"""Pure-behaviour tests for HabitVectorStore.search().

DD-TASK-005B: Uses fixed 2-d vectors to test cosine similarity, Top-K,
k guard, and edge cases without loading a real SentenceTransformer model.
"""

import math
import numpy as np
import pytest
from app.ai.vector_store import HabitVectorStore, _normalize_rows


# ── Fixed 2-d test vectors ────────────────────────────────────────

_SMALL_TEMPLATES = [
    {"name": "a", "frequency": "Every day", "time_period": "Morning",
     "icon": "fas fa-sun", "category": "health", "tags": ["a"]},
    {"name": "b", "frequency": "Every day", "time_period": "Evening",
     "icon": "fas fa-moon", "category": "mindfulness", "tags": ["b"]},
    {"name": "c", "frequency": "Once a week", "time_period": "Noon",
     "icon": "fas fa-star", "category": "fitness", "tags": ["c"]},
]

# Non-unit-length embeddings — search normalises at runtime, so raw dot
# order differs from cosine order.
#   doc A: direction (1,0), norm 3.0  → cosine to query = 1.0
#   doc B: direction (0.7,0.7), norm 10.0 → raw dot = 20, cosine ≈ 0.71
# Raw dot order: B, A, C
# Cosine order:  A, B, C
_SMALL_EMBEDDINGS = np.array([
    [3.0,  0.0],    # doc A — large norm, aligned with [1,0]
    [7.0,  7.0],    # doc B — even larger norm
    [0.0,  1.0],    # doc C
], dtype=np.float64)


class FakeModel:
    """Minimal model stub that returns preset 2-d embeddings."""
    def encode(self, texts, show_progress_bar=False):
        if isinstance(texts, str):
            texts = [texts]
        # Return _SMALL_EMBEDDINGS-scaled query — query aligned with doc A
        return np.array([[1.0, 0.0]] * len(texts), dtype=np.float64)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def store():
    """A ``HabitVectorStore`` instance with fake 2-d data."""
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

    def test_search_uses_cosine_not_raw_dot(self, store):
        """Search returns cosine ordering (A first), not raw dot ordering (B first)."""
        results = store.search("x", k=3)
        names = [r["name"] for r in results]
        assert names[0] == "a", f"Expected 'a' first (cosine 1.0), got {names}"
        # doc B has larger raw dot but smaller cosine → should be second
        assert names.index("b") > names.index("a"), (
            f"Raw-dot ordering would place B before A; got {names}"
        )

    def test_search_scores_in_cosine_range(self, store):
        """Cosine scores should be in [-1, 1]."""
        results = store.search("x", k=3)
        for r in results:
            assert -1.0 <= r["score"] <= 1.0


class TestSearchEdgeCases:
    """Boundary and edge-case behaviours."""

    def test_search_k_larger_than_templates(self, store):
        """``k > len(templates)`` safely returns all available."""
        total = len(store.templates)
        results = store.search("x", k=total + 99)
        assert len(results) == total

    def test_search_k_zero(self, store):
        """``k=0`` returns empty list, encoder NOT called."""
        call_count = 0
        original_encode = store.model.encode
        def tracking_encode(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_encode(*args, **kwargs)
        store.model.encode = tracking_encode
        results = store.search("x", k=0)
        assert results == []
        assert call_count == 0, f"Encoder called {call_count} time(s), expected 0"

    def test_search_k_negative(self, store):
        """``k=-1`` returns empty list, encoder NOT called."""
        call_count = 0
        original_encode = store.model.encode
        def tracking_encode(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_encode(*args, **kwargs)
        store.model.encode = tracking_encode
        results = store.search("x", k=-1)
        assert results == []
        assert call_count == 0, f"Encoder called {call_count} time(s), expected 0"

    def test_search_empty_templates(self):
        """Zero templates → no crash, empty result list."""
        s = object.__new__(HabitVectorStore)
        s.model = FakeModel()
        s.templates = []
        s._embeddings = np.empty((0, 2), dtype=np.float64)
        s._initialized = True
        results = s.search("x", k=5)
        assert results == []

    def test_search_zero_norm_embeddings_raises(self):
        """Zero-norm embeddings → ``_normalize_rows`` raises ``ValueError``."""
        s = object.__new__(HabitVectorStore)
        s.model = FakeModel()
        s.templates = [_SMALL_TEMPLATES[0]]
        s._embeddings = np.array([[0.0, 0.0]], dtype=np.float64)
        s._initialized = True
        with pytest.raises(ValueError, match="zero-norm"):
            s.search("x", k=1)

    def test_search_zero_norm_query_raises(self, store):
        """Zero-norm query → ``_normalize_rows`` raises ``ValueError``."""
        def _zero_encode(texts, show_progress_bar=False):
            return np.array([[0.0, 0.0]], dtype=np.float64)
        store.model.encode = _zero_encode
        with pytest.raises(ValueError, match="zero-norm"):
            store.search("x", k=1)

    def test_search_nan_embedding_raises(self):
        """NaN embeddings → ``_normalize_rows`` raises ``ValueError``."""
        s = object.__new__(HabitVectorStore)
        s.model = FakeModel()
        s.templates = [_SMALL_TEMPLATES[0]]
        s._embeddings = np.array([[float("nan"), 0.0]], dtype=np.float64)
        s._initialized = True
        with pytest.raises(ValueError, match="NaN or Inf"):
            s.search("x", k=1)

    def test_search_equal_scores_does_not_assume_tie_order(self, store):
        """When scores are identical, any order is acceptable."""
        s = object.__new__(HabitVectorStore)
        tie_embs = np.array([[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]], dtype=np.float64)
        s.model = FakeModel()
        s.templates = _SMALL_TEMPLATES
        s._embeddings = tie_embs
        s._initialized = True
        results = s.search("x", k=3)
        assert len(results) == 3
        scores = [r["score"] for r in results]
        assert all(s == pytest.approx(0.7071, abs=1e-3) for s in scores)

    def test_search_dimension_mismatch_raises(self, store):
        """Query with wrong dimension → ``np.dot`` raises ``ValueError``."""
        def _bad_encode(texts, show_progress_bar=False):
            return np.array([[1.0, 0.0, 0.5]], dtype=np.float64)
        store.model.encode = _bad_encode
        with pytest.raises(ValueError, match="dim "):
            store.search("x", k=1)


# ── _normalize_rows unit tests ───────────────────────────────────


class TestNormalizeRows:
    """Direct tests for the module-level ``_normalize_rows`` function."""

    def test_normalize_returns_unit_vectors(self):
        """L2-normalised vectors have unit norm."""
        arr = np.array([[3.0, 4.0]], dtype=float)
        result = _normalize_rows(arr)
        norms = np.linalg.norm(result, axis=1)
        assert np.allclose(norms, 1.0)

    def test_normalize_nan_raises(self):
        """NaN input raises ValueError."""
        with pytest.raises(ValueError, match="NaN or Inf"):
            _normalize_rows(np.array([[float("nan"), 1.0]], dtype=float))

    def test_normalize_zero_norm_raises(self):
        """Zero-norm row raises ValueError."""
        with pytest.raises(ValueError, match="zero-norm"):
            _normalize_rows(np.array([[0.0, 0.0]], dtype=float))

    def test_normalize_inf_raises(self):
        """Inf input raises ValueError."""
        with pytest.raises(ValueError, match="NaN or Inf"):
            _normalize_rows(np.array([[float("inf"), 1.0]], dtype=float))
