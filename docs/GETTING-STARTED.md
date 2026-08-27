# Getting Started with CI/CD

A beginner-friendly guide to understanding CI/CD and using ops-factory.

## Table of Contents

1. [What is CI/CD?](#what-is-cicd)
2. [How GitHub Actions Works](#how-github-actions-works)
3. [Understanding YAML](#understanding-yaml)
4. [Creating Your First Workflow](#creating-your-first-workflow)
5. [Setting Up Secrets](#setting-up-secrets)
6. [Using ops-factory Actions](#using-ops-factory-actions)
7. [Common Patterns](#common-patterns)

---

## What is CI/CD?

**CI/CD** stands for Continuous Integration and Continuous Deployment.

- **Continuous Integration (CI)**: Automatically building and testing your code every time you push changes
- **Continuous Deployment (CD)**: Automatically deploying your code to cloud services (like GCP) after it passes tests

**Why use it?**

| Without CI/CD | With CI/CD |
|---------------|------------|
| Manual builds | Automatic builds on every push |
| "Works on my machine" problems | Consistent build environment |
| Manual deployments | One-click or automatic deployments |
| Easy to forget steps | Every step is documented and repeatable |

---

## How GitHub Actions Works

GitHub Actions is GitHub's built-in CI/CD system. Here's how it works:

```
You push code
    |
    v
GitHub detects the push
    |
    v
GitHub looks for workflow files in .github/workflows/
    |
    v
GitHub runs the workflow on a virtual machine (called a "runner")
    |
    v
You see the results in the "Actions" tab
```

### Key Concepts

| Term | What it means |
|------|---------------|
| **Workflow** | A YAML file that defines what to do (lives in `.github/workflows/`) |
| **Job** | A set of steps that run on the same machine |
| **Step** | A single task (run a command, use an action, etc.) |
| **Action** | A reusable piece of automation (like a function you can call) |
| **Runner** | The virtual machine that runs your workflow |
| **Trigger** | What causes the workflow to run (push, PR, manual, etc.) |

---

## Understanding YAML

Workflow files use YAML format (`.yml` or `.yaml` - both work the same).

### YAML Basics

YAML uses **indentation** (spaces, not tabs) to show structure:

```yaml
# This is a comment

# Simple key-value
name: My Workflow

# Nested structure (use 2 spaces for indentation)
person:
  name: John
  age: 30

# List of items (use dash and space)
fruits:
  - apple
  - banana
  - orange

# List of objects
people:
  - name: Alice
    role: Developer
  - name: Bob
    role: Designer
```

### Common Mistakes to Avoid

```yaml
# WRONG - tabs instead of spaces
name:	My Workflow

# WRONG - inconsistent indentation
person:
  name: John
   age: 30

# WRONG - missing space after colon
name:My Workflow

# WRONG - missing space after dash
fruits:
  -apple
```

---

## Creating Your First Workflow

### Step 1: Create the Folder Structure

In your repository, create this folder structure:

```
your-repo/
  .github/
    workflows/
      my-workflow.yml    <-- your workflow file goes here
```

### Step 2: Define the Workflow

Here's the simplest possible workflow:

```yaml
name: My First Workflow

on:
  push:
    branches:
      - main

jobs:
  hello:
    runs-on: ubuntu-latest
    steps:
      - name: Say Hello
        run: echo "Hello, World!"
```

Let's break this down:

| Line | What it does |
|------|--------------|
| `name: My First Workflow` | Names your workflow (shows in GitHub UI) |
| `on:` | Defines when this workflow runs |
| `push:` | Run when code is pushed |
| `branches: [main]` | Only on the main branch |
| `jobs:` | List of jobs to run |
| `hello:` | Name of this job |
| `runs-on: ubuntu-latest` | Use an Ubuntu Linux machine |
| `steps:` | List of steps in this job |
| `name: Say Hello` | Name of this step |
| `run: echo "Hello, World!"` | Shell command to run |

### Step 3: Commit and Push

Once you push this file to your repository:
1. Go to your repository on GitHub
2. Click the **Actions** tab
3. You'll see your workflow running

---

## Workflow Triggers

The `on:` section defines when your workflow runs:

### Push Trigger
```yaml
on:
  push:
    branches:
      - main
      - develop
```
Runs when you push to main or develop branch.

### Pull Request Trigger
```yaml
on:
  pull_request:
    branches:
      - main
```
Runs when someone opens a PR targeting main.

### Manual Trigger
```yaml
on:
  workflow_dispatch:
    inputs:
      environment:
        description: 'Target environment'
        required: true
        type: choice
        options:
          - development
          - production
```
Adds a "Run workflow" button in the Actions tab with input options.

### Scheduled Trigger
```yaml
on:
  schedule:
    - cron: '0 9 * * 1'  # Every Monday at 9 AM UTC
```

### Multiple Triggers
```yaml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:
```

---

## Setting Up Secrets

Secrets are encrypted values (passwords, API keys, etc.) that your workflows can use without exposing them in code.

### How to Add a Secret

1. Go to your repository on GitHub
2. Click **Settings**
3. In the left sidebar, click **Secrets and variables** then **Actions**
4. Click **New repository secret**
5. Enter a name (e.g., `MY_API_KEY`) and value
6. Click **Add secret**

### Using Secrets in Workflows

```yaml
steps:
  - name: Use a secret
    env:
      API_KEY: ${{ secrets.MY_API_KEY }}
    run: echo "Using secret (value hidden)"
```

### Environment-Specific Secrets

For different environments (dev, test, prod), use GitHub Environments:

1. Go to **Settings** then **Environments**
2. Create environments: `development`, `test`, `production`
3. Add secrets specific to each environment

Then in your workflow:
```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production  # Uses secrets from 'production' environment
```

### Common Secrets for ops-factory

| Secret Name | What it's for | Example Value |
|-------------|---------------|---------------|
| `OPENAI_API_KEY` | OpenAI API access for Forge | `sk-...` |
| `FORGE_PAT` | GitHub Personal Access Token for Forge | `ghp_xxxx...` |

---

## Using ops-factory Actions

ops-factory provides ready-to-use actions. Here's how to use them:

### Basic Structure

```yaml
steps:
  - uses: piyushvikas/demo_cli_agent/actions/ACTION_NAME@v0
    with:
      input_name: 'value'
      another_input: 'another value'
```

The `@v0` means "use the latest v0.x release" - this ensures your workflow won't break if we update the action (a future `@v1` will exist once the API is stable).

### Example: Forge AI PR Review

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

See [docs/FORGE.md](FORGE.md) for the full Forge documentation.

---

## Common Patterns

### Pattern 1: Run Only on Specific Files Changed

```yaml
on:
  push:
    paths:
      - 'src/**'        # Only if files in src/ changed
      - '!src/tests/**' # But not test files
```

### Pattern 2: Use Outputs from One Step in Another

```yaml
steps:
  - name: AI code review
    id: review
    uses: piyushvikas/demo_cli_agent/actions/forge@v0
    with:
      mode: 'review'
      openai_api_key: ${{ secrets.OPENAI_API_KEY }}
      # ... other inputs

  - name: Show result
    run: echo "Review recommendation: ${{ steps.review.outputs.recommendation }}"
```

### Pattern 3: Run Jobs in Sequence

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo "Building..."

  test:
    needs: build  # Wait for 'build' to complete
    runs-on: ubuntu-latest
    steps:
      - run: echo "Testing..."

  deploy:
    needs: test   # Wait for 'test' to complete
    runs-on: ubuntu-latest
    steps:
      - run: echo "Deploying..."
```

### Pattern 4: Conditional Steps

```yaml
steps:
  - name: Deploy to production
    if: github.ref == 'refs/heads/main'
    run: echo "Deploying to production"

  - name: Deploy to staging
    if: github.ref == 'refs/heads/develop'
    run: echo "Deploying to staging"
```

### Pattern 5: Matrix Builds (Run Same Job Multiple Times)

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12']
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python --version
```

---

## Troubleshooting

### Where to See Logs

1. Go to your repository on GitHub
2. Click the **Actions** tab
3. Click on the workflow run
4. Click on the failed job
5. Expand the failed step to see logs

### Common Errors

| Error | Likely Cause | Fix |
|-------|--------------|-----|
| `File not found` | Wrong path | Check paths are relative to repo root |
| `Permission denied` | Missing secrets or wrong service account | Verify secrets are set correctly |
| `Bad credentials` | Expired or wrong token | Regenerate the secret |
| `Workflow not found` | Typo in `uses:` line | Check spelling and version tag |

### Debugging Tips

1. Add debug output:
   ```yaml
   - run: |
       echo "Current directory: $(pwd)"
       echo "Files: $(ls -la)"
   ```

2. Check environment variables:
   ```yaml
   - run: env | sort
   ```

3. Enable debug logging: Add repository secret `ACTIONS_STEP_DEBUG` with value `true`

---

## Next Steps

1. Browse the [ops-factory actions](../actions/) to see what's available
2. Check [examples](examples/) for copy-paste ready workflows
3. Read [VERSIONING.md](VERSIONING.md) to understand our version strategy

Questions? Reach out to the ops-factory maintainers.
