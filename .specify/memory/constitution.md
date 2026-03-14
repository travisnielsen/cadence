<!--
Sync Impact Report
- Version change: 2.0.0 -> 2.0.1
- Modified principles:
	- I. Async-First (clarified explicit prohibition on blocking libraries)
	- II. Validated Data at Boundaries (clarified boundary scope and no-raw-dict rule)
	- III. Fully Typed (clarified full signature typing and static typing gate)
	- V. Automated Quality Gates (clarified required command and pre-commit requirement)
- Added sections:
	- None
- Removed sections:
	- None
- Templates requiring updates:
	- ✅ .specify/templates/plan-template.md
	- ✅ .specify/templates/spec-template.md (reviewed, no change required)
	- ✅ .specify/templates/tasks-template.md (reviewed, no change required)
	- ✅ .specify/templates/commands/*.md
- Follow-up TODOs:
	- None
-->

# Cadence Constitution

## Core Principles

### I. Async-First

All I/O-bound operations MUST be asynchronous. The event loop MUST NOT be blocked.
Use async-compatible libraries exclusively for network and filesystem I/O.
Rationale: predictable concurrency and responsiveness are required for streaming agent workflows.

### II. Validated Data at Boundaries

All data crossing boundaries (API payloads, environment/config loading, inter-agent
messages, and external service responses) MUST be validated through Pydantic models.
Raw dict contracts at boundaries are prohibited.
Rationale: boundary validation reduces runtime ambiguity and prevents malformed data propagation.

### III. Fully Typed

All function parameters and return values MUST have explicit type annotations.
Static type checks MUST pass before merge.
Rationale: strong typing is required for safe refactoring and reliable multi-agent integration.

### IV. Single-Responsibility Executors

Each workflow executor owns exactly one concern. Shared capabilities (clients, tools) are centralized for reuse across agents.
Rationale: strict ownership limits cross-cutting drift and keeps workflow routing understandable.

### V. Automated Quality Gates (NON-NEGOTIABLE)

All quality checks MUST pass before any commit. The canonical gate is
`uv run poe check`. No exceptions and no bypasses.
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
2. **Quality before commit** — `uv run poe check` MUST pass.
3. **Push before done** — All changes committed AND pushed before ending a session.

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

**Version**: 2.0.1 | **Ratified**: 2026-02-09 | **Last Amended**: 2026-03-11
