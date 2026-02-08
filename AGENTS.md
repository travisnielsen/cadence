# AGENTS.md

Instructions for AI coding agents working on this repository.

## Environment

- Package manager: `uv` (not pip)
- Task runner: `uv run poe <task>`
- Task tracker: `bd` (beads)
- Python: 3.11+

## Commands

```bash
uv run poe check      # Run all quality checks before committing
uv run poe test       # Run tests with coverage
uv run poe typecheck  # basedpyright standard mode
uv run poe lint       # ruff with anti-slop rules
uv run poe metrics    # Check code quality: dead code, complexity, and maintainability
```

See [DEV_SETUP.md](DEV_SETUP.md) for all commands and [CODING_STANDARD.md](CODING_STANDARD.md) for code style.

## Agents

9 agents in `.github/agents/`. See [agents.instructions.md](.github/instructions/agents.instructions.md).

## Session Completion

Before ending a session:

1. **Run quality checks** - `uv run poe check` must pass
2. **Update tasks** - Close completed beads tasks with `bd close <id> --reason "Done"`
3. **Sync tasks** - Run `bd sync` to commit task changes
4. **Summarize** - Provide context for next session if work is incomplete

Work is complete when all checks pass and tasks are updated.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
