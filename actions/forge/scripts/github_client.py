"""
GitHub Client — typed wrapper over PyGithub for Forge operations.

Handles:
  - PR operations: get details, diff, files, post comments, approve/request changes
  - Issue operations: get details, comments, post updates
  - Branch operations: create branches, commit, push
  - PR creation for issue implementation
"""

from __future__ import annotations

import os
import re
from typing import Any

from github import Github, GithubException, InputGitAuthor
from github.PullRequest import PullRequest
from github.Issue import Issue


class GitHubClient:
    """GitHub API client for Forge.

    Supports both github.com and GitHub Enterprise Server (GHES).
    Auto-detects the API URL from the ``GITHUB_API_URL`` env var
    (set automatically by all GitHub Actions runners).

    The same PyGithub instance can access any repo the token has
    permissions for — enabling cross-repo context when needed.
    """

    def __init__(self, token: str, repo_full_name: str) -> None:
        # GitHub Actions sets GITHUB_API_URL for GHES; fall back to public API
        self.api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")

        # ── Auth diagnostics (secrets are masked by Actions) ──────────
        _is_ghes = self.api_url != "https://api.github.com"
        print(f"  GitHub API  : {self.api_url} ({'GHES' if _is_ghes else 'github.com'})")
        print(f"  Token       : {'set' if token else 'EMPTY!'} "
              f"(length={len(token)}, prefix={token[:4]}…)" if token else "")
        print(f"  Repository  : {repo_full_name}")

        if _is_ghes:
            self.gh = Github(token, base_url=self.api_url, per_page=100)
        else:
            self.gh = Github(token, per_page=100)

        try:
            self.repo = self.gh.get_repo(repo_full_name)
        except GithubException as exc:
            # Surface a clear diagnostic instead of a raw traceback
            print(f"\n❌ GitHub authentication failed ({exc.status}):")
            print(f"   API URL : {self.api_url}")
            print(f"   Repo    : {repo_full_name}")
            print(f"   Token   : length={len(token)}, prefix={token[:4]}…")
            if exc.status == 401:
                print("\n   Common causes:")
                print("   1. PAT was created on github.com but runner uses GitHub Enterprise")
                print(f"      → Create the PAT on {self.api_url.replace('/api/v3', '')}")
                print("   2. PAT has expired — regenerate it in Settings → Developer settings → PAT")
                print("   3. PAT is missing the 'repo' scope (classic) or repository permission (fine-grained)")
                print("   4. Secret value has leading/trailing whitespace — re-paste it cleanly")
            raise

        self.repo_name = repo_full_name
        self.token = token

    # ── Cross-repo operations (read-only) ────────────────────────────

    def get_repo_file(
        self, repo_full_name: str, path: str, ref: str = ""
    ) -> str:
        """Read a file from any repo the token has access to.

        Args:
            repo_full_name: owner/repo (e.g. 'ccfc/some-other-repo')
            path: file path within the repo
            ref: branch/tag/sha (default: repo default branch)

        Returns:
            File contents as string (decoded UTF-8).
        """
        repo = self.gh.get_repo(repo_full_name)
        kwargs: dict[str, Any] = {}
        if ref:
            kwargs["ref"] = ref
        content = repo.get_contents(path, **kwargs)
        if isinstance(content, list):
            return f"Error: '{path}' is a directory, not a file"
        return content.decoded_content.decode("utf-8")

    def list_repo_dir(
        self, repo_full_name: str, path: str = "", ref: str = ""
    ) -> list[str]:
        """List files/dirs in a path of any repo the token has access to."""
        repo = self.gh.get_repo(repo_full_name)
        kwargs: dict[str, Any] = {}
        if ref:
            kwargs["ref"] = ref
        contents = repo.get_contents(path, **kwargs)
        if not isinstance(contents, list):
            contents = [contents]
        return [
            f"{c.name}/" if c.type == "dir" else c.name
            for c in contents
        ]

    def search_repo_code(
        self, repo_full_name: str, query: str
    ) -> list[dict[str, str]]:
        """Search code in another repo using GitHub code search.

        Returns list of {path, fragment} dicts (max 10 results).
        """
        results = self.gh.search_code(f"{query} repo:{repo_full_name}")
        hits: list[dict[str, str]] = []
        for item in results[:10]:
            try:
                snippet = item.decoded_content.decode("utf-8")[:500]
            except Exception:
                snippet = f"(binary or too large — {item.path})"
            hits.append({"path": item.path, "fragment": snippet})
        return hits

    # ── Pull Request operations ──────────────────────────────────────

    def get_pr(self, number: int) -> PullRequest:
        return self.repo.get_pull(number)

    def get_pr_diff(self, number: int) -> str:
        """Get the unified diff for a PR."""
        import httpx
        url = f"{self.api_url}/repos/{self.repo_name}/pulls/{number}"
        resp = httpx.get(
            url,
            headers={
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.diff",
            },
            timeout=30,
        )
        resp.raise_for_status()
        diff = resp.text
        # Truncate enormous diffs
        if len(diff) > 50000:
            diff = diff[:25000] + "\n\n... [diff truncated — use tools to explore specific files] ...\n\n" + diff[-25000:]
        return diff

    def get_pr_files(self, number: int):
        """Get list of changed files in a PR."""
        pr = self.repo.get_pull(number)
        return list(pr.get_files())

    def get_pr_comments(self, number: int) -> list[dict[str, Any]]:
        """Get all review comments and issue comments on a PR.

        Returns a chronological list of comments so the agent can see
        prior feedback and know what has already been addressed.
        """
        pr = self.repo.get_pull(number)
        comments: list[dict[str, Any]] = []

        # Issue-level comments (general discussion)
        for c in pr.get_issue_comments():
            comments.append({
                "type": "comment",
                "author": c.user.login,
                "body": c.body[:2000],
                "created_at": c.created_at.isoformat(),
            })

        # Review-level comments (reviews with verdicts)
        for r in pr.get_reviews():
            if r.body:
                comments.append({
                    "type": "review",
                    "author": r.user.login,
                    "state": r.state,  # APPROVED, CHANGES_REQUESTED, COMMENTED
                    "body": r.body[:3000],
                    "created_at": r.submitted_at.isoformat() if r.submitted_at else "",
                })

        # Sort chronologically
        comments.sort(key=lambda c: c.get("created_at", ""))
        return comments

    def post_pr_review(
        self,
        pr_number: int,
        body: str,
        event: str = "COMMENT",
        comments: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Post a PR review.

        Args:
            pr_number: PR number
            body: Review body text
            event: APPROVE | REQUEST_CHANGES | COMMENT
            comments: Optional inline comments [{path, line, body}]
        """
        pr = self.repo.get_pull(pr_number)

        review_comments = []
        if comments:
            for c in comments:
                review_comments.append({
                    "path": c["path"],
                    "line": c.get("line", 1),
                    "body": c["body"],
                })

        def _submit(review_event: str) -> None:
            if review_comments:
                pr.create_review(body=body, event=review_event, comments=review_comments)
            else:
                pr.create_review(body=body, event=review_event)

        try:
            _submit(event)
        except GithubException as e:
            if event == "APPROVE":
                # GitHub blocks the default GITHUB_TOKEN from submitting APPROVE
                # reviews — a deliberate anti self-approval security policy, not
                # a bug. A fresh COMMENT-type review does NOT clear an earlier
                # REQUEST_CHANGES from the same reviewer (verified — GitHub only
                # clears that block on a real approval or an explicit dismissal).
                # So: actively dismiss our own prior REQUEST_CHANGES review(s)
                # via the API, then post a COMMENT review with the new verdict.
                print(f"  ℹ️ APPROVE not permitted for GitHub Actions bots ({e}); dismissing prior REQUEST_CHANGES and posting a COMMENT review instead")
                self._dismiss_own_change_requests(pr)
                try:
                    _submit("COMMENT")
                    return
                except GithubException as e2:
                    print(f"  ⚠️ COMMENT review also failed ({e2}), falling back to plain comment...")
                    pr.create_issue_comment(body)
                    return
            # Fall back to simple comment if review fails
            print(f"  ⚠️ Review submission failed ({e}), falling back to comment...")
            pr.create_issue_comment(body)

    @staticmethod
    def _dismiss_own_change_requests(pr: PullRequest) -> None:
        """Dismiss this bot's own prior CHANGES_REQUESTED review(s) on this PR.

        Best-effort: dismissing a review on a protected branch requires repo
        admin (or being on the branch's explicit dismiss-allowlist) per
        GitHub's docs, so this may fail for a plain GITHUB_TOKEN — that's not
        fatal, just means the stale block has to be cleared manually.
        """
        try:
            reviews = list(pr.get_reviews())
        except GithubException as e:
            print(f"  ⚠️ Could not list reviews to dismiss ({e})")
            return

        for review in reviews:
            if review.state != "CHANGES_REQUESTED":
                continue
            if review.user.login != "github-actions[bot]":
                continue
            try:
                review.dismiss("Superseded by a newer Forge review — see below.")
                print(f"  🗑️ Dismissed stale REQUEST_CHANGES review (id={review.id})")
            except GithubException as e:
                print(f"  ⚠️ Could not dismiss review {review.id} ({e}) — likely needs admin/dismiss-allowlist on this protected branch")

    def post_pr_comment(self, pr_number: int, body: str) -> None:
        """Post a simple comment on a PR."""
        pr = self.repo.get_pull(pr_number)
        pr.create_issue_comment(body)

    def react_to_comment(self, comment_id: int, reaction: str = "eyes") -> None:
        """Add an emoji reaction to a PR/issue comment.

        Args:
            comment_id: The issue comment ID.
            reaction: Reaction type — one of: +1, -1, laugh, confused,
                      heart, hooray, rocket, eyes.
        """
        import httpx

        url = f"{self.api_url}/repos/{self.repo_name}/issues/comments/{comment_id}/reactions"
        resp = httpx.post(
            url,
            headers={
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github+json",
            },
            json={"content": reaction},
            timeout=10,
        )
        resp.raise_for_status()

    # ── Issue operations ────────────────────────────────────────────

    def get_issue(self, number: int) -> Issue:
        return self.repo.get_issue(number)

    def get_issue_comments(self, number: int):
        """Get comments on an issue."""
        issue = self.repo.get_issue(number)
        return list(issue.get_comments())

    def post_issue_comment(self, issue_number: int, body: str) -> None:
        issue = self.repo.get_issue(issue_number)
        issue.create_comment(body)

    # ── Branch / PR creation ────────────────────────────────────────

    def create_branch(self, branch_name: str, base_ref: str = "main") -> str:
        """
        Create a new branch from the given base.

        Returns the full branch ref.
        """
        # Get the base branch SHA
        try:
            base = self.repo.get_branch(base_ref)
        except GithubException:
            # Try 'master' if 'main' doesn't exist
            base = self.repo.get_branch("master")
            base_ref = "master"

        ref = f"refs/heads/{branch_name}"
        try:
            self.repo.create_git_ref(ref=ref, sha=base.commit.sha)
        except GithubException as e:
            if e.status == 422:  # Already exists
                print(f"  ℹ️ Branch {branch_name} already exists")
            else:
                raise
        return branch_name

    def create_pr(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        labels: list[str] | None = None,
    ) -> PullRequest:
        """Create a pull request."""
        try:
            pr = self.repo.create_pull(
                title=title,
                body=body,
                head=head,
                base=base,
            )
        except GithubException:
            # Try 'master' if 'main' doesn't exist
            pr = self.repo.create_pull(
                title=title,
                body=body,
                head=head,
                base="master",
            )

        if labels:
            pr.set_labels(*labels)

        return pr

    def commit_and_push(
        self,
        branch: str,
        files: dict[str, str],
        message: str,
    ) -> str:
        """
        Commit multiple files to a branch via the GitHub API (no local git needed).

        Args:
            branch: Branch name to commit to
            files: Dict of {path: content} to commit
            message: Commit message

        Returns:
            Commit SHA
        """
        # Get the current commit on the branch
        ref = self.repo.get_git_ref(f"heads/{branch}")
        base_sha = ref.object.sha
        base_commit = self.repo.get_git_commit(base_sha)
        base_tree = base_commit.tree

        # Create blobs for each file
        tree_elements = []
        from github import InputGitTreeElement
        for path, content in files.items():
            blob = self.repo.create_git_blob(content, "utf-8")
            tree_elements.append(
                InputGitTreeElement(
                    path=path,
                    mode="100644",
                    type="blob",
                    sha=blob.sha,
                )
            )

        # Create tree
        new_tree = self.repo.create_git_tree(tree_elements, base_tree)

        # Create commit
        author = InputGitAuthor(
            "Forge AI", "forge@ccfc.github.io"
        )
        new_commit = self.repo.create_git_commit(
            message=message,
            tree=new_tree,
            parents=[base_commit],
            author=author,
            committer=author,
        )

        # Update ref
        ref.edit(sha=new_commit.sha)
        return new_commit.sha


def sanitize_branch_name(text: str) -> str:
    """Convert issue title to a valid git branch name."""
    # Lowercase, replace spaces and special chars
    name = text.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s]+", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    # Limit length
    return name[:60]
