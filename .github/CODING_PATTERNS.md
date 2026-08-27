# ops-factory Coding Patterns

This file teaches Forge ops-factory's own conventions. Forge reads this before every PR review of this repo.

## Project Structure

- `actions/forge/action.yml` — the composite action's contract (inputs/outputs/steps). Any new input must be read somewhere in `forge_agent.py`'s `ForgeConfig`, and any input removed from `action.yml` must be removed from every workflow that calls it (`.github/workflows/*.yml`, `docs/examples/*.yml`).
- `actions/forge/scripts/` — flat module layout, no subpackages. One concern per file (`tools.py` = tool registry, `context_engine.py` = prompt building, `mode_review.py` = review orchestration, etc.). Keep new functionality in an existing module unless it's a genuinely new concern.
- `.github/workflows/` — reusable/dispatch workflows only live here. Example/template workflows for consumer repos live in `docs/examples/`, never here.

## Code Style (Python)

- Python 3.11+, `from __future__ import annotations` at the top of every module.
- Type hints on all function signatures; prefer `X | None` over `Optional[X]`.
- No docstrings on trivial functions — only where behavior is non-obvious (see repo-wide convention already in `tools.py`, `react_loop.py`).
- Errors from tool execution become observations, not exceptions — follow the existing pattern in `react_loop.py` (`try/except` around `tools.execute`, turn failures into a string the model sees) rather than letting exceptions propagate out of the ReAct loop.
- Prints, not a logging framework — this is a short-lived CI process; stick to the existing `print()` + emoji-prefixed status lines rather than introducing `logging`.

## The `generate()` Contract

Any LLM client (`openai_client.py` today) must return the exact dict shape `react_loop.py` expects: `text`, `function_calls`, `executable_code`, `code_results`, `grounding_metadata`, `parts`, `finish_reason`, `usage`. If a provider has no equivalent for a field (e.g. no server-side code execution), return the empty/`None` default — don't omit the key, `react_loop.py` and `forge_agent.py` read these unconditionally.

## Tools (`tools.py`)

- Every tool is a subclass of `Tool` with `name`, `description`, `parameters` (JSON Schema), and `execute(**kwargs) -> str`.
- New tools must be registered in `ToolRegistry.__init__` (always available) or via a `register_*_tools()` helper (conditionally available, e.g. `register_cross_repo_tools`) — follow the existing split, don't hardcode tool wiring elsewhere.
- Tool output is always a string, truncated with head/tail preservation for long output (see `ExecuteTool`, `GitDiffTool`) — never return raw objects.
- Anything that shells out must go through the `blocked` pattern list in `ExecuteTool.execute` if it's remotely destructive — extend that list rather than adding a second gate elsewhere.

## Testing

- `test_mode_review.py` and `test_github_client.py` are real unit tests (pytest, mocked APIs, no network calls) covering the pure-logic parts: recommendation parsing, the severity-based blocking override, and the PR review-posting fallback chain. Run with `pip install -r requirements.txt pytest && pytest` from `actions/forge/scripts/`.
- `test_local.py` is a separate manual integration harness (`python test_local.py` against a real `OPENAI_API_KEY`) — not run in CI, use it to sanity-check actual OpenAI connectivity and tool execution end-to-end.
- Any change to `mode_review.py`'s text-parsing logic (`_extract_recommendation`, `_has_blocking_tag`) or `github_client.py`'s `post_pr_review` fallback chain must come with a passing unit test — these are exactly the places where a silent regression previously caused real, hard-to-diagnose problems (a misread verdict, an unrecoverable stuck PR review).

## GitHub Actions / Workflow Conventions

- `runs-on: ubuntu-latest` (GitHub-hosted) — never reintroduce a custom self-hosted runner label without updating every workflow consistently.
- Reusable workflows (`workflow_call`) declare every input/secret they pass through to `actions/forge` explicitly — don't let a workflow silently drop or rename something `action.yml` expects.
- Absolute `uses: <owner>/<repo>/...@<tag>` references must match the actual current home of this repo — when the repo moves (e.g. personal account → org), every such reference must move with it (see `docs/FORGE.md`'s architecture section for the full list of files that hardcode this).

## Secrets & Security

- Never hardcode a real project ID, API key, account email, or internal hostname in a workflow or script — use `secrets.*` / `vars.*` even in "self" workflows like `forge-self-review.yml`.
- Flag hardcoded credentials or tokens in review as CRITICAL, not a nit.

## Git Conventions

- Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `feat!:`) — `auto-release.yml` parses these to compute the next version bump. A wrongly-typed prefix silently changes the release type.
