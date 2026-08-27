"""
Context Engine — gathers all contextual information for the agent.

Builds rich context from:
  - Repository structure (tree)
  - Team coding patterns (.github/CODING_PATTERNS.md)
  - PR diff and metadata (for review mode)
  - Git history (recent commits)
  - Additional context paths specified by the user
"""

from __future__ import annotations

import os
from typing import Any

from github_client import GitHubClient


class ContextEngine:
    """Assembles context for the Forge agent's system prompt."""

    def __init__(
        self,
        workspace: str,
        gh: GitHubClient,
        team_patterns_path: str = ".github/CODING_PATTERNS.md",
        extra_paths: list[str] | None = None,
    ) -> None:
        self.workspace = workspace
        self.gh = gh
        self.team_patterns_path = team_patterns_path
        self.extra_paths = extra_paths or []
        self.memory_context: str = ""  # injected by forge_agent from ForgeMemory

    # ── Public builders ─────────────────────────────────────────────

    def build_review_context(self, pr_number: int) -> dict[str, Any]:
        """Build context for PR review mode."""
        pr = self.gh.get_pr(pr_number)
        diff = self.gh.get_pr_diff(pr_number)
        files = self.gh.get_pr_files(pr_number)
        prior_comments = self.gh.get_pr_comments(pr_number)

        return {
            "pr": {
                "number": pr.number,
                "title": pr.title,
                "body": pr.body or "(no description)",
                "author": pr.user.login,
                "base": pr.base.ref,
                "head": pr.head.ref,
                "changed_files": pr.changed_files,
                "additions": pr.additions,
                "deletions": pr.deletions,
                "labels": [l.name for l in pr.labels],
            },
            "diff": diff,
            "files": [
                {
                    "filename": f.filename,
                    "status": f.status,
                    "additions": f.additions,
                    "deletions": f.deletions,
                    "patch": f.patch or "",
                }
                for f in files
            ],
            "prior_comments": prior_comments,
            "team_patterns": self._load_team_patterns(),
            "repo_structure": self._get_repo_structure(),
            "extra_context": self._load_extra_context(),
        }

    # ── System prompts ──────────────────────────────────────────────

    def review_system_prompt(self, ctx: dict[str, Any]) -> str:
        """Build the system prompt for PR review mode."""
        patterns = ctx["team_patterns"]
        structure = ctx["repo_structure"]
        pr = ctx["pr"]
        extra = ctx["extra_context"]
        has_prior = bool(ctx.get("prior_comments"))

        prompt = f"""You are Forge, a senior software engineer on the team.
You review PRs the way a sharp, friendly human colleague does — direct, helpful,
and efficient. Only raise a nit when it's a genuine, worthwhile quality issue —
not just to have something to say (see Review Rules for how severity works).

## Your Personality
- Talk like a real developer. Use "nit:", "ship it 🚀", "nice catch",
  "this could bite us later", "clean impl" — whatever fits naturally.
- Be concise. A good review comment is 1-3 sentences, not a paragraph.
- When something is good, say so briefly: "Nice — clean separation of concerns."
- When something is bad, be direct: "This will NPE on null input. Needs a guard."
- Don't open with a generic "Great PR!" — go straight into the substance (TL;DR).
- Never use phrases like "Comprehensive assessment" or "meticulous attention".
  Talk like a human, not a press release.
- Sign off naturally, but never "LGTM" or imply approval while a CRITICAL
  issue is still open — that's a contradiction. Save sign-offs like "ship it 🚀"
  for when you are actually recommending APPROVE. Not "*Reviewed by Forge*".

## Your Memory
- You are the SAME reviewer across all review cycles on this PR.
- If you gave feedback before, you REMEMBER it. Check if it was addressed.
- Be conversational on follow-ups: "Fixed, thanks!" or "Still seeing the issue on L42."
{"- Keep re-reviews SHORT — 5-10 lines max. Don't re-review what you already approved." if has_prior else ""}

## Your Approach — ReAct Pattern
THINK → ACT (explore with tools) → OBSERVE → repeat until confident.
{"Keep exploration SHORT on re-reviews — you already know the codebase." if has_prior else ""}

## Review Rules
1. Explore related files BEFORE commenting — understand, then critique
2. Be specific: file names, line numbers, function names. Always.
3. Tag every issue as one of three severities:
   - CRITICAL (bugs, security, data loss, broken behavior)
   - nit (style, naming, code smell — a real but minor quality issue)
   - suggestion (an idea or optional improvement, not a problem with the code as-is)
   The line between nit and suggestion: if the code AS WRITTEN has something
   wrong with it (bad name, wrong logging, duplicated logic, missed edge case)
   → nit. If the code as written is correct and fine, and you're proposing
   something NEW it doesn't currently do (an enhancement, an extra parameter,
   a feature it doesn't need) → suggestion. Tag accurately regardless of
   severity's consequence — the distinction still matters for the reader
   even though only CRITICAL affects the merge decision (see Rule 6).
4. Provide concrete suggestion blocks (code examples) when you can
5. Acknowledge good work — "clean impl", "nice pattern" — but keep it brief
6. REQUEST_CHANGES only for an unresolved CRITICAL issue — including one you
   raised yourself in an earlier round. Neither "nit" nor "suggestion" ever
   blocks — tag them accurately (Rule 3), mention them in the review, and
   still APPROVE if no CRITICAL remains. Tagging severity is not optional
   even though only CRITICAL has a merge consequence — the reader still
   needs to know what's a real quality issue (nit) vs. an idea (suggestion).
{"7. On follow-ups: confirm fixes, flag remaining issues, skip re-reviewing everything" if has_prior else ""}
{"8. DO NOT re-raise issues that were addressed. Say 'fixed' and move on." if has_prior else ""}

## GitHub Interaction — Your Choice
You have tools to interact directly:
- **github_comment**: Post a comment on any PR or issue
- **github_read_comments**: Read existing discussion

Use these freely — ask questions, share findings, reply to threads.
No hardcoded rules. You decide when and what to comment.

## Repository Context
```
{structure}
```

{f"### Team Patterns{chr(10)}{patterns}" if patterns else ""}

## PR Under Review
- **#{pr['number']}**: {pr['title']}
- **Author**: {pr['author']}
- **Branch**: {pr['head']} → {pr['base']}
- **Changes**: {pr['changed_files']} files, +{pr['additions']} -{pr['deletions']}
- **Labels**: {', '.join(pr['labels']) if pr['labels'] else 'none'}
- **Description**: {pr['body'][:2000]}

{self.memory_context}{f"## Additional Context{chr(10)}{extra}" if extra else ""}
## What To Look For
Bugs, logic errors, edge cases, security vulnerabilities, performance issues,
missing error handling, test coverage gaps, breaking changes, style consistency.

## Output Format
{"### FOLLOW-UP REVIEW (prior reviews exist)" if has_prior else "### INITIAL REVIEW"}
{self._follow_up_format(pr) if has_prior else self._initial_review_format(pr)}
"""
        return prompt

    def _initial_review_format(self, pr: dict) -> str:
        return f"""Write your review naturally. Here's the structure to follow:

**TL;DR**: [1-2 sentences. What does this PR do and is it ready?]

**Score**: [X/10]

### What's good
- [Brief. 2-4 bullet points max.]

### Issues
For each issue:
**[CRITICAL|nit|suggestion]** `filename` L[N]
[1-3 sentence description. Include a suggestion code block when you can.]

### Tests
[1-2 sentences on test coverage. Skip if N/A.]

### Security
[Only if relevant. Otherwise skip this section entirely.]

**Recommendation**: APPROVE | REQUEST_CHANGES | COMMENT
(REQUEST_CHANGES only for an unresolved CRITICAL above. Open "nit" or
"suggestion" items never block — APPROVE regardless of those.)"""

    def _follow_up_format(self, pr: dict) -> str:
        return f"""This is a follow-up. Be terse. A human reviewer writes 3-10 lines for a re-review.

**What was fixed**: [list items from prior feedback that are now addressed]

**Still open**: [anything unresolved, or "All good."]

**New issues**: [anything new, or skip if none]

**Score**: [X/10]

**Recommendation**: APPROVE | REQUEST_CHANGES | COMMENT
(REQUEST_CHANGES only if a CRITICAL is still open — check "Still open" against
severity, not just presence. A still-open nit or suggestion never blocks.)

That's it. Don't re-review what's already approved. Don't write an essay."""

    # ── Private helpers ──────────────────────────────────────────────

    def _load_team_patterns(self) -> str:
        """Load team coding patterns file if it exists."""
        path = os.path.join(self.workspace, self.team_patterns_path)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                return content[:5000]  # Limit size
            except Exception:
                pass
        return ""

    def _get_repo_structure(self) -> str:
        """Get a compact repo structure (top 2 levels)."""
        lines: list[str] = []
        self._walk_tree(self.workspace, "", 2, lines)
        return "\n".join(lines[:100])

    def _walk_tree(self, root: str, prefix: str, depth: int, lines: list[str]) -> None:
        if depth < 0 or len(lines) > 100:
            return
        try:
            entries = sorted(os.listdir(root))
            entries = [e for e in entries if not e.startswith(".") and e not in (
                "node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build",
                "target", ".next", ".nuxt", "coverage",
            )]
            for i, name in enumerate(entries):
                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "
                fp = os.path.join(root, name)
                if os.path.isdir(fp):
                    lines.append(f"{prefix}{connector}{name}/")
                    extension = "    " if is_last else "│   "
                    self._walk_tree(fp, prefix + extension, depth - 1, lines)
                else:
                    lines.append(f"{prefix}{connector}{name}")
        except PermissionError:
            pass

    def _load_extra_context(self) -> str:
        """Load additional context files specified by the user."""
        parts: list[str] = []
        for rel_path in self.extra_paths:
            full = os.path.join(self.workspace, rel_path)
            if os.path.isfile(full):
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        content = f.read()
                    parts.append(f"### {rel_path}\n```\n{content[:3000]}\n```")
                except Exception:
                    pass
        return "\n\n".join(parts)
