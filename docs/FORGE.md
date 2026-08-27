# Forge - AI Coding Assistant

Forge is an OpenAI-powered coding assistant that **actively explores your codebase** using terminal commands before reviewing PRs. Unlike simple code review tools, Forge uses the **ReAct pattern** (Reasoning + Acting) to think, explore, and act iteratively.

## 🔥 What Makes Forge Different

| Traditional AI Review | Forge |
|----------------------|-------|
| Looks at PR diff only | **Explores entire codebase** with git, grep, find |
| No context awareness | **Understands your patterns** from existing code |
| Static analysis | **Runs commands** to verify, test, explore |
| One-shot generation | **ReAct loop**: Think → Act → Observe → Repeat |

### How Forge Explores

```
🔨 Forge AI Coding Assistant
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Iteration 1/15

💭 Thought
I need to understand the project structure and existing patterns before reviewing.
Let me explore the codebase.

⚡ execute({'command': 'find . -type f -name "*.py" | head -20'})

👁️ Observation
./src/main.py
./src/utils/helpers.py
./tests/test_main.py
...

💭 Thought
Found Python files. Let me check the existing code style and patterns.

⚡ read_file({'path': 'src/main.py', 'start_line': 1, 'end_line': 50})

👁️ Observation
1: """Main application module."""
2: from typing import Optional
3: 
4: def process_data(input: str) -> Optional[dict]:
...

💭 Thought
I see they use type hints and docstrings. Let me check if there are tests
for the changes in this PR.

⚡ grep({'pattern': 'test_.*process', 'path': 'tests/'})
...
```

## Quick Start

### Quick Start — One file, any repo

Create `.github/workflows/forge.yml` in your repo:

```yaml
name: Forge AI
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  review:
    uses: piyushvikas/demo_cli_agent/.github/workflows/forge-review.yml@v0
    secrets:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      FORGE_PAT: ${{ secrets.FORGE_PAT }}
```

That's it. One file, copy-paste, works on any repo.

### Required Secrets (set once at org level)

| Secret | Purpose |
|--------|---------|
| `OPENAI_API_KEY` | OpenAI API access |
| `FORGE_PAT` | GitHub PAT — cross-repo context (optional but recommended) |

> **Tip:** Set these as **organisation-level secrets** so every repo inherits them automatically. Then teams just add the one workflow file.

## 🛠️ Available Tools

Forge has access to these read-only tools while reviewing a PR:

| Tool | Description | Example |
|------|-------------|---------|
| `execute` | Run any shell command | `git log --oneline -10`, `npm test` |
| `read_file` | Read file contents | `read_file('src/main.py', 1, 100)` |
| `ls` | List directory | `ls('src/')` |
| `glob` | Find files by pattern | `glob('**/*.ts')` |
| `grep` | Search in files | `grep('TODO', '.', '*.py')` |
| `tree` | Directory structure | Project layout |
| `find_definition` | Find symbol definition | Function/class location |
| `git_diff` | View diffs | Compare changes |
| `git_log` | Commit history | Recent commits |
| `git_show` | Show commit | Specific commit details |
| `run_tests` | Run project test suite | Auto-detects pytest/jest/cargo, defaults to full suite |
| `github_comment` | Post on PR/issue | Share findings, ask questions |
| `github_read_comments` | Read discussion | Understand prior feedback |
| `cross_repo_read` | Read file from another repo | Check shared libs, patterns |
| `cross_repo_ls` | Browse another repo | Explore related projects |
| `cross_repo_search` | Search code in another repo | Find implementations |

## 🎯 PR Review Features

When reviewing a PR, Forge:

1. **Explores context** - Reads related files, checks git history
2. **Understands patterns** - Looks at how similar code is written
3. **Analyzes changes** - Evaluates the diff against best practices
4. **Posts comments** - Specific, line-by-line feedback with suggestions

### Review Output

```markdown
## 🔨 Forge Code Review

**Summary**: This PR adds user authentication but has a potential SQL injection vulnerability.

**Quality Score**: 6/10

### ✅ Positive Aspects
- Good use of existing patterns from auth module
- Tests added for happy path

### ⚠️ Key Concerns
- SQL injection risk in query construction (line 45)
- Missing error handling for token expiration
- No tests for edge cases

**Recommendation**: REQUEST_CHANGES
```

### Comment Format

```markdown
🚨 **CRITICAL** (security)

User input is directly interpolated into SQL query. This allows SQL injection attacks.

**Suggested fix:**
```suggestion
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```
```

Forge posts this as a real GitHub PR **review** — `APPROVE`, `REQUEST_CHANGES`, or `COMMENT` — with inline comments attached to specific lines. It does **not** merge PRs; merging still requires a human or your repo's own auto-merge setting.

## 📝 Team Coding Patterns

Create `.github/CODING_PATTERNS.md` to teach Forge your conventions:

```markdown
# Team Coding Patterns

## Code Style
- Use TypeScript strict mode
- Prefer functional components with hooks
- Use named exports, not default exports

## Naming
- Components: PascalCase (UserProfile.tsx)
- Utilities: camelCase (formatDate.ts)
- Constants: UPPER_SNAKE_CASE

## Testing
- Every feature must have unit tests
- Use React Testing Library
- Minimum 80% coverage
```

Forge reads this file and applies your patterns when reviewing. This is the easiest way to customize what Forge checks for — e.g. add a line like "flag any hardcoded API key or credential as CRITICAL" if you want it to pay closer attention to secrets.

## ⚙️ Configuration

### PR Review Options

| Input | Default | Description |
|-------|---------|-------------|
| `model_name` | `gpt-4o` | OpenAI model (`gpt-4o-mini` for cheaper/faster reviews) |
| `auto_approve` | `false` | Reserved — not currently wired to any behavior |
| `max_iterations` | `15` | ReAct loop iterations |
| `team_patterns_path` | `.github/CODING_PATTERNS.md` | Patterns file |

## 🔐 Required Secrets

| Secret | Required For | Description |
|--------|--------------|-------------|
| `OPENAI_API_KEY` | All modes | OpenAI API key |
| `FORGE_PAT` | Recommended | GitHub PAT — cross-repo reads |

### Creating an OpenAI API Key

1. [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Create a new secret key
3. Add as `OPENAI_API_KEY` secret in your repo (or org-level for all repos)

### Creating FORGE_PAT

1. GitHub Settings → Developer Settings → Personal Access Tokens
2. Generate token with `repo` scope
3. Add as `FORGE_PAT` secret

## 🧠 Technical Details

### ReAct Pattern

Forge uses the ReAct (Reasoning + Acting) pattern from AI research:

```
while not done:
    thought = model.think(context + trajectory)
    action, args = model.decide_action(thought)
    observation = execute_tool(action, args)
    trajectory.append({thought, action, observation})
```

This allows Forge to:
- **Reason** about what it needs to know
- **Act** by running commands
- **Learn** from observations
- **Iterate** until it has enough context

### Built on Best Practices

Inspired by:
- **DSPy** - Tool calling, structured outputs, ReAct pattern
- **DeepAgents** - Filesystem tools, execute command, exploration
- **LangGraph** - Workflow orchestration

## 🚀 Advanced Usage

### Using a Cheaper/Faster Model

`gpt-4o` is the default (stronger reasoning, better issue coverage). For lower
cost/latency on simple repos:

```yaml
with:
  model_name: 'gpt-4o-mini'
```

### More Exploration Iterations

```yaml
with:
  max_iterations: 25  # Default is 15
```

## 🔒 Security

- Forge runs **in your GitHub Actions environment**
- Code sent to **the OpenAI API** using your own API key (not routed through any other party)
- Terminal commands run **in isolated CI environment**
- All actions **logged in GitHub Actions**
- Forge's review checks for bugs, logic errors, and general security issues, but is **not** a dedicated secret scanner — pair it with `secret-scan.yml` (see below), a deterministic gitleaks-based job, rather than relying on Forge alone for hardcoded-secret detection.

## 🔒 Deterministic Secret Scanning

`secret-scan.yml` runs [gitleaks](https://github.com/gitleaks/gitleaks) — a
rule-based scanner, not an LLM — to catch hardcoded secrets/credentials with
guaranteed, repeatable detection. Add it alongside `forge-review.yml`:

```yaml
jobs:
  secret-scan:
    uses: piyushvikas/demo_cli_agent/.github/workflows/secret-scan.yml@v0
```

No secrets required — it only reads the repo. Fails the job (non-zero exit)
if it finds anything, so wire it as a required status check in branch
protection the same way you would the test suite.

## 🏗️ Architecture

Forge is built as a modular Python agent with clear separation of concerns:

```
actions/forge/
├── action.yml              # GitHub Actions composite action definition
├── requirements.txt        # Python dependencies
└── scripts/
    ├── forge_agent.py      # Entrypoint — config, dispatch, output handling
    ├── openai_client.py    # OpenAI client (chat completions + function calling)
    ├── react_loop.py       # Core ReAct loop (think → act → observe → repeat)
    ├── tools.py            # Tool registry (shell, file I/O, git, GitHub, cross-repo)
    ├── context_engine.py   # Context builder (PR diff, team patterns)
    ├── github_client.py    # GitHub API (PRs, comments, reactions)
    ├── forge_memory.py     # Persistent git-committed memory (.github/forge/memory.md)
    ├── mode_review.py      # PR review mode handler
    └── test_local.py       # Standalone local test harness (outside GitHub Actions)
```

### Design Principles

Inspired by studying the best open-source agent repos:

| Pattern | Source | How Forge Uses It |
|---------|--------|-------------------|
| ReAct loop with trajectory | **DSPy** | Flat trajectory with iteration tracking, two-phase (explore → extract) |
| Filesystem tools + shell exec | **DeepAgents** | Tools: file I/O, shell, git, GitHub, cross-repo |
| Safety guards | **LangChain** | max_iterations + wall-clock timeout + output truncation |
| Think tool | **Open Deep Research** | Explicit reasoning steps with no side effects |
| Context overflow handling | **DeepAgents** | Auto-compact trajectory when messages exceed 60 entries |
| Structured system prompts | **All** | Rich context: repo structure, team patterns, diff |

### Token Flow

```
User Prompt (task + diff)
        ↓
   System Prompt (patterns + structure + instructions)
        ↓
┌─── ReAct Loop ──────────────────────┐
│  Model thinks → picks tool → args   │
│  Tool executes → observation         │
│  Append to messages → repeat        │
│  (auto-compact if messages > 60)    │
└─────────────────────────────────────┘
        ↓
   Final extraction (no tools)
        ↓
   Post to GitHub (review)
```

## Contributing

See the [ops-factory contributing guide](../../README.md).
