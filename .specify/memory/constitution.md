# Cadence Constitution

## Core Principles

### I. Async-First

All I/O-bound operations must be asynchronous. Never block the event loop. Use async-compatible libraries exclusively.

### II. Validated Data at Boundaries

All data crossing boundaries (API, config, inter-agent messages) must be validated through Pydantic models. No raw dicts or untyped data.

### III. Fully Typed

All function parameters and return types must have type annotations. Static type checking must pass.

### IV. Single-Responsibility Executors

Each workflow executor owns exactly one concern. Shared capabilities (clients, tools) are centralized for reuse across agents.

### V. Automated Quality Gates (NON-NEGOTIABLE)

All quality checks must pass before any commit. No exceptions, no overrides.

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

1. **Task tracking** — All work tracked via `bd` (beads).
2. **Conventional commits** — `type(scope): description` format required.
3. **Quality before commit** — Automated checks must pass.
4. **Push before done** — All changes committed AND pushed before ending a session.

## Governance

This constitution states principles. Implementation details (file conventions, thresholds, commands) live in `CODING_STANDARD.md`, `DEV_SETUP.md`, and `.github/copilot-instructions.md`. Changes to principles require updating this file and related docs in tandem.

**Version**: 1.1.0 | **Ratified**: 2026-02-09 | **Last Amended**: 2026-02-09
