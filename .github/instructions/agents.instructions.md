---
applyTo: "**/*.agent.md"
description: Guidelines for creating and maintaining GitHub Copilot custom agents
---

# Custom Agent Guidelines

Guidelines for creating and maintaining GitHub Copilot custom agents.

## File Location & Naming

- **Repository-level:** `.github/agents/*.agent.md`
- **Organization-level:** `agents/*.agent.md` (in org-wide repo)
- **Naming:** lowercase with hyphens (e.g., `feature-planner.agent.md`)

## Required Frontmatter

```yaml
---
name: "Display Name"
description: "Brief description (50-150 chars) shown in agent selector"
tools: ["read", "edit", "search"]
model: Claude Sonnet 4.5
user-invokable: true # Show in dropdown (default: true)
disable-model-invocation: false # Allow as subagent (default: false)
agents: ["*"] # Which subagents this agent can invoke (default: all)
handoffs:
  - label: "Next Step"
    agent: target-agent
    prompt: "Context for handoff"
    send: false
---
```

## Tools Reference

### Standard Tool Aliases

| Alias     | Description                   |
| --------- | ----------------------------- |
| `read`    | Read file contents            |
| `edit`    | Create and modify files       |
| `search`  | Search workspace (grep, glob) |
| `execute` | Run terminal commands         |
| `web`     | Fetch web pages, web search   |
| `agent`   | Invoke sub-agents             |
| `todo`    | Manage task lists             |

### MCP Server Tools

| MCP Server        | Usage              | Description                    |
| ----------------- | ------------------ | ------------------------------ |
| `github/*`        | GitHub operations  | Built-in, read-only by default |
| `playwright/*`    | Browser automation | Built-in, localhost only       |
| `azure-mcp/*`     | Azure resources    | Requires configuration         |
| `microsoftdocs/*` | Microsoft docs     | Documentation search           |

### Common Tool Sets

**Base tools (all agents):** `read`, `edit`, `search`

**+ Planning/Research:** `web`, `todo`

**+ Implementation:** `execute`, `todo`

**+ Orchestration:** `agent`, `todo`

**+ Azure/Cloud:** `azure-mcp/*`, `microsoftdocs/*`

## Available Agents

All agents include base tools: `read`, `edit`, `search`

Select agents from the **dropdown menu** in VS Code Chat view.

### Role Agents

| Agent            | Purpose                       | Additional Tools                                |
| ---------------- | ----------------------------- | ----------------------------------------------- |
| `Planner`        | Implementation planning       | `web`, `todo`, `azure-mcp/*`, `microsoftdocs/*` |
| `Implementer`    | Write production code         | `execute`, `todo`                               |
| `Tester`         | Write tests                   | `execute`, `todo`                               |
| `Reviewer`       | Code quality review           | `azure-mcp/*`                                   |
| `Security`       | Security audit (OWASP)        | `web`                                           |
| `Orchestrator`   | Coordinate workflows          | `agent`, `todo`                                 |
| `Architect`      | Architecture design (no code) | `web`, `azure-mcp/*`, `microsoftdocs/*`         |
| `Infrastructure` | IaC (Bicep/Terraform/ARM)     | `execute`, `web`, `agent`, `azure-mcp/*`        |
| `Docs`           | Technical writing & docs      | `web`, `microsoftdocs/*`                        |

### Spec Kit Agents (Planning)

Use these via slash commands for structured feature planning. See copilot-instructions.md for the full flow.

| Agent                  | Slash Command          | Purpose                        |
| ---------------------- | ---------------------- | ------------------------------ |
| `speckit.specify`      | `/speckit.specify`     | Create feature spec from description |
| `speckit.clarify`      | `/speckit.clarify`     | Refine spec requirements       |
| `speckit.plan`         | `/speckit.plan`        | Generate technical plan        |
| `speckit.tasks`        | `/speckit.tasks`       | Generate ordered task checklist |
| `speckit.analyze`      | `/speckit.analyze`     | Analyze for consistency        |
| `speckit.checklist`    | `/speckit.checklist`   | Generate review checklists     |
| `speckit.implement`    | `/speckit.implement`   | Execute tasks (prefer role agents) |
| `speckit.taskstoissues`| `/speckit.taskstoissues`| Convert tasks to GitHub Issues |
| `speckit.constitution` | `/speckit.constitution` | Set project coding standards   |

## Agent Body Structure

1. **Identity** - Clear role statement ("You are a...")
2. **Process** - Step-by-step workflow
3. **Guidelines** - Domain-specific rules
4. **Output Format** - Expected deliverables and file locations
5. **Constraints** - What NOT to do
6. **Checklist** - Quality verification before completion

## Handoffs (IDE Only)

Handoffs create workflow transitions **only in VS Code/IDEs**, not on GitHub.com.

```yaml
handoffs:
  - label: "Button Text" # Display text
    agent: target-agent # Agent name (without @)
    prompt: "Do X with above" # Pre-filled context
    send: false # false = user reviews before sending
```

### Handoff Modes

| `send` Value | Behavior                                              |
| ------------ | ----------------------------------------------------- |
| `false`      | Shows button; user clicks, reviews prompt, then sends |
| `true`       | Pre-fills prompt in chat; user just presses Enter     |

> **Note:** All agents in this project use `send: false` for manual control over handoffs.

### Fully Automatic Handoffs (Orchestrator)

For **fully automatic** agent-to-agent execution without user interaction, use the **Orchestrator** agent:

```
@Orchestrator implement user authentication with tests and security review
```

The Orchestrator has the `agent` tool which allows it to **programmatically invoke** other agents as subagents. This enables true end-to-end automation.

### Orchestrator Parallel Pattern

```
Phase 1 (Sequential):  Planner --> Architect
Phase 2 (Parallel):    Infrastructure ──┐
                       Implementer ─────┴--> (wait)
Phase 3 (Sequential):  Tester --> Reviewer
Phase 4 (Parallel):    Security ──┐
                       Docs ──────┴--> (complete)
```

## Workflows

### Standard Development Flow

```
Step 1: Planner      --> Creates implementation plan
Step 2: Implementer  --> Writes code based on plan
Step 3: Tester       --> Writes tests for implementation
Step 4: Reviewer     --> Reviews code quality
Step 5: Security     --> Audits for vulnerabilities
Step 6: Docs         --> Creates/updates documentation
```

### Handoff Matrix

| From Agent       | Hands Off To                      |
| ---------------- | --------------------------------- |
| `Planner`        | `Implementer`, `Docs`             |
| `Implementer`    | `Tester`, `Reviewer`, `Docs`      |
| `Tester`         | `Reviewer`                        |
| `Reviewer`       | `Security`, `Planner` (if rework) |
| `Security`       | `Implementer` (if fixes needed)   |
| `Architect`      | `Planner`, `Infrastructure`       |
| `Infrastructure` | `Security`, `Reviewer`            |
| `Docs`           | `Reviewer`                        |

### Alternative Workflows

| Workflow       | Flow                                                            |
| -------------- | --------------------------------------------------------------- |
| Bug Fix        | `Planner --> Implementer --> Tester`                            |
| Refactoring    | `Planner --> Tester (tests first) --> Implementer --> Reviewer` |
| Architecture   | `Architect --> Planner --> Implementer`                         |
| Infrastructure | `Architect --> Infrastructure --> Security`                     |

## Best Practices

- Be specific, not vague in instructions
- Include code examples where helpful
- Reference [CODING_STANDARD.md](../../CODING_STANDARD.md)
- Define clear boundaries and constraints
- Specify output format and file locations
- Keep prompt under 30,000 characters

## References

- [GitHub Docs: Custom Agents](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-custom-agents)
- [GitHub Docs: Configuration Reference](https://docs.github.com/en/copilot/reference/custom-agents-configuration)
- [VS Code Docs: Subagents](https://code.visualstudio.com/docs/copilot/agents/subagents)
- [Awesome Copilot](https://github.com/github/awesome-copilot)
