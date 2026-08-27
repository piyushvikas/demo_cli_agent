# ops-factory

Centralized CI/CD repository for reusable GitHub Actions and workflows. --This is just for pushing the code 

> **New to CI/CD?** Start with our [Getting Started Guide](docs/GETTING-STARTED.md) - a beginner-friendly introduction to GitHub Actions and how to use ops-factory.

## Available Actions

| Action | Description |
|--------|-------------|
| `actions/forge` | 🔨 AI coding assistant — PR reviews |

## Available Workflows

| Workflow | Trigger | Description |
|----------|---------|-------------|
| `forge-review.yml` | `workflow_call` | 🔨 AI-powered PR code review |
| `secret-scan.yml` | `workflow_call` | 🔒 Deterministic secret/credential scanning (gitleaks) |

## Usage

See [docs/examples](docs/examples/) for copy-paste ready workflow examples.

## Versioning

This repository uses semantic versioning with conventional commits. See [docs/VERSIONING.md](docs/VERSIONING.md) for details.

| Version | Usage |
|---------|-------|
| `@v0` | Latest pre-stable release (recommended for now) |
| `@v0.1.0` | Specific version (for strict version pinning) |
| `@v1` | Latest stable release (when available) |

## Repository Structure

```
ops-factory/
├── .github/workflows/     # Auto-release + Reusable workflows
├── actions/               # Composite actions
│   └── forge/             # 🔨 AI coding assistant
└── docs/
    ├── FORGE.md           # 🔨 Forge documentation
    ├── VERSIONING.md
    └── examples/
```

## OpenAI Authentication

Forge authenticates to the OpenAI API using an API key:

```yaml
secrets:
  OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

## Required Secrets

| Secret | Required For | Notes |
|--------|--------------|-------|
| `OPENAI_API_KEY` | Forge (OpenAI API access) | From [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |

---

## 🔨 Forge — AI Coding Assistant

Forge is an OpenAI-powered AI agent that reviews PRs and can peek at other repos in your org for wider context.

### Add Forge to any repo in 1 minute

Create **one file** — `.github/workflows/forge.yml`:

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

### Required Secrets (set once at org level)

| Secret | Purpose |
|--------|---------|
| `OPENAI_API_KEY` | OpenAI API access |
| `FORGE_PAT` | GitHub PAT — cross-repo reads |

### How it works

1. **Open a PR** → Forge reviews it automatically

📖 **[Full Forge Documentation](docs/FORGE.md)**