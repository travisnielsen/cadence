<!--
Sync Impact Report
- Version change: 1.1.0 → 2.0.0
- Modified principles:
	- Development Workflow / Task tracking rule: "All work tracked via bd" → "Task tracking via bd is optional"
- Added sections:
	- Governance amendment procedure
	- Governance versioning policy
	- Governance compliance review expectations
- Removed sections:
	- None
- Templates requiring updates:
	- ✅ .specify/templates/plan-template.md
	- ✅ .specify/templates/spec-template.md (reviewed, no change required)
	- ✅ .specify/templates/tasks-template.md (reviewed, no change required)
	- ⚠ .specify/templates/commands/*.md (directory not present in repository)
- Follow-up TODOs:
	- TODO(COMMAND_TEMPLATES_DIR): Confirm whether command templates are stored outside this repo or intentionally omitted.
-->

# Cadence Constitution

## Core Principles

### I. Async-First

All I/O-bound operations must be asynchronous. Never block the event loop. Use async-compatible libraries exclusively.
Rationale: predictable concurrency and responsiveness are required for streaming agent workflows.

### II. Validated Data at Boundaries

All data crossing boundaries (API, config, inter-agent messages) must be validated through Pydantic models. No raw dicts or untyped data.
Rationale: boundary validation reduces runtime ambiguity and prevents malformed data propagation.

### III. Fully Typed

All function parameters and return types must have type annotations. Static type checking must pass.
Rationale: strong typing is required for safe refactoring and reliable multi-agent integration.

### IV. Single-Responsibility Executors

Each workflow executor owns exactly one concern. Shared capabilities (clients, tools) are centralized for reuse across agents.
Rationale: strict ownership limits cross-cutting drift and keeps workflow routing understandable.

### V. Automated Quality Gates (NON-NEGOTIABLE)

All quality checks must pass before any commit. No exceptions, no overrides.
Rationale: mandatory automated gates preserve baseline correctness and code health.

## Technology Stack

| Layer       | Technology                                     |
| ----------- | ---------------------------------------------- |
| Backend     | Python 3.11+, FastAPI, Microsoft Agent Framework |
| Frontend    | Next.js, React, assistant-ui, Tailwind CSS     |
| AI Platform | Azure AI Foundry, Azure OpenAI                 |
| Data        | Azure SQL, Azure AI Search                     |
| Auth        | Azure AD via MSAL (optional)                   |
| IaC         | Terraform                                      |

## Development Workflow

1. **Conventional commits** — `type(scope): description` format required.
3. **Quality before commit** — Automated checks must pass.
4. **Push before done** — All changes committed AND pushed before ending a session.

## Governance

This constitution states principles. Implementation details (file conventions, thresholds, commands) live in `CODING_STANDARD.md`, `DEV_SETUP.md`, and `.github/copilot-instructions.md`.

### Amendment Procedure

Any constitutional change MUST:



1. Update this file with clear normative language.
2. Include a Sync Impact Report at the top of this file.
3. Propagate aligned updates to affected templates and runtime guidance docs in the same change.

### Versioning Policy



Constitution versions follow semantic versioning:

- **MAJOR**: backward-incompatible governance or principle removals/redefinitions.
- **MINOR**: new principle/section or materially expanded guidance.
- **PATCH**: clarifications, wording fixes, and non-semantic refinements.

### Compliance Review Expectations

Every planning cycle MUST include a constitution check. Any violations MUST be documented with explicit justification and a simpler alternative considered.

**Version**: 2.0.0 | **Ratified**: 2026-02-09 | **Last Amended**: 2026-02-22
