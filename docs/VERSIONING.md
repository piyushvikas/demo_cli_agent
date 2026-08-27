# Versioning

ops-factory uses semantic versioning with automatic releases based on conventional commits.

## Version Format

```
vMAJOR.MINOR.PATCH
```

- **MAJOR**: Breaking changes (v1.0.0 → v2.0.0)
- **MINOR**: New features, backward compatible (v1.0.0 → v1.1.0)
- **PATCH**: Bug fixes, documentation (v1.0.0 → v1.0.1)

## Conventional Commits

Use this format for commit messages:

```
<type>: <description>
```

### Commit Types and Version Bumps

| Type | Bump | Example |
|------|------|---------|
| `feat!:` | Major | `feat!: change action input structure` |
| `feat:` | Minor | `feat: add support for new region` |
| `fix:` | Patch | `fix: resolve timeout issue` |
| `docs:` | Patch | `docs: update examples` |
| `chore:` | Patch | `chore: update dependencies` |
| `refactor:` | Patch | `refactor: simplify auth logic` |

### Examples

```bash
# Patch release (v0.1.0 → v0.1.1)
git commit -m "fix: handle empty response in schema migration"

# Minor release (v0.1.1 → v0.2.0)
git commit -m "feat: add epoch_count parameter to fine-tuning"

# Major release (v0.2.0 → v1.0.0)
git commit -m "feat!: migrate to Document AI v2 API

BREAKING CHANGE: removed legacy_mode input"
```

## Release Process

1. Create feature branch from `master`
2. Make changes and commit with conventional messages
3. Open PR to `master`
4. After merge, auto-release workflow:
   - Analyzes commit messages
   - Calculates version bump
   - Creates tag (e.g., `v0.2.0`)
   - Creates GitHub release
   - Updates major version pointer (`v0`)

## Using Versions

```yaml
# Recommended: Use major version for automatic updates
uses: piyushvikas/demo_cli_agent/.github/workflows/forge-review.yml@v0

# Strict: Pin to specific version
uses: piyushvikas/demo_cli_agent/.github/workflows/forge-review.yml@v0.1.0
```

## Version Lifecycle

| Phase | Version | Stability |
|-------|---------|-----------|
| Pre-release | v0.x.x | API may change |
| Stable | v1.x.x | Production ready, stable API |
