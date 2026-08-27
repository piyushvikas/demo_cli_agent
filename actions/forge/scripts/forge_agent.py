#!/usr/bin/env python3
"""
Forge Agent — OpenAI-powered ReAct coding assistant.

Entrypoint for the Forge composite action. Dispatches to the review_pr
mode handler and orchestrates the full ReAct loop against the OpenAI API.

Architecture inspired by:
  - DSPy ReAct: trajectory-as-dict, two-phase (loop → extraction)
  - DeepAgents: filesystem tools, shell execution, middleware
  - LangChain: safety guards (max_iterations + wall-clock timeout)
  - Open Deep Research: structured actions via Pydantic, think tool
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from typing import Any

from context_engine import ContextEngine
from forge_memory import ForgeMemory, build_memory_context
from github_client import GitHubClient
from mode_review import handle_review_pr
from react_loop import ReActLoop
from tools import ToolRegistry, GitHubCommentTool, GitHubReadCommentsTool, register_cross_repo_tools
from openai_client import OpenAIClient


# ──────────────────────────────────────────────────────────────────────
# Configuration from environment
# ──────────────────────────────────────────────────────────────────────

class ForgeConfig:
    """All configuration flows through environment variables set by action.yml."""

    def __init__(self) -> None:
        # OpenAI
        self.openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
        self.model_name: str = os.environ.get("MODEL_NAME", "gpt-4o")

        # GitHub
        self.github_token: str = os.environ["GITHUB_TOKEN"]
        self.github_repository: str = os.environ["GITHUB_REPOSITORY"]
        self.github_workspace: str = os.environ.get("GITHUB_WORKSPACE", os.getcwd())

        # Mode — accept short alias (review)
        _MODE_ALIASES = {
            "review": "review_pr",
        }
        raw_mode = os.environ["FORGE_MODE"].strip().lower()
        self.mode: str = _MODE_ALIASES.get(raw_mode, raw_mode)
        self.pr_number: int | None = _int_or_none(os.environ.get("PR_NUMBER"))

        # Limits
        self.auto_approve: bool = os.environ.get("AUTO_APPROVE", "false").lower() == "true"
        self.context_paths: list[str] = [
            p.strip() for p in os.environ.get("CONTEXT_PATHS", "").split(",") if p.strip()
        ]
        self.team_patterns_path: str = os.environ.get(
            "TEAM_PATTERNS_PATH", ".github/CODING_PATTERNS.md"
        )
        self.max_iterations: int = int(os.environ.get("MAX_ITERATIONS", "15"))

    def validate(self) -> None:
        if self.mode not in ("review_pr", "analyze"):
            _die(f"Unknown mode: {self.mode}")
        if self.mode == "review_pr" and not self.pr_number:
            _die("PR_NUMBER required for review_pr mode")
        if not self.openai_api_key:
            _die("OPENAI_API_KEY is required")


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    start = time.time()
    print("🔨 Forge AI Coding Assistant")
    print("━" * 50)

    # 1. Load config
    try:
        cfg = ForgeConfig()
        cfg.validate()
    except Exception as exc:
        _die(f"Configuration error: {exc}")

    print(f"  Mode       : {cfg.mode}")
    print(f"  Model      : {cfg.model_name}")
    print(f"  Repository : {cfg.github_repository}")
    print(f"  Iterations : {cfg.max_iterations}")
    print()

    # 2. Initialise clients
    gh = GitHubClient(cfg.github_token, cfg.github_repository)
    llm = OpenAIClient(
        api_key=cfg.openai_api_key,
        model_name=cfg.model_name,
    )

    # 3. Build tools
    tools = ToolRegistry(workspace=cfg.github_workspace)
    # Register GitHub interaction tools — agent decides when to comment/reply
    tools.register(GitHubCommentTool(gh))
    tools.register(GitHubReadCommentsTool(gh))
    # Register cross-repo tools (always available — PAT scope determines access)
    register_cross_repo_tools(tools, gh)

    # 4. Build context
    context_engine = ContextEngine(
        workspace=cfg.github_workspace,
        gh=gh,
        team_patterns_path=cfg.team_patterns_path,
        extra_paths=cfg.context_paths,
    )

    # 4a. Load persistent memory
    forge_memory = ForgeMemory(workspace=cfg.github_workspace)
    mem_content = forge_memory.load()
    if mem_content:
        print(f"  🧠 Loaded memory ({len(mem_content)} chars)")
        context_engine.memory_context = build_memory_context(forge_memory)
    else:
        print("  🧠 No memory file yet (will bootstrap after first run)")
        context_engine.memory_context = ""

    # 5. Create ReAct loop
    react = ReActLoop(
        llm=llm,
        tools=tools,
        max_iterations=cfg.max_iterations,
        max_wall_clock=600,  # 10 minute hard limit
    )
    memory = None  # No File Search memory — that's a Gemini-only feature

    # 6. Dispatch to mode handler
    try:
        if cfg.mode == "review_pr":
            result = handle_review_pr(
                cfg=cfg, gh=gh, react=react, context_engine=context_engine,
                memory=memory,
            )
        else:
            _die(f"Mode '{cfg.mode}' not yet implemented")
            return
    except Exception:
        tb = traceback.format_exc()
        print(f"\n❌ Forge failed:\n{tb}")
        _set_output("status", "failure")
        _set_output("summary", f"Forge failed: {tb.splitlines()[-1]}")
        sys.exit(1)

    # 6a. Update persistent memory
    try:
        _update_memory(llm, forge_memory, result, cfg)
    except Exception as e:
        print(f"  ⚠️ Memory update failed (non-fatal): {e}")

    elapsed = time.time() - start
    print(f"\n✅ Forge completed in {elapsed:.1f}s")

    # 7. Write outputs
    _set_output("status", result.get("status", "success"))
    _set_output("summary", result.get("summary", ""))
    _set_output("recommendation", result.get("recommendation", ""))
    _set_output("review_comments", str(result.get("review_comments", 0)))


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _int_or_none(val: str | None) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except ValueError:
        return None


def _update_memory(
    llm: OpenAIClient,
    forge_memory: ForgeMemory,
    result: dict[str, Any],
    cfg: ForgeConfig,
) -> None:
    """Ask the LLM to update the persistent memory file after a run."""
    summary = result.get("summary", "")
    if not summary:
        return

    print("\n  🧠 Updating memory...")
    prompt = forge_memory.build_update_prompt(summary, cfg.mode)
    response = llm.generate(
        messages=[{"role": "user", "text": prompt}],
        tools=None,
        system_instruction=(
            "You maintain a concise memory file for an AI coding assistant. "
            "Return only the updated Markdown content, no code fences."
        ),
        temperature=0.0,
    )
    new_content = response.get("text", "").strip()
    if new_content and len(new_content) > 50:
        forge_memory.save(new_content)
        print(f"  ✅ Memory updated ({len(new_content)} chars)")
    else:
        print("  ⚠️ Memory update produced empty content — skipped")


def _set_output(name: str, value: str) -> None:
    """Write a GitHub Actions output variable."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            # Handle multiline values
            if "\n" in value:
                import uuid
                delimiter = f"ghadelimiter_{uuid.uuid4()}"
                f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                f.write(f"{name}={value}\n")
    # Always print for local testing
    print(f"  ::output:: {name} = {value[:200]}{'...' if len(value) > 200 else ''}")


def _die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    _set_output("status", "failure")
    _set_output("summary", msg)
    sys.exit(1)


if __name__ == "__main__":
    main()
