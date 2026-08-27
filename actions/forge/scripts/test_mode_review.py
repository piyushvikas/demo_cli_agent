"""Unit tests for mode_review.py's review-text parsing logic.

Run with: pip install -r requirements.txt pytest && pytest test_mode_review.py
(from actions/forge/scripts/) — these are pure-function tests, no network calls.
"""

from __future__ import annotations

import pytest

from mode_review import _extract_recommendation, _has_blocking_tag


# ── _extract_recommendation ─────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        ("**Recommendation**: APPROVE", "APPROVE"),
        ("**Recommendation**: REQUEST_CHANGES", "REQUEST_CHANGES"),
        ("Recommendation: APPROVE", "APPROVE"),
        ("no keywords here at all", "COMMENT"),
        ("this uses AUTO-APPROVE only, not a real recommendation", "COMMENT"),
    ],
)
def test_extract_recommendation_primary_pattern(text, expected):
    assert _extract_recommendation(text) == expected


def test_extract_recommendation_uses_last_occurrence_not_first():
    """Regression test: an earlier mention of REQUEST_CHANGES (e.g. quoting a
    prior review) must not override the model's actual final verdict."""
    text = (
        "Some text mentioning REQUEST_CHANGES from a previous round.\n"
        "Everything is fixed now.\n"
        "Final call: APPROVE"
    )
    assert _extract_recommendation(text) == "APPROVE"


def test_extract_recommendation_reverse_case_still_correct():
    text = "Looked fine initially (APPROVE-ish) but found a bug.\nREQUEST_CHANGES"
    assert _extract_recommendation(text) == "REQUEST_CHANGES"


def test_extract_recommendation_template_echo_still_matches_primary_pattern():
    # If the model echoes the template's pipe-separated options verbatim,
    # the primary "RECOMMENDATION**: APPROVE" pattern still matches first.
    text = "**Recommendation**: APPROVE | REQUEST_CHANGES | COMMENT"
    assert _extract_recommendation(text) == "APPROVE"


# ── _has_blocking_tag ───────────────────────────────────────────────

def test_has_blocking_tag_critical():
    assert _has_blocking_tag("**CRITICAL** `file.py` L10\nSQL injection risk") is True


def test_has_blocking_tag_nit():
    assert _has_blocking_tag("**nit** `file.py` L5\nRename this variable") is True


def test_has_blocking_tag_suggestion_never_blocks():
    assert _has_blocking_tag("**suggestion** `file.py` L1\nConsider adding X") is False


def test_has_blocking_tag_no_issues():
    assert _has_blocking_tag("Everything looks clean. Recommendation: APPROVE") is False


def test_has_blocking_tag_case_insensitive():
    assert _has_blocking_tag("**critical** `file.py` L1\nbug") is True
    assert _has_blocking_tag("**NIT** `file.py` L1\nstyle") is True
