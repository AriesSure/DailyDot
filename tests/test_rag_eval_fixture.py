"""Integrity checks for the RAG evaluation dataset.

DD-TASK-005A: Every case must reference real templates and have consistent
structure.  These tests run in CI and do *not* load a real embedding model.
"""

import pytest
from tests.fixtures.rag_eval_cases import RAG_EVAL_CASES
from app.ai.knowledge_base import HABIT_TEMPLATES

_TEMPLATE_NAMES = {t["name"] for t in HABIT_TEMPLATES}


def test_expected_names_exist_in_templates():
    """Every ``expected_name`` must be a valid template name."""
    all_template_names = {t["name"] for t in HABIT_TEMPLATES}
    missing = []
    for case in RAG_EVAL_CASES:
        for name in case["expected_names"]:
            if name not in all_template_names:
                missing.append((case["id"], name))
    assert not missing, f"expected_names not found in HABIT_TEMPLATES: {missing}"


def test_all_ids_unique():
    """No duplicate case ``id``."""
    ids = [c["id"] for c in RAG_EVAL_CASES]
    assert len(ids) == len(set(ids)), f"Duplicate ids: {ids}"


def test_queries_non_empty():
    """Every case must have a non-empty ``query``."""
    for case in RAG_EVAL_CASES:
        assert case["query"].strip(), f"Case {case['id']}: query is empty"


def test_negative_cases_have_empty_expected():
    """``should_retrieve=False`` cases must have no ``expected_names``."""
    for case in RAG_EVAL_CASES:
        if not case["should_retrieve"]:
            assert case["expected_names"] == [], (
                f"Case {case['id']}: should_retrieve=False but has "
                f"expected_names={case['expected_names']}"
            )


def test_relevant_cases_have_expected():
    """``should_retrieve=True`` cases must have non-empty ``expected_names``."""
    for case in RAG_EVAL_CASES:
        if case["should_retrieve"]:
            assert len(case["expected_names"]) > 0, (
                f"Case {case['id']}: should_retrieve=True but "
                f"expected_names is empty"
            )


def test_no_duplicate_queries():
    """No two cases should have the same query text."""
    queries = [c["query"].strip().lower() for c in RAG_EVAL_CASES]
    assert len(queries) == len(set(queries)), (
        f"Duplicate queries found"
    )


def test_expected_names_have_no_duplicates():
    """Within each case, expected_names should not have duplicates."""
    for case in RAG_EVAL_CASES:
        assert len(case["expected_names"]) == len(set(case["expected_names"])), (
            f"Case {case['id']}: duplicate names in expected_names"
        )


def test_total_case_count():
    """The dataset should contain exactly 16 cases."""
    assert len(RAG_EVAL_CASES) == 16


# ── Metric denominator integrity ─────────────────────────────────


def test_relevant_case_count():
    """Exactly 13 cases with ``should_retrieve=True``.

    Guards against accidental denominator changes in Hit/Recall/MRR
    calculations that divide by the number of relevant cases (``n_pos``).
    """
    n_pos = sum(1 for c in RAG_EVAL_CASES if c["should_retrieve"])
    assert n_pos == 13, f"Expected 13 relevant cases, got {n_pos}"
