"""
Mode: Review PR — handles the full PR review flow.

Flow:
  1. Gather PR context (diff, files, metadata)
  2. Build system prompt with team patterns and repo structure
  3. Run ReAct loop → agent explores codebase, then produces review
  4. Parse review output → extract inline comments, recommendation
  5. Post review to GitHub as PR review with inline comments
"""

from __future__ import annotations

import re
from typing import Any

from context_engine import ContextEngine
from github_client import GitHubClient
from react_loop import ReActLoop


def handle_review_pr(
    cfg: Any,
    gh: GitHubClient,
    react: ReActLoop,
    context_engine: ContextEngine,
    memory: Any = None,
) -> dict[str, Any]:
    """Execute the PR review mode."""

    print(f"\n📋 Reviewing PR #{cfg.pr_number}")
    print("━" * 50)

    # 1. Build context
    print("  📦 Gathering PR context...")
    ctx = context_engine.build_review_context(cfg.pr_number)
    pr = ctx["pr"]
    print(f"     Title: {pr['title']}")
    print(f"     Files: {pr['changed_files']} changed (+{pr['additions']} -{pr['deletions']})")

    # 2. Build system prompt
    system_prompt = context_engine.review_system_prompt(ctx)

    # 3. Build user prompt with the diff and prior discussion
    diff_section = ctx["diff"]
    files_summary = "\n".join(
        f"  - `{f['filename']}` ({f['status']}, +{f['additions']} -{f['deletions']})"
        for f in ctx["files"]
    )

    # Include prior review comments so the agent knows what was already discussed
    prior_discussion = ""
    is_follow_up = bool(ctx.get("prior_comments"))
    if is_follow_up:
        parts = []
        for c in ctx["prior_comments"]:
            prefix = f"**@{c['author']}**"
            if c["type"] == "review":
                prefix += f" ({c['state']})"
            parts.append(f"{prefix} — {c['created_at']}:\n{c['body']}")
        prior_discussion = "\n\n---\n\n".join(parts)

    if is_follow_up:
        user_prompt = f"""This is a FOLLOW-UP review. You have reviewed this PR before.

## Changed Files
{files_summary}

## Current Diff (latest code)
```diff
{diff_section[:30000]}
```

## Your Previous Review & Discussion
Read this carefully — this is YOUR prior feedback and the conversation so far:

{prior_discussion}

## What To Do Now
1. Use `git_log` and `git_diff` to see what commits were added since your last review
2. Check if the author addressed your previous feedback
3. For each prior issue: confirm it's fixed, or note it's still open
4. Look for any NEW issues in the new commits
5. Write a SHORT follow-up review (not a full re-review)

Be concise. Be human. A follow-up is 5-15 lines, not a wall of text."""
    else:
        user_prompt = f"""Please review this pull request thoroughly.

## Changed Files
{files_summary}

## Diff
```diff
{diff_section[:30000]}
```

## Instructions
1. First, use tools to explore the codebase and understand the context around the changed files
2. Check how similar code is written elsewhere in the project
3. Look at git history for the changed files to understand evolution
4. Check if tests exist and cover the changes
5. Then provide your comprehensive review

Start by exploring the repository to understand the context before commenting on the diff."""

    # 4. Run ReAct loop
    print("\n🧠 Starting ReAct exploration loop...")

    # For review mode, exclude write tools (read-only exploration)
    read_only_tools = [
        "think", "execute", "read_file", "ls", "glob", "grep",
        "tree", "find_definition", "git_diff", "git_log", "git_show",
        "run_tests",
        "github_comment", "github_read_comments",
        # Cross-repo (read-only — peek at other repos for wider context)
        "cross_repo_read", "cross_repo_ls", "cross_repo_search",
    ]
    result = react.run(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tool_filter=read_only_tools,
    )

    print(f"\n📊 {result.stats_summary()}")

    # 5. Parse the review output
    review_text = result.text
    recommendation = _extract_recommendation(review_text)
    score = _extract_score(review_text)
    inline_comments = _extract_inline_comments(review_text, ctx["files"])

    # Deterministic override: the model is instructed to REQUEST_CHANGES for any
    # open CRITICAL/nit, but prompt-following isn't guaranteed. Don't trust the
    # model's own verdict when its own review text tags a CRITICAL or nit issue —
    # enforce the policy in code instead of hoping it's followed.
    if recommendation == "APPROVE" and _has_blocking_tag(review_text):
        print("  ⚠️ Model said APPROVE but review text tags an open CRITICAL/nit — overriding to REQUEST_CHANGES")
        recommendation = "REQUEST_CHANGES"

    print(f"\n  📝 Review: {recommendation} (score: {score}/10)")
    print(f"  💬 Inline comments: {len(inline_comments)}")

    # 6. Decide review event
    # When recommending APPROVE, always post as APPROVE event to clear any
    # previous REQUEST_CHANGES from this bot. Otherwise the PR stays blocked.
    if recommendation == "APPROVE":
        event = "APPROVE"
        print("  ✅ Approving PR")
    elif recommendation == "REQUEST_CHANGES":
        event = "REQUEST_CHANGES"
        print("  🔄 Requesting changes")
    else:
        event = "COMMENT"

    # 7. Post review to GitHub
    print(f"\n  📤 Posting review ({event})...")
    try:
        gh.post_pr_review(
            pr_number=cfg.pr_number,
            body=review_text,
            event=event,
            comments=inline_comments,
        )
        print("  ✅ Review posted!")
    except Exception as e:
        print(f"  ⚠️ Failed to post review: {e}")
        # Fall back to comment
        try:
            gh.post_pr_comment(cfg.pr_number, review_text)
            print("  ✅ Posted as comment (fallback)")
        except Exception as e2:
            print(f"  ❌ Failed to post comment: {e2}")

    # 8. Store review in memory for future reference
    if memory and memory.available:
        memory.store_review(
            pr_number=cfg.pr_number,
            author=pr["author"],
            summary=review_text[:5000],
            recommendation=recommendation,
        )

    return {
        "status": "success",
        "summary": f"Reviewed PR #{cfg.pr_number}: {recommendation} ({score}/10), {len(inline_comments)} comments",
        "recommendation": recommendation,
        "review_comments": len(inline_comments),
    }


# ──────────────────────────────────────────────────────────────────────
# Parsing helpers
# ──────────────────────────────────────────────────────────────────────

def _extract_recommendation(text: str) -> str:
    """Extract APPROVE / REQUEST_CHANGES / COMMENT from review text."""
    text_upper = text.upper()
    if "RECOMMENDATION**: REQUEST_CHANGES" in text_upper or "**RECOMMENDATION**: REQUEST_CHANGES" in text_upper:
        return "REQUEST_CHANGES"
    if "RECOMMENDATION**: APPROVE" in text_upper or "**RECOMMENDATION**: APPROVE" in text_upper:
        return "APPROVE"

    # Fallback: the model didn't follow the "**Recommendation**: X" template
    # exactly. Pick whichever keyword appears LAST in the text — the
    # recommendation is always the final field — instead of checking one
    # keyword before the other, which let an incidental earlier mention of
    # "REQUEST_CHANGES" (e.g. quoting a prior review) silently win over the
    # model's actual final verdict.
    rc_pos = text_upper.rfind("REQUEST_CHANGES")
    approve_pos = text_upper.rfind("APPROVE")
    if approve_pos != -1 and text_upper[max(0, approve_pos - 5):approve_pos] == "AUTO-":
        approve_pos = -1  # that was "AUTO-APPROVE", not a recommendation

    if rc_pos == -1 and approve_pos == -1:
        return "COMMENT"
    return "REQUEST_CHANGES" if rc_pos > approve_pos else "APPROVE"


_BLOCKING_TAG_RE = re.compile(r"\*\*(CRITICAL|nit)\*\*", re.IGNORECASE)


def _has_blocking_tag(text: str) -> bool:
    """True if the review body tags an issue as CRITICAL or nit (per the
    **CRITICAL**/**nit**/**suggestion** severity format the prompt instructs).
    Used to override a model-claimed APPROVE deterministically — a "suggestion"
    tag never matches this, matching the policy that suggestions never block.
    """
    return bool(_BLOCKING_TAG_RE.search(text))


def _extract_score(text: str) -> int:
    """Extract quality score (X/10) from review text."""
    match = re.search(r"(?:Quality\s+Score|Score)\s*[:\s]*\**\s*(\d+)\s*/\s*10", text, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return 5  # Default


def _extract_inline_comments(
    text: str, files: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    Extract inline review comments from the review text.

    Supports multiple formats the model might use:
      - File: `path/to/file.py` line 42
      - File: `path/to/file.py` (line 42)
      - `path/to/file.py:42`
      - **File**: `path/to/file.py`, line 42
      - In `path/to/file.py` at line 42
    """
    comments: list[dict[str, Any]] = []
    valid_paths = {f["filename"] for f in files}

    # Multiple patterns the model might use, tried in order
    patterns = [
        # File: `path` line N  /  File: `path` (line N)
        re.compile(
            r"\*{0,2}File\*{0,2}:\s*`([^`]+)`\s*(?:[,:]?\s*\(?lines?\s*(\d+)\)?)?",
            re.IGNORECASE,
        ),
        # `path:N` (colon-separated)
        re.compile(
            r"`([^`]+?):(\d+)`",
        ),
        # In `path` at line N
        re.compile(
            r"[Ii]n\s+`([^`]+)`\s+(?:at\s+)?lines?\s+(\d+)",
        ),
    ]

    # Try each pattern and collect matches
    seen: set[tuple[str, int]] = set()
    for pattern in patterns:
        # Use finditer for more precise extraction
        for match in pattern.finditer(text):
            path = match.group(1).strip()
            line_str = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            line = int(line_str) if line_str else 1

            if path not in valid_paths:
                continue
            if (path, line) in seen:
                continue
            seen.add((path, line))

            # Extract body: text after the match until next heading, file ref, or pattern
            start = match.end()
            rest = text[start:]
            # Stop at next heading, file reference, or end-of-section marker
            for stop_pattern in ["\n### ", "\n## ", "\nFile:", "\n**File", "\n`"]:
                idx = rest.find(stop_pattern)
                if idx != -1:
                    rest = rest[:idx]
            body = rest.strip()

            # Skip if body is too short or looks like another pattern match
            if body and len(body) > 10:
                comments.append({
                    "path": path,
                    "line": line,
                    "body": body[:2000],
                })

    return comments[:20]  # Limit inline comments
