# Team Coding Patterns

> This file teaches Forge your team's conventions. Place it at `.github/CODING_PATTERNS.md`
> in your repository. Forge reads this before every PR review.
>
> This version is a standardized enterprise Python baseline — the conventions most
> professional Python teams converge on. Treat it as a starting point: replace or
> extend sections with your team's actual decisions where they differ.

## Code Style

- Follow PEP 8; enforce it with a formatter (Black or Ruff format), not by hand.
- Line length 88–100 characters (formatter default), not a hard 79.
- Type hints on every public function signature. Prefer `X | None` over `Optional[X]` (Python 3.10+).
- Docstrings on every public module, class, and function (Google or NumPy style — pick one, stay consistent). Skip docstrings only on trivial private helpers where the name says everything.
- No bare `except:` — always catch a specific exception type.
- No mutable default arguments (`def f(x=[])`) — use `None` and initialize inside the function.
- Prefer composition over inheritance; avoid deep class hierarchies.

## SOLID Principles

- **Single Responsibility** — a class or module has one reason to change. If a code review comment is "this does too many things," that's a SRP violation, not a style nit.
- **Open/Closed** — extend behavior via new classes/functions, not by editing working code to bolt on a special case. Watch for functions accumulating `if isinstance(...)` branches over time.
- **Liskov Substitution** — a subclass must be usable anywhere its parent is expected, with no surprising behavior changes or narrowed contracts.
- **Interface Segregation** — prefer several small, focused interfaces (Protocols/ABCs) over one large interface that forces implementers to stub out methods they don't need.
- **Dependency Inversion** — depend on abstractions (interfaces, injected dependencies), not concrete implementations. Business logic should not import a specific database driver, HTTP client, or vendor SDK directly — inject it.

## DRY (Don't Repeat Yourself)

- Three or more near-identical code blocks is the signal to extract a shared function — not the second one; two similar blocks are often still fine as-is (premature abstraction has its own cost).
- Shared constants and config values live in one place, not copy-pasted across modules.
- Duplicated logic across services should move to a shared internal library, not be re-implemented per-service.
- DRY applies to logic, not incidental similarity — don't force two conceptually different things into one abstraction just because they currently look alike.

## Naming Conventions

- Classes: `PascalCase`
- Functions, variables, methods: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private/internal: leading underscore (`_helper`), not name-mangled `__double_underscore` unless you specifically need mangling
- Modules and packages: short, lowercase, `snake_case`, no abbreviations that aren't obvious
- Boolean variables/functions read as a predicate: `is_valid`, `has_permission`, `should_retry`

## Project Structure

- Source under `src/<package_name>/`, tests under `tests/`, mirroring the source layout.
- One class per file for substantial classes; small, related helpers can share a module.
- No business logic in `__init__.py` beyond re-exports.
- Configuration (env vars, settings) centralized in one module, loaded once, not read from `os.environ` scattered throughout the codebase.

## Testing

- Every new feature or bug fix ships with a test. A bug fix without a regression test is incomplete.
- Unit tests are isolated — no real network calls, no real database, no filesystem side effects. Mock external dependencies at the boundary.
- Test names describe behavior: `test_returns_empty_list_when_no_matches`, not `test_1`.
- Arrange–Act–Assert structure per test; one logical assertion focus per test.
- Target meaningful coverage on business logic, not 100% coverage as a vanity metric — untested error paths and edge cases matter more than untested boilerplate.

## Error Handling

- Fail loudly on programmer errors (bad arguments, invariant violations); fail gracefully on expected runtime conditions (network timeout, missing file, bad user input).
- Custom exception classes for domain errors, inheriting from a project-specific base exception — not raising bare `Exception` or `ValueError` everywhere.
- Never swallow an exception silently (`except: pass`). At minimum, log it with context.
- Log errors with structured context (what operation, what input, what failed) — not just the exception message.
- Validate at system boundaries (API input, external responses); trust internal function contracts once validated.

## API Design

- REST endpoints use nouns and HTTP verbs correctly (`GET /users/{id}`, not `GET /getUser`).
- Consistent request/response shapes across endpoints, including a consistent error response shape (e.g. `{"error": {"code": ..., "message": ...}}`).
- Validate all external input with a schema library (Pydantic, Zod, etc.) — don't hand-roll validation.
- Version breaking API changes explicitly; don't silently change a response shape consumers depend on.

## Git Conventions

- Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `feat!:` for breaking changes).
- PR titles match the same `type: description` convention.
- One logical change per PR — a PR that mixes a refactor with a feature is harder to review and harder to revert.
- No direct commits to the default branch; all changes go through PR review.

## Security

- Never commit secrets, credentials, or API keys — use environment variables or a secrets manager, and verify `.gitignore` covers local env files.
- Validate and sanitize all user input; never interpolate raw input into SQL, shell commands, or HTML output.
- Use parameterized queries for all database access — no string-built SQL.
- Least privilege by default — a service account, token, or role should have only the permissions it actually needs.
- Dependencies are kept current; known-vulnerable versions get patched, not ignored.
