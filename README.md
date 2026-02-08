# Cadence

`cadence` is a reference application that demonstrates exploration of structured and unstructured data using natural language and agentic retrieval. It's built using [Microsoft Agent Framework](https://aka.ms/agent-framework) (MAF) hosted on FastAPI for intent and retrieval orchestration and [assistant-ui](https://github.com/assistant-ui/assistant-ui) for the user experience. Communication between these two components happens via Server-Sent Events with thread management delegated to Microsoft Foundry.

![screenshot](./docs/images/data-agent-screenshot.png)

## Getting Started

For complete setup instructions including Azure infrastructure deployment, local development, and production deployment, see the [Infrastructure Guide](infra/README.md).

### Quick Start

1. **Deploy Infrastructure** - Follow the [Infrastructure Guide](infra/README.md) to set up Azure resources
2. **Install Dependencies** - `pnpm install` (also sets up Python environment)
3. **Configure Environment** - Set up `.env` files for API and frontend
4. **Run Locally** - `pnpm dev` starts both UI and agent servers
