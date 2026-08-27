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


def test_approve_blocked_falls_back_to_comment_review():
    pr = MagicMock()
    pr.create_review.side_effect = [_not_permitted_error(), None]
    client = _make_client(pr)

    client.post_pr_review(pr_number=1, body="Looks good", event="APPROVE")

    assert pr.create_review.call_count == 2
    first_call, second_call = pr.create_review.call_args_list
    assert first_call.kwargs["event"] == "APPROVE"
    assert second_call.kwargs["event"] == "COMMENT"
    assert second_call.kwargs["body"] == "Looks good"
    # Must NOT fall back to a plain issue comment — that's the whole point.
    pr.create_issue_comment.assert_not_called()


def test_approve_blocked_and_comment_review_also_fails_falls_back_to_plain_comment():
    pr = MagicMock()
    pr.create_review.side_effect = [_not_permitted_error(), _not_permitted_error()]
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
    client = _make_client(pr)

    inline = [{"path": "app/main.py", "line": 10, "body": "nice"}]
    client.post_pr_review(pr_number=1, body="Looks good", event="APPROVE", comments=inline)

    first_call, second_call = pr.create_review.call_args_list
    assert first_call.kwargs["comments"] == [{"path": "app/main.py", "line": 10, "body": "nice"}]
    assert second_call.kwargs["event"] == "COMMENT"
    assert second_call.kwargs["comments"] == [{"path": "app/main.py", "line": 10, "body": "nice"}]
