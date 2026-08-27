"""Unit tests for GitHubClient.post_pr_review's APPROVE fallback logic.

Run with: pip install -r requirements.txt pytest && pytest test_github_client.py
(from actions/forge/scripts/) — these mock the GitHub API, no network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from github import GithubException

from github_client import GitHubClient


def _make_client(pr: MagicMock) -> GitHubClient:
    """Build a GitHubClient without hitting the network — __init__ calls the
    real GitHub API, so bypass it and wire up just what post_pr_review needs.
    """
    client = GitHubClient.__new__(GitHubClient)
    client.repo = MagicMock()
    client.repo.get_pull.return_value = pr
    return client


def _not_permitted_error() -> GithubException:
    return GithubException(
        422,
        {"message": "Unprocessable Entity", "errors": ["GitHub Actions is not permitted to approve pull requests."]},
        None,
    )


def test_approve_success_no_fallback():
    pr = MagicMock()
    client = _make_client(pr)

    client.post_pr_review(pr_number=1, body="Looks good", event="APPROVE")

    pr.create_review.assert_called_once_with(body="Looks good", event="APPROVE")
    pr.create_issue_comment.assert_not_called()


def _review(state: str, login: str, review_id: int = 1) -> MagicMock:
    review = MagicMock()
    review.state = state
    review.user.login = login
    review.id = review_id
    return review


def test_approve_blocked_falls_back_to_comment_review():
    pr = MagicMock()
    pr.create_review.side_effect = [_not_permitted_error(), None]
    pr.get_reviews.return_value = []
    client = _make_client(pr)

    client.post_pr_review(pr_number=1, body="Looks good", event="APPROVE")

    assert pr.create_review.call_count == 2
    first_call, second_call = pr.create_review.call_args_list
    assert first_call.kwargs["event"] == "APPROVE"
    assert second_call.kwargs["event"] == "COMMENT"
    assert second_call.kwargs["body"] == "Looks good"
    # Must NOT fall back to a plain issue comment — that's the whole point.
    pr.create_issue_comment.assert_not_called()


def test_approve_blocked_dismisses_own_prior_change_request():
    pr = MagicMock()
    pr.create_review.side_effect = [_not_permitted_error(), None]
    stale = _review("CHANGES_REQUESTED", "github-actions[bot]", review_id=42)
    other_bot = _review("CHANGES_REQUESTED", "some-other-bot", review_id=43)
    already_commented = _review("COMMENTED", "github-actions[bot]", review_id=44)
    pr.get_reviews.return_value = [stale, other_bot, already_commented]
    client = _make_client(pr)

    client.post_pr_review(pr_number=1, body="Looks good", event="APPROVE")

    stale.dismiss.assert_called_once()
    other_bot.dismiss.assert_not_called()  # not ours — must not touch it
    already_commented.dismiss.assert_not_called()  # not CHANGES_REQUESTED


def test_dismiss_failure_does_not_block_posting_the_comment_review():
    """Dismissing may fail (e.g. requires admin on a protected branch) —
    that must not prevent the COMMENT review from still being posted."""
    pr = MagicMock()
    pr.create_review.side_effect = [_not_permitted_error(), None]
    stale = _review("CHANGES_REQUESTED", "github-actions[bot]", review_id=42)
    stale.dismiss.side_effect = GithubException(403, {"message": "Must be an administrator"}, None)
    pr.get_reviews.return_value = [stale]
    client = _make_client(pr)

    client.post_pr_review(pr_number=1, body="Looks good", event="APPROVE")

    stale.dismiss.assert_called_once()
    assert pr.create_review.call_count == 2  # still posted the COMMENT review


def test_approve_blocked_and_comment_review_also_fails_falls_back_to_plain_comment():
    pr = MagicMock()
    pr.create_review.side_effect = [_not_permitted_error(), _not_permitted_error()]
    pr.get_reviews.return_value = []
    client = _make_client(pr)

    client.post_pr_review(pr_number=1, body="Looks good", event="APPROVE")

    assert pr.create_review.call_count == 2
    pr.create_issue_comment.assert_called_once_with("Looks good")


def test_request_changes_failure_falls_back_to_plain_comment_directly():
    """REQUEST_CHANGES isn't blocked by the APPROVE-specific policy, so a
    failure there should go straight to the plain-comment fallback, not
    retry as COMMENT (that retry path is APPROVE-specific)."""
    pr = MagicMock()
    pr.create_review.side_effect = GithubException(500, {"message": "Server Error"}, None)
    client = _make_client(pr)

    client.post_pr_review(pr_number=1, body="Needs work", event="REQUEST_CHANGES")

    assert pr.create_review.call_count == 1
    pr.create_issue_comment.assert_called_once_with("Needs work")


def test_approve_with_inline_comments_retries_with_comments_too():
    pr = MagicMock()
    pr.create_review.side_effect = [_not_permitted_error(), None]
    pr.get_reviews.return_value = []
    client = _make_client(pr)

    inline = [{"path": "app/main.py", "line": 10, "body": "nice"}]
    client.post_pr_review(pr_number=1, body="Looks good", event="APPROVE", comments=inline)

    first_call, second_call = pr.create_review.call_args_list
    assert first_call.kwargs["comments"] == [{"path": "app/main.py", "line": 10, "body": "nice"}]
    assert second_call.kwargs["event"] == "COMMENT"
    assert second_call.kwargs["comments"] == [{"path": "app/main.py", "line": 10, "body": "nice"}]
