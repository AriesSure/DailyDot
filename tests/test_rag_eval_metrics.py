"""Metric aggregation tests — uses the real aggregate_positive_metrics() function.

DD-TASK-005A: These tests verify that the aggregation calculates Hit@K,
macro-Recall@K and MRR using only relevant cases as the denominator, and
that raw and cosine metrics are stored in separate namespaces.
"""

from scripts.eval_rag import aggregate_positive_metrics


def _result(is_positive, expected_names, top_names_dot, top_names_cos=None):
    """Build a per-case result dict consumed by ``aggregate_positive_metrics``."""
    top_names_cos = top_names_cos or top_names_dot
    return {
        "should_retrieve": is_positive,
        "expected_names": expected_names,
        "top_names_dot": top_names_dot,
        "top_names_cos": top_names_cos,
    }


class TestNamespaceSeparation:
    """Raw and cosine metrics must not overwrite each other.

    This test uses cases where raw and cosine produce *different* ranks
    to prove they occupy separate namespaces.
    """

    def test_raw_and_cosine_differ(self):
        """2 relevant cases — raw Hit@1=0.5, cosine Hit@1=1.0."""
        # Raw:  only case A hits at rank 1
        # Cosine: both A and B hit at rank 1
        results = [
            _result(True, ["A"], top_names_dot=["A", "X"], top_names_cos=["A", "B"]),
            _result(True, ["B"], top_names_dot=["A", "B"], top_names_cos=["B", "A"]),
        ]
        m = aggregate_positive_metrics(results, ks=(1, 3))
        assert m["raw"]["hit_at_1"] == 0.5
        assert m["cosine"]["hit_at_1"] == 1.0

    def test_raw_and_cosine_mrr_differ(self):
        """raw and cosine MRR must be computed independently — different values prove no namespace overlap.

        Case 1 expected=[A]: raw rank=2 (0.5), cosine rank=1 (1.0)
        Case 2 expected=[B]: raw rank=2 (0.5), cosine rank=2 (0.5)

        Raw MRR   = (0.5 + 0.5) / 2 = 0.5
        Cosine MRR = (1.0 + 0.5) / 2 = 0.75
        """
        results = [
            _result(True, ["A"], top_names_dot=["X", "A"], top_names_cos=["A"]),
            _result(True, ["B"], top_names_dot=["X", "B"], top_names_cos=["X", "B"]),
        ]
        m = aggregate_positive_metrics(results, ks=(1, 3))
        assert m["raw"]["mrr"] == 0.5
        assert m["cosine"]["mrr"] == 0.75


class TestHitDenominator:
    """Hit@K must divide by relevant case count, not total count."""

    def test_relevant_only_denominator(self):
        """2 relevant + 1 negative, only 1 relevant hits @1 → Hit@1 = 0.5."""
        results = [
            _result(True,  ["跑步"],                ["跑步", "早睡"]),
            _result(True,  ["阅读 30 分钟", "写日记"], ["早睡", "阅读 30 分钟"]),
            _result(False, [],                     ["无关结果"]),
        ]
        m = aggregate_positive_metrics(results, ks=(1, 3, 5))
        assert m["raw"]["hit_at_1"] == 0.5
        assert m["cosine"]["hit_at_1"] == 0.5


class TestMRRDenominator:
    """MRR must divide by relevant case count, not total count."""

    def test_mrr_relevant_only(self):
        """2 relevant (rank-1, rank-2) + 1 neg → MRR = (1.0+0.5)/2 = 0.75."""
        results = [
            _result(True,  ["跑步"],                ["跑步", "早睡"]),       # 1.0
            _result(True,  ["阅读 30 分钟", "写日记"], ["早睡", "阅读 30 分钟"]),  # 0.5
            _result(False, [],                     ["无关结果"]),
        ]
        m = aggregate_positive_metrics(results, ks=(1, 3, 5))
        assert m["raw"]["mrr"] == 0.75
        assert m["cosine"]["mrr"] == 0.75


class TestRecall:
    """Macro-Recall averages per-case recall over relevant cases."""

    def test_macro_recall_relevant_only(self):
        """2 relevant: case A recall=0.5, case B recall=1.0 → macro avg = 0.75."""
        results = [
            _result(True, ["跑步", "写日记"], ["跑步", "早睡"]),  # 1/2 = 0.5
            _result(True, ["跑步", "早睡"],    ["跑步", "早睡"]),  # 2/2 = 1.0
        ]
        m = aggregate_positive_metrics(results, ks=(5,))
        assert m["raw"]["recall_at_5"] == 0.75
        assert m["cosine"]["recall_at_5"] == 0.75
