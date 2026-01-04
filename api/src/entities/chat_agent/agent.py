"""
Chat Agent - User-facing agent that renders data results.

The chat agent:
1. Receives structured data results from the data agent
2. Formats and presents data clearly to the user
3. Provides helpful context about the results
"""

import os
from pathlib import Path

from agent_framework import ChatAgent
from azure.identity.aio import DefaultAzureCredential

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from shared.reusable_client import ReusableAgentClient  # type: ignore[import-not-found]
except ImportError:
    from src.entities.shared.reusable_client import ReusableAgentClient


def load_prompt() -> str:
    """Load the prompt from prompt.md in this folder."""
    return (Path(__file__).parent / "prompt.md").read_text(encoding="utf-8")


def _create_agent() -> ChatAgent:
    """Create the chat agent."""
    # Get Azure AI Foundry endpoint from environment
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise ValueError(
            "AZURE_AI_PROJECT_ENDPOINT environment variable is required. "
            "Set it to your Azure AI Foundry project endpoint."
        )

    # Create chat client with Azure credential
    # Use AZURE_CLIENT_ID for user-assigned managed identity in Container Apps
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        credential = DefaultAzureCredential(managed_identity_client_id=client_id)
    else:
        credential = DefaultAzureCredential()
    chat_client = ReusableAgentClient(
        endpoint=endpoint,
        credential=credential,
        should_cleanup_agent=False,  # Don't delete agent on client close
    )

    # Load instructions
    instructions = load_prompt()

    # Create agent (no tools - this agent just renders responses)
    return ChatAgent(
        name="chat-agent",
        instructions=instructions,
        chat_client=chat_client,
    )


# Create agent at module level for DevUI discovery
agent = _create_agent()
