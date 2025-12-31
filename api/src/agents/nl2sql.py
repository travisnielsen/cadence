"""
NL2SQL Agent - Natural Language to SQL query generation agent.

This agent helps users query databases using natural language,
leveraging Azure AI Search for schema discovery and query examples.
"""

from __future__ import annotations

import logging
import os

from azure.identity.aio import DefaultAzureCredential
import agent_framework.azure as _azure_module  # type: ignore
AzureAIAgentClient = _azure_module.AzureAIAgentClient
from agent_framework import ChatAgent

from .base import BaseAgent, find_agent_by_name, load_prompt

logger = logging.getLogger(__name__)


class NL2SQLAgent(BaseAgent):
    """
    NL2SQL Agent for natural language database querying.
    
    This agent:
    - Converts natural language questions to SQL queries
    - Uses vector search to find relevant table schemas
    - Uses vector search to find similar query examples
    """
    
    AGENT_NAME = "data-explorer-agent"
    PROMPT_NAME = "nl2sql"
    DEFAULT_MODEL = "gpt-4o-mini"
    
    agent_name: str  # Type annotation
    
    def __init__(self):
        super().__init__()
        # Override agent name from environment if provided
        self.agent_name = os.getenv("AZURE_AI_AGENT_NAME", self.AGENT_NAME)


async def build_nl2sql_client() -> AzureAIAgentClient:
    """
    Build the Azure AI (Foundry) agent client for NL2SQL.
    
    Returns:
        Configured AzureAIAgentClient instance
    """
    agent_config = NL2SQLAgent()
    
    # Try to find existing agent by name
    agent_id = await find_agent_by_name(agent_config.endpoint, agent_config.agent_name)
    
    if agent_id:
        logger.info("Reusing existing agent: %s (id: %s)", agent_config.agent_name, agent_id)
    else:
        logger.info("No existing agent found with name '%s', will create new one", agent_config.agent_name)

    logger.info(
        "Using endpoint: %s, deployment: %s, agent: %s",
        agent_config.endpoint,
        agent_config.deployment,
        agent_id or agent_config.agent_name
    )

    return AzureAIAgentClient(
        credential=DefaultAzureCredential(),
        project_endpoint=agent_config.endpoint,
        model_deployment_name=agent_config.deployment,
        agent_id=agent_id,
        agent_name=agent_config.agent_name,
        should_cleanup_agent=False,
    )


def create_nl2sql_agent(chat_client: AzureAIAgentClient) -> ChatAgent:
    """
    Create the NL2SQL chat agent.
    
    Args:
        chat_client: The Azure AI agent client
        
    Returns:
        Configured ChatAgent instance
    """
    instructions = load_prompt("nl2sql")
    
    return ChatAgent(
        name="assistant",
        instructions=instructions,
        chat_client=chat_client,
    )
