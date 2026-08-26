"""
Tool Registry — all tools available to the Forge ReAct agent.

Design principles (from studying DSPy, DeepAgents, LangChain):
  - Tools are plain Python callables with Pydantic-style schemas
  - Each tool has a name, description, parameters dict, and execute() method
  - The registry auto-generates Gemini function declarations
  - Tools operate within the GitHub Actions workspace (sandboxed by CI)
  - Shell execution has timeout guards

Cross-platform notes:
  CI runner  : Ubuntu Linux (runs-on: ubuntu-latest, shell: bash)
  Local dev  : Windows / macOS
  All tools use pure-Python or git-based fallbacks so they work on both.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

# ── Platform detection ────────────────────────────────────────────
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
HAS_BASH = shutil.which("bash") is not None
HAS_GIT = shutil.which("git") is not None
_PLATFORM_LABEL = f"{platform.system()} {platform.machine()}"


# ──────────────────────────────────────────────────────────────────────
# Tool base
# ──────────────────────────────────────────────────────────────────────

class Tool:
    """Base class for a Forge tool."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    workspace: str = ""  # set by subclasses that need file access

    def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError

    def declaration(self) -> dict[str, Any]:
        """Return Gemini function-declaration dict."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def _not_found(self, path: str) -> str:
        """Return a helpful error when a file path doesn't exist."""
        import glob as _glob
        basename = os.path.basename(path)
        suggestions: list[str] = []
        if self.workspace and basename:
            matches = _glob.glob(
                os.path.join(self.workspace, "**", basename), recursive=True
            )
            suggestions = [
                os.path.relpath(m, self.workspace).replace("\\", "/")
                for m in matches[:5]
            ]
        hint = ""
        if suggestions:
            hint = "\nDid you mean: " + ", ".join(f"`{s}`" for s in suggestions)
        return f"Error: file not found: {path}{hint}\nUse `tree` or `glob` to explore."


# ──────────────────────────────────────────────────────────────────────
# Individual tools
# ──────────────────────────────────────────────────────────────────────

class ThinkTool(Tool):
    """Explicit reasoning step — no side effects, just records a thought."""

    name = "think"
    description = (
        "Use this to reason step-by-step about the problem before taking action. "
        "Record your analysis, plans, or observations. No side effects."
    )
    parameters = {
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Your step-by-step reasoning about what to do next.",
            }
        },
        "required": ["reasoning"],
    }

    def execute(self, reasoning: str = "", **kw: Any) -> str:
        return f"[Thought recorded: {reasoning[:200]}]"


class ExecuteTool(Tool):
    """Run a shell command in the workspace."""

    name = "execute"
    # Description is set dynamically in __init__ based on detected OS
    description = ""
    parameters = {
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute. Examples: 'git log --oneline -10', 'grep -rn TODO src/', 'find . -name \"*.py\" | head -20'",
            }
        },
        "required": ["command"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        # OS-aware description so the model picks appropriate commands
        if IS_LINUX:
            self.description = (
                "Execute a shell command (bash) in the repository workspace. Use for: "
                "git commands, grep, find, cat, head, tail, wc, tree, ls, npm/pip commands, "
                "running tests, etc. Commands run in a sandboxed CI environment. "
                "Timeout: 120 seconds. Avoid destructive commands (rm -rf /, etc)."
            )
        else:
            self.description = (
                "Execute a shell command in the repository workspace. "
                "Available: git commands, python, pip, node/npm. "
                "Note: Unix commands (grep/find/cat) may not be available on this OS; "
                "use the dedicated grep, glob, read_file tools instead. "
                "Timeout: 120 seconds. Avoid destructive commands."
            )

    def execute(self, command: str = "", **kw: Any) -> str:
        if not command.strip():
            return "Error: empty command"

        # Safety: block destructive / dangerous command patterns
        blocked = [
            # Filesystem destruction
            "rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf .",
            "rmdir /s /q c:", "del /f /s /q c:",
            "mkfs", "dd if=", "shred ",
            # Fork bombs
            ":(){", "fork bomb", ":(){ :|:& };",
            # Privilege escalation
            "chmod 777 /", "chown root", "sudo su",
            # Network exfiltration
            "curl -d", "curl --data", "wget --post",
            "curl -F", "curl --upload",
            # Crypto / reverse shells
            "nc -e", "ncat -e", "bash -i >&", "/dev/tcp/",
            "python -c 'import socket",
            # History / credential theft
            ".bash_history", ".ssh/", "id_rsa",
            # Disk fill
            "yes >", "/dev/zero",
        ]
        cmd_lower = command.lower()
        for b in blocked:
            if b in cmd_lower:
                return f"Error: blocked dangerous command pattern: {b}"

        try:
            # On Windows, try to use bash (Git Bash) for better Unix compat
            # On Linux CI this is a no-op — shell=True already uses /bin/sh
            if IS_WINDOWS and HAS_BASH and self._needs_bash(command):
                run_cmd: Any = ["bash", "-c", command]
                use_shell = False
            else:
                run_cmd = command
                use_shell = True

            result = subprocess.run(
                run_cmd,
                shell=use_shell,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=120,
                errors="replace",
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            output = result.stdout or ""
            if result.stderr:
                output += f"\n[stderr]: {result.stderr}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"

            # Truncate very long output — head/tail preservation
            if len(output) > 15000:
                output = (
                    output[:6000]
                    + "\n\n… [output truncated — showing first 6K and last 6K chars] …\n\n"
                    + output[-6000:]
                )

            return output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 120 seconds"
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def _needs_bash(command: str) -> bool:
        """Detect commands that need a Unix shell (grep, find, cat, etc.)."""
        unix_cmds = (
            "grep ", "find ", "cat ", "head ", "tail ", "wc ",
            "sed ", "awk ", "sort ", "uniq ", "xargs ", "chmod ",
            "ls ", "ls\n", "tree ", "which ", "curl ", "wget ",
        )
        # Pipes and redirections also benefit from bash
        if "|" in command or ">" in command or "&&" in command:
            return True
        return command.startswith(unix_cmds) or command in ("ls", "tree", "pwd")


class ReadFileTool(Tool):
    """Read file contents with optional line range."""

    name = "read_file"
    description = (
        "Read contents of a file. Specify start_line and end_line for large files. "
        "Line numbers are 1-based. If omitted, reads the entire file (truncated at 500 lines)."
    )
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file from the repository root.",
            },
            "start_line": {
                "type": "integer",
                "description": "First line to read (1-based, inclusive). Default: 1.",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to read (1-based, inclusive). Default: 500.",
            },
        },
        "required": ["path"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, path: str = "", start_line: int = 1, end_line: int = 500, **kw: Any) -> str:
        full = os.path.join(self.workspace, path)
        if not os.path.isfile(full):
            return self._not_found(path)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            total = len(lines)
            start = max(1, int(start_line)) - 1
            end = min(total, int(end_line))
            selected = lines[start:end]
            numbered = [f"{i + start + 1:4d}: {line.rstrip()}" for i, line in enumerate(selected)]
            header = f"[{path}] lines {start + 1}-{end} of {total}"
            return header + "\n" + "\n".join(numbered)
        except Exception as e:
            return f"Error reading {path}: {e}"


class WriteFileTool(Tool):
    """Create or overwrite a file."""

    name = "write_file"
    description = (
        "Create a new file or overwrite an existing file with the given content. "
        "Parent directories are created automatically. Use for implementing new files."
    )
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file from the repository root.",
            },
            "content": {
                "type": "string",
                "description": "Complete file content to write.",
            },
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, path: str = "", content: str = "", **kw: Any) -> str:
        full = os.path.join(self.workspace, path)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            lines = content.count("\n") + 1
            return f"✅ Wrote {lines} lines to {path}"
        except Exception as e:
            return f"Error writing {path}: {e}"


class EditFileTool(Tool):
    """Precise line-level editing of existing files."""

    name = "edit_file"
    description = (
        "Edit an existing file by replacing a specific string with new content. "
        "Provide enough context (3+ lines before and after) to uniquely identify "
        "the location. The old_string must match exactly (including whitespace)."
    )
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file from the repository root.",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find and replace. Include 3+ lines of context.",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text. Maintain correct indentation.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, path: str = "", old_string: str = "", new_string: str = "", **kw: Any) -> str:
        full = os.path.join(self.workspace, path)
        if not os.path.isfile(full):
            return self._not_found(path)
        try:
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
            count = content.count(old_string)
            if count == 0:
                return f"Error: old_string not found in {path}. Check whitespace and context."
            if count > 1:
                return f"Error: old_string matches {count} locations in {path}. Add more context."
            new_content = content.replace(old_string, new_string, 1)
            with open(full, "w", encoding="utf-8") as f:
                f.write(new_content)
            return f"✅ Edited {path} (replaced 1 occurrence)"
        except Exception as e:
            return f"Error editing {path}: {e}"


class DeleteFileTool(Tool):
    """Delete a file from the workspace."""

    name = "delete_file"
    description = (
        "Delete a file from the repository. Use for removing obsolete files, "
        "cleaning up temporary files, or as part of a refactoring."
    )
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file to delete.",
            },
        },
        "required": ["path"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, path: str = "", **kw: Any) -> str:
        full = os.path.join(self.workspace, path)
        if not os.path.isfile(full):
            return self._not_found(path)
        # Safety: don't allow deleting outside workspace
        real_workspace = os.path.realpath(self.workspace)
        real_target = os.path.realpath(full)
        if not real_target.startswith(real_workspace):
            return "Error: cannot delete files outside the workspace"
        try:
            os.remove(full)
            return f"\u2705 Deleted {path}"
        except Exception as e:
            return f"Error deleting {path}: {e}"


class LsTool(Tool):
    """List directory contents."""

    name = "ls"
    description = "List files and directories at the given path. Returns names with / suffix for dirs."
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative directory path. Default: '.' (repository root).",
            }
        },
        "required": [],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, path: str = ".", **kw: Any) -> str:
        full = os.path.join(self.workspace, path)
        if not os.path.isdir(full):
            return f"Error: not a directory: {path}"
        try:
            entries = sorted(os.listdir(full))
            result = []
            for e in entries:
                if e.startswith("."):
                    continue  # Skip hidden files for cleaner output
                fp = os.path.join(full, e)
                result.append(f"{e}/" if os.path.isdir(fp) else e)
            return "\n".join(result) or "(empty directory)"
        except Exception as e:
            return f"Error listing {path}: {e}"


class GlobTool(Tool):
    """Find files matching a glob pattern."""

    name = "glob"
    description = "Find files matching a glob pattern (e.g., '**/*.py', 'src/**/*.ts')."
    parameters = {
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern to match files.",
            }
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, pattern: str = "", **kw: Any) -> str:
        try:
            matches = sorted(Path(self.workspace).glob(pattern))
            # Filter out .git directory
            matches = [m for m in matches if ".git" not in m.parts]
            if not matches:
                return f"No files matching: {pattern}"
            rel = [str(m.relative_to(self.workspace)) for m in matches[:100]]
            result = "\n".join(rel)
            if len(matches) > 100:
                result += f"\n... and {len(matches) - 100} more"
            return result
        except Exception as e:
            return f"Error: {e}"


class GrepTool(Tool):
    """Search for patterns in files."""

    name = "grep"
    description = (
        "Search for a text pattern in files. Returns matching lines with file paths "
        "and line numbers. Supports regex."
    )
    parameters = {
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Search pattern (regex supported).",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in. Default: '.'",
            },
            "include": {
                "type": "string",
                "description": "File pattern to include (e.g., '*.py', '*.ts'). Default: all files.",
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, pattern: str = "", path: str = ".", include: str = "", **kw: Any) -> str:
        # Try git grep first (cross-platform, works on Windows with Git)
        cmd = f'git grep -rn --no-color'
        if include:
            cmd += f' -- "{include}"'
        # Build the full command
        full_cmd = f'git grep -rn --no-color -e "{pattern}" -- "{path}"'
        if include:
            full_cmd = f'git grep -rn --no-color -e "{pattern}" -- "{path}/{include}"'
        try:
            result = subprocess.run(
                full_cmd,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
                errors="replace",
            )
            output = (result.stdout or "").strip()
            if not output:
                # Fallback: Python-based search
                return self._python_grep(pattern, path, include)
            lines = output.split("\n")
            if len(lines) > 100:
                output = "\n".join(lines[:100]) + f"\n... and {len(lines) - 100} more matches"
            return output
        except subprocess.TimeoutExpired:
            return "Error: grep timed out"
        except Exception:
            # Fallback: Python-based search
            return self._python_grep(pattern, path, include)

    def _python_grep(self, pattern: str, path: str, include: str) -> str:
        """Pure-Python fallback grep for Windows compatibility."""
        import re
        import fnmatch
        results: list[str] = []
        search_root = os.path.join(self.workspace, path)
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = re.compile(re.escape(pattern))
        for root, dirs, files in os.walk(search_root):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".venv")]
            for fname in files:
                if include and not fnmatch.fnmatch(fname, include):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, self.workspace).replace("\\", "/")
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(f"{rel}:{i}:{line.rstrip()}")
                                if len(results) >= 100:
                                    results.append("... (truncated at 100 matches)")
                                    return "\n".join(results)
                except (PermissionError, OSError):
                    pass
        return "\n".join(results) if results else f"No matches for pattern: {pattern}"


class TreeTool(Tool):
    """Show directory structure as a tree."""

    name = "tree"
    description = "Display directory structure as a tree. Limited to 3 levels deep by default."
    parameters = {
        "properties": {
            "path": {
                "type": "string",
                "description": "Root path for the tree. Default: '.'",
            },
            "depth": {
                "type": "integer",
                "description": "Maximum depth to display. Default: 3.",
            },
        },
        "required": [],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, path: str = ".", depth: int = 3, **kw: Any) -> str:
        full = os.path.join(self.workspace, path)
        lines: list[str] = []
        self._walk(full, "", int(depth), lines)
        if not lines:
            return "(empty)"
        return "\n".join(lines[:200])

    def _walk(self, root: str, prefix: str, depth: int, lines: list[str]) -> None:
        if depth < 0 or len(lines) > 200:
            return
        try:
            entries = sorted(os.listdir(root))
            entries = [e for e in entries if not e.startswith(".")]
            for i, name in enumerate(entries):
                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "
                fp = os.path.join(root, name)
                if os.path.isdir(fp):
                    lines.append(f"{prefix}{connector}{name}/")
                    extension = "    " if is_last else "│   "
                    self._walk(fp, prefix + extension, depth - 1, lines)
                else:
                    lines.append(f"{prefix}{connector}{name}")
        except PermissionError:
            pass


class FindDefinitionTool(Tool):
    """Find where a symbol is defined in the codebase."""

    name = "find_definition"
    description = (
        "Search for the definition of a function, class, or variable in the codebase. "
        "Uses grep with common definition patterns (def, class, function, const, etc.)."
    )
    parameters = {
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Name of the function, class, or variable to find.",
            },
            "language": {
                "type": "string",
                "description": "Programming language hint: python, typescript, javascript, go, rust, java.",
            },
        },
        "required": ["symbol"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, symbol: str = "", language: str = "", **kw: Any) -> str:
        patterns = [
            f"def {symbol}",
            f"class {symbol}",
            f"function {symbol}",
            f"const {symbol}",
            f"let {symbol}",
            f"var {symbol}",
            f"func {symbol}",
            f"fn {symbol}",
            f"type {symbol}",
            f"interface {symbol}",
            f"struct {symbol}",
        ]
        combined = "|".join(patterns)
        # Try git grep first (cross-platform)
        try:
            result = subprocess.run(
                f'git grep -rn -E "{combined}" -- "*.py" "*.ts" "*.js" "*.go" "*.rs" "*.java" "*.yml"',
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
                errors="replace",
            )
            output = (result.stdout or "").strip()
            if output:
                lines = output.split("\n")
                lines = [l for l in lines if ".git/" not in l and "node_modules/" not in l]
                return "\n".join(lines[:30]) or f"No definition found for: {symbol}"
        except Exception:
            pass

        # Fallback: Python-based search
        import re
        results: list[str] = []
        try:
            regex = re.compile("|".join(re.escape(p) for p in patterns))
        except re.error:
            regex = re.compile(re.escape(symbol))
        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".venv")]
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, self.workspace).replace("\\", "/")
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(f"{rel}:{i}:{line.rstrip()}")
                                if len(results) >= 30:
                                    return "\n".join(results)
                except (PermissionError, OSError):
                    pass
        return "\n".join(results) if results else f"No definition found for: {symbol}"


class GitDiffTool(Tool):
    """View git diff."""

    name = "git_diff"
    description = "Show git diff for a specific commit, branch comparison, or staged/unstaged changes."
    parameters = {
        "properties": {
            "args": {
                "type": "string",
                "description": "Git diff arguments. Examples: 'HEAD~1', '--staged', 'main...feature', 'HEAD -- path/file.py'",
            }
        },
        "required": [],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, args: str = "", **kw: Any) -> str:
        cmd = f"git diff {args}".strip()
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
                errors="replace",
            )
            output = (result.stdout or "").strip()
            if not output:
                return "(no diff)"
            if len(output) > 15000:
                output = (
                    output[:6000]
                    + "\n\n… [diff truncated — showing first 6K and last 6K chars] …\n\n"
                    + output[-6000:]
                )
            return output
        except Exception as e:
            return f"Error: {e}"


class GitLogTool(Tool):
    """View git log."""

    name = "git_log"
    description = "Show git commit history. Defaults to last 20 commits in oneline format."
    parameters = {
        "properties": {
            "args": {
                "type": "string",
                "description": "Git log arguments. Examples: '--oneline -20', '--oneline --all --graph -30', '-1 -p'",
            }
        },
        "required": [],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, args: str = "--oneline -20", **kw: Any) -> str:
        cmd = f"git log {args}".strip()
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
                errors="replace",
            )
            return (result.stdout or "").strip() or "(no commits)"
        except Exception as e:
            return f"Error: {e}"


class GitShowTool(Tool):
    """Show a specific git commit."""

    name = "git_show"
    description = "Show details of a specific git commit (message, author, diff)."
    parameters = {
        "properties": {
            "ref": {
                "type": "string",
                "description": "Git ref to show (commit hash, HEAD, tag, etc.).",
            }
        },
        "required": ["ref"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, ref: str = "HEAD", **kw: Any) -> str:
        try:
            result = subprocess.run(
                ["git", "show", "--stat", ref],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
                errors="replace",
            )
            return (result.stdout or "").strip() or f"(no commit at {ref})"
        except Exception as e:
            return f"Error: {e}"


# ──────────────────────────────────────────────────────────────────────
# Cross-repo tools — read files/code from other repositories
# ──────────────────────────────────────────────────────────────────────

class CrossRepoReadTool(Tool):
    """Read a file from another repository (read-only)."""

    name = "cross_repo_read"
    description = (
        "Read a file from another GitHub repository that you have access to. "
        "Useful for checking how other projects in the organisation implement "
        "similar patterns, shared libraries, or configuration files. "
        "Only use this when the current repo doesn't have enough context."
    )
    parameters = {
        "properties": {
            "repo": {
                "type": "string",
                "description": "Full repo name: owner/repo (e.g. 'my-org/shared-lib')",
            },
            "path": {
                "type": "string",
                "description": "File path within the repo (e.g. 'src/utils.py')",
            },
            "ref": {
                "type": "string",
                "description": "Branch, tag, or SHA. Default: repo default branch.",
            },
        },
        "required": ["repo", "path"],
    }

    def __init__(self, gh: Any) -> None:
        self._gh = gh

    def execute(self, repo: str = "", path: str = "", ref: str = "", **kw: Any) -> str:
        if not repo or not path:
            return "Error: 'repo' and 'path' are required"
        try:
            content = self._gh.get_repo_file(repo, path, ref=ref)
            lines = content.split("\n")
            if len(lines) > 500:
                content = "\n".join(lines[:500]) + f"\n\n... [{len(lines)} lines total — truncated]"
            return f"# {repo}:{path}\n\n{content}"
        except Exception as e:
            return f"Error reading {repo}:{path} — {e}"


class CrossRepoBrowseTool(Tool):
    """List files in a directory of another repository."""

    name = "cross_repo_ls"
    description = (
        "List files and directories in another GitHub repository. "
        "Use this to explore the structure of related projects."
    )
    parameters = {
        "properties": {
            "repo": {
                "type": "string",
                "description": "Full repo name: owner/repo",
            },
            "path": {
                "type": "string",
                "description": "Directory path (empty string or '/' for repo root). Default: root.",
            },
        },
        "required": ["repo"],
    }

    def __init__(self, gh: Any) -> None:
        self._gh = gh

    def execute(self, repo: str = "", path: str = "", **kw: Any) -> str:
        if not repo:
            return "Error: 'repo' is required"
        try:
            entries = self._gh.list_repo_dir(repo, path)
            return f"# {repo}:{path or '/'}\n\n" + "\n".join(entries)
        except Exception as e:
            return f"Error listing {repo}:{path} — {e}"


class CrossRepoSearchTool(Tool):
    """Search code in another repository."""

    name = "cross_repo_search"
    description = (
        "Search for code patterns in another GitHub repository. "
        "Uses GitHub code search. Returns matching file paths and snippets."
    )
    parameters = {
        "properties": {
            "repo": {
                "type": "string",
                "description": "Full repo name: owner/repo",
            },
            "query": {
                "type": "string",
                "description": "Search query (code text, function name, etc.)",
            },
        },
        "required": ["repo", "query"],
    }

    def __init__(self, gh: Any) -> None:
        self._gh = gh

    def execute(self, repo: str = "", query: str = "", **kw: Any) -> str:
        if not repo or not query:
            return "Error: 'repo' and 'query' are required"
        try:
            hits = self._gh.search_repo_code(repo, query)
            if not hits:
                return f"No results for '{query}' in {repo}"
            lines = [f"Found {len(hits)} result(s) in {repo}:\n"]
            for h in hits:
                lines.append(f"**{h['path']}**:\n```\n{h['fragment']}\n```\n")
            return "\n".join(lines)
        except Exception as e:
            return f"Error searching {repo}: {e}"


def register_cross_repo_tools(registry: "ToolRegistry", gh: Any) -> None:
    """Register cross-repo tools. Call from forge_agent when PAT is available."""
    registry.register(CrossRepoReadTool(gh))
    registry.register(CrossRepoBrowseTool(gh))
    registry.register(CrossRepoSearchTool(gh))


# ──────────────────────────────────────────────────────────────────────
# GitHub interaction tools — agent-driven, no hardcoded rules
# ──────────────────────────────────────────────────────────────────────

class GitHubCommentTool(Tool):
    """
    Post a comment on any PR or issue.

    The agent decides WHEN and WHAT to comment — no hardcoded rules.
    Use cases: ask clarifying questions, share findings mid-review,
    post progress updates, reply to existing discussion, etc.
    """

    name = "github_comment"
    description = (
        "Post a comment on a GitHub pull request or issue. Use this freely — "
        "share findings, ask clarifying questions, post progress updates, "
        "reply to discussion threads, or provide interim feedback. "
        "You decide when and what to comment — there are no hardcoded rules."
    )
    parameters = {
        "type": "object",
        "properties": {
            "number": {
                "type": "integer",
                "description": "PR or issue number to comment on",
            },
            "body": {
                "type": "string",
                "description": "Comment body (Markdown supported). Sign off with a brief note that this is from Forge.",
            },
        },
        "required": ["number", "body"],
    }

    def __init__(self, gh: Any) -> None:
        self._gh = gh

    def execute(self, number: int = 0, body: str = "", **kw: Any) -> str:
        if not number or not body:
            return "Error: both 'number' and 'body' are required"
        try:
            self._gh.post_issue_comment(number, body)
            return f"✅ Comment posted on #{number}"
        except Exception as e:
            return f"Error posting comment: {e}"


class GitHubReadCommentsTool(Tool):
    """
    Read existing comments on a PR or issue.

    Lets the agent understand ongoing discussion before responding.
    """

    name = "github_read_comments"
    description = (
        "Read existing comments on a GitHub pull request or issue. "
        "Use this to understand ongoing discussions, check if questions "
        "have already been answered, or review feedback from maintainers."
    )
    parameters = {
        "type": "object",
        "properties": {
            "number": {
                "type": "integer",
                "description": "PR or issue number to read comments from",
            },
            "last_n": {
                "type": "integer",
                "description": "Number of most recent comments to return (default: 10)",
            },
        },
        "required": ["number"],
    }

    def __init__(self, gh: Any) -> None:
        self._gh = gh

    def execute(self, number: int = 0, last_n: int = 10, **kw: Any) -> str:
        if not number:
            return "Error: 'number' is required"
        try:
            comments = self._gh.get_issue_comments(number)
            comments = comments[-last_n:]
            if not comments:
                return f"No comments on #{number}"
            lines: list[str] = []
            for c in comments:
                lines.append(
                    f"**@{c.user.login}** ({c.created_at.isoformat()}):\n{c.body[:500]}"
                )
            return "\n\n---\n\n".join(lines)
        except Exception as e:
            return f"Error reading comments: {e}"



# ──────────────────────────────────────────────────────────────────────
# Test execution tool
# ──────────────────────────────────────────────────────────────────────

class RunTestsTool(Tool):
    """Run the project's test suite with auto-detection and structured output."""

    name = "run_tests"
    description = (
        "Run the project's test suite. Auto-detects the test command from "
        "project config files (package.json, pyproject.toml, Makefile, Cargo.toml). "
        "Returns structured results: pass/fail counts, failure details. "
        "Use AFTER making changes to verify they don't break anything. Timeout: 300s."
    )
    parameters = {
        "properties": {
            "scope": {
                "type": "string",
                "description": (
                    "Test scope: 'all' runs full suite, 'path' runs a specific test file/dir."
                ),
            },
            "path": {
                "type": "string",
                "description": "Specific test file/directory to run (when scope='path').",
            },
        },
        "required": [],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute(self, scope: str = "all", path: str = "", **kw: Any) -> str:
        cmd = self._detect_test_command()
        if not cmd:
            return "No test command detected. Looked for: package.json, pyproject.toml, Makefile, Cargo.toml, pytest.ini"

        if scope == "path" and path:
            # Append specific path for pytest/jest
            if "pytest" in cmd:
                cmd = f"{cmd} {path}"
            elif "npm test" in cmd or "jest" in cmd:
                cmd = f"{cmd} -- {path}"
            else:
                cmd = f"{cmd} {path}"

        print(f"  🧪 Running: {cmd}")
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=300,
                errors="replace",
                env={**os.environ, "CI": "true", "FORCE_COLOR": "0"},
            )
        except subprocess.TimeoutExpired:
            return "⚠️ Tests timed out after 300 seconds."
        except Exception as e:
            return f"⚠️ Failed to run tests: {e}"

        output = (result.stdout or "") + "\n" + (result.stderr or "")
        output = output.strip()

        # Truncate large output (head/tail)
        if len(output) > 12000:
            output = output[:6000] + "\n\n… [truncated] …\n\n" + output[-6000:]

        passed = result.returncode == 0
        status = "✅ PASSED" if passed else "❌ FAILED"

        return f"{status} (exit code {result.returncode})\n\nCommand: {cmd}\n\n{output}"

    def _detect_test_command(self) -> str:
        """Auto-detect the appropriate test command for this project."""
        ws = self.workspace

        # Python: pyproject.toml or pytest.ini
        if os.path.isfile(os.path.join(ws, "pyproject.toml")):
            return "python -m pytest -x --tb=short -q"
        if os.path.isfile(os.path.join(ws, "pytest.ini")):
            return "python -m pytest -x --tb=short -q"
        if os.path.isfile(os.path.join(ws, "setup.cfg")):
            try:
                with open(os.path.join(ws, "setup.cfg"), "r") as f:
                    if "[tool:pytest]" in f.read():
                        return "python -m pytest -x --tb=short -q"
            except Exception:
                pass

        # Node.js: package.json with test script
        pkg_json = os.path.join(ws, "package.json")
        if os.path.isfile(pkg_json):
            try:
                with open(pkg_json, "r") as f:
                    pkg = json.loads(f.read())
                if "test" in pkg.get("scripts", {}):
                    return "npm test"
            except Exception:
                pass

        # Rust: Cargo.toml
        if os.path.isfile(os.path.join(ws, "Cargo.toml")):
            return "cargo test"

        # Go: go.mod
        if os.path.isfile(os.path.join(ws, "go.mod")):
            return "go test ./..."

        # Makefile with test target
        makefile = os.path.join(ws, "Makefile")
        if os.path.isfile(makefile):
            try:
                with open(makefile, "r") as f:
                    content = f.read()
                if "\ntest:" in content or content.startswith("test:"):
                    return "make test"
            except Exception:
                pass

        # Fallback: look for tests/ directory with Python files
        if os.path.isdir(os.path.join(ws, "tests")):
            return "python -m pytest tests/ -x --tb=short -q"

        return ""


# ──────────────────────────────────────────────────────────────────────
# Tool Registry
# ──────────────────────────────────────────────────────────────────────

class ToolRegistry:
    """Registry of all tools available to the Forge agent."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self._tools: dict[str, Tool] = {}

        # Register all tools
        self._register(ThinkTool())
        self._register(ExecuteTool(workspace))
        self._register(ReadFileTool(workspace))
        self._register(WriteFileTool(workspace))
        self._register(EditFileTool(workspace))
        self._register(DeleteFileTool(workspace))
        self._register(LsTool(workspace))
        self._register(GlobTool(workspace))
        self._register(GrepTool(workspace))
        self._register(TreeTool(workspace))
        self._register(FindDefinitionTool(workspace))
        self._register(GitDiffTool(workspace))
        self._register(GitLogTool(workspace))
        self._register(GitShowTool(workspace))
        self._register(RunTestsTool(workspace))

    def _register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register(self, tool: Tool) -> None:
        """Public method to register additional tools after init."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def declarations(self) -> list[dict[str, Any]]:
        """Return all tool declarations for Gemini function calling."""
        return [t.declaration() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def execute(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool by name with given arguments."""
        tool = self._tools.get(name)
        if not tool:
            return f"Error: unknown tool '{name}'. Available: {', '.join(self.names())}"
        try:
            return tool.execute(**args)
        except Exception as e:
            return f"Error executing {name}: {e}"
