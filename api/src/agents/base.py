"""
Base agent interface and common functionality.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from azure.identity.aio import DefaultAzureCredential
from azure.ai.agents.aio import AgentsClient

logger = logging.getLogger(__name__)


def load_prompt(prompt_name: str) -> str:
    """
    Load a system prompt from the prompts directory.
    
    Args:
        prompt_name: Name of the prompt file (without .md extension)
        
    Returns:
        The content of the prompt file as a string
    """
    prompts_dir = Path(__file__).parent.parent / "prompts"
    prompt_path = prompts_dir / f"{prompt_name}.md"
    
    if not prompt_path.exists():
        logger.warning("Prompt file not found: %s, using default", prompt_path)
        return "You are a helpful AI assistant."
    
    return prompt_path.read_text(encoding="utf-8").strip()


async def find_agent_by_name(endpoint: str, agent_name: str) -> str | None:
    """
    Find an existing agent by name and return its ID.
    
    Args:
        endpoint: Azure AI project endpoint
        agent_name: Name of the agent to find
        
    Returns:
        Agent ID if found, None otherwise
    """
    async with AgentsClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    ) as client:
        agents = client.list_agents()
        async for agent in agents:
            if agent.name == agent_name:
                logger.info("Found existing agent: %s (id: %s)", agent.name, agent.id)
                return agent.id
    return None


class BaseAgent:
    """
    Base class for all agents.
    
    Provides common configuration and utility methods.
    """
    
    # Override in subclasses
    AGENT_NAME: str = "base-agent"
    PROMPT_NAME: str = "default"
    DEFAULT_MODEL: str = "gpt-4o-mini"
    
    endpoint: str  # Type annotation for the endpoint
    deployment: str
    
    def __init__(self):
        endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        if not endpoint:
            raise ValueError("AZURE_AI_PROJECT_ENDPOINT environment variable is required")
        self.endpoint = endpoint
        self.deployment = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", self.DEFAULT_MODEL)
    
    def get_instructions(self) -> str:
        """Load and return the system prompt for this agent."""
        return load_prompt(self.PROMPT_NAME)
    
    async def find_existing_agent(self) -> str | None:
        """Find an existing agent with this agent's name."""
        return await find_agent_by_name(self.endpoint, self.AGENT_NAME)
