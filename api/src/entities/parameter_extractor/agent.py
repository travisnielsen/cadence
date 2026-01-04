"""
Parameter Extractor Agent - Standalone agent for DevUI testing.

The agent extracts parameter values from user queries to fill
SQL template tokens using LLM-based analysis.
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
    """Create the parameter extractor agent."""
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

    # Use a potentially smaller/faster model for parameter extraction
    default_model = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    extractor_model = os.getenv("AZURE_AI_PARAM_EXTRACTOR_MODEL", default_model)

    chat_client = ReusableAgentClient(
        endpoint=endpoint,
        credential=credential,
        model_deployment_name=extractor_model,
        should_cleanup_agent=False,  # Don't delete agent on client close
    )

    # Load instructions
    instructions = load_prompt()

    # Create agent (no tools needed - this is pure LLM reasoning)
    return ChatAgent(
        name="parameter-extractor-agent",
        instructions=instructions,
        chat_client=chat_client,
    )


# Create agent at module level for DevUI discovery
agent = _create_agent()
