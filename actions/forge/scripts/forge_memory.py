"""
Forge Memory — persistent, git-committed memory for cross-run awareness.

Architecture inspired by:
  - DeepAgents: AGENTS.md self-updating memory files
  - LangChain: ConversationSummaryBufferMemory (bounded rolling summary)

Storage: .github/forge/memory.md in the repo (human-readable, version-controlled).

Sections:
  - Project Context (stable)   — tech stack, architecture, key files
  - Learned Patterns (rolling)  — patterns discovered across reviews/implementations
  - Common Issues (rolling)     — recurring problems with frequency counts

Bounding: when the file exceeds MAX_TOKENS (~4000 tokens ≈ 16KB), the LLM
compresses the oldest entries.  The memory file is loaded into the system
prompt at startup (same pattern as CODING_PATTERNS.md).
"""

from __future__ import annotations

import os
from typing import Any

# Approximate: 4 chars ≈ 1 token, so 16KB ≈ 4000 tokens
MAX_MEMORY_CHARS = 16_000
MEMORY_DIR = ".github/forge"
MEMORY_FILE = f"{MEMORY_DIR}/memory.md"

BOOTSTRAP_TEMPLATE = """# Forge Memory

> Auto-maintained by Forge. Humans can edit this too.

## Project Context
<!-- Stable info: tech stack, architecture, key conventions -->
- _Forge will populate this after its first run._

## Learned Patterns
<!-- Rolling: patterns discovered across reviews and implementations -->

## Common Issues
<!-- Rolling: recurring problems Forge has seen, with counts -->
"""


class ForgeMemory:
    """Persistent memory backed by a Markdown file in the repo."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.memory_path = os.path.join(workspace, MEMORY_FILE)
        self._content: str | None = None

    def load(self) -> str:
        """Load memory file contents, or return empty string if not found."""
        if os.path.isfile(self.memory_path):
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    self._content = f.read()
                return self._content
            except Exception:
                return ""
        self._content = ""
        return ""

    @property
    def exists(self) -> bool:
        return os.path.isfile(self.memory_path)

    @property
    def content(self) -> str:
        if self._content is None:
            self.load()
        return self._content or ""

    def save(self, content: str) -> None:
        """Write memory content to disk (for local commit later)."""
        os.makedirs(os.path.join(self.workspace, MEMORY_DIR), exist_ok=True)
        with open(self.memory_path, "w", encoding="utf-8") as f:
            f.write(content)
        self._content = content

    def needs_compression(self) -> bool:
        """Check if memory exceeds the size bound."""
        return len(self.content) > MAX_MEMORY_CHARS

    def build_update_prompt(self, run_summary: str, mode: str) -> str:
        """Build a prompt for the LLM to update the memory file.

        Args:
            run_summary: Summary of what Forge did this run.
            mode: review_pr.

        Returns:
            A user prompt to send to the LLM for memory update.
        """
        current = self.content or BOOTSTRAP_TEMPLATE
        compress_note = ""
        if self.needs_compression():
            compress_note = (
                "\n\nIMPORTANT: The memory file is getting long. "
                "Compress the oldest entries in 'Learned Patterns' and "
                "'Common Issues' — merge similar items, drop stale ones. "
                "Keep total under 300 lines."
            )

        return f"""Update the Forge memory file based on this run.

## Current Memory
```markdown
{current}
```

## What Happened This Run
- **Mode**: {mode}
- **Summary**: {run_summary[:3000]}

## Rules
1. Keep the same Markdown structure (## Project Context, ## Learned Patterns, ## Common Issues)
2. If Project Context is empty, fill it from what you learned about the repo
3. Add any new patterns you discovered to Learned Patterns (one bullet each)
4. If you saw a recurring issue, increment its count or add it to Common Issues
5. Don't duplicate existing entries — update them instead
6. Be concise: each bullet should be 1 line
7. Don't remove human-added content (anything not added by Forge)
{compress_note}

Return the COMPLETE updated memory file content (just the markdown, no code fences)."""


def build_memory_context(memory: ForgeMemory) -> str:
    """Format memory for injection into the system prompt."""
    content = memory.load()
    if not content:
        return ""
    return f"\n## Forge Memory (from past runs)\n{content}\n"
