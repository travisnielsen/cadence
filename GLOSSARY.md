# Glossary

Quick reference for abbreviations and short names used in this project.

## Terms

| Term  | Full Name                             | Description                                              |
| ----- | ------------------------------------- | -------------------------------------------------------- |
| ADR   | Architecture Decision Record          | Architectural decisions                                  |
| Allowed Values Cache | Allowed Values Cache       | Runtime cache of distinct DB column values for parameter validation |
| API   | Application Programming Interface     | HTTP endpoints, SDK interfaces                           |
| CI/CD | Continuous Integration/Delivery       | Automated build and deploy pipelines                     |
| CLI   | Command-Line Interface                | Terminal-based tools (bd, uv, az)                        |
| Effective Confidence | Effective Confidence       | Per-parameter confidence score (0.0–1.0) = base_confidence × weight |
| Hypothesis-First | Hypothesis-First Clarification | Clarification pattern: present best guess, ask user to confirm or correct |
| IaC   | Infrastructure as Code                | Terraform templates                                     |
| MAF   | Microsoft Agent Framework             | Multi-agent orchestration framework                      |
| NL2SQL| Natural Language to SQL               | Converting user questions to SQL queries                 |
| PR    | Pull Request                          | Code review request on GitHub                            |
| RBAC  | Role-Based Access Control             | Azure permission model                                   |
| Schema Area | Schema Area Context              | Database schema grouping (Sales, Warehouse, etc.) for contextual suggestions |
| SSE   | Server-Sent Events                    | Streaming protocol for real-time UI updates              |
| TLS   | Transport Layer Security              | Encryption protocol (require 1.2+)                       |

## File Types

| Extension   | Description                            |
| ----------- | -------------------------------------- |
| `.jsonl`    | JSON Lines (newline-delimited JSON)    |
| `.agent.md` | GitHub Copilot custom agent definition |
