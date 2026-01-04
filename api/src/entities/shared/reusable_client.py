"""
Reusable Agent Client - Finds existing agents by name before creating new ones.

This prevents duplicate agents from being created on each app restart or deployment.
Uses a global cache to avoid repeated agent lookups across instances.
"""

import logging
from typing import Any

from agent_framework_azure_ai import AzureAIAgentClient

logger = logging.getLogger(__name__)

# Global cache of agent name -> agent_id (persists across all instances)
_AGENT_ID_CACHE: dict[str, str] = {}


class ReusableAgentClient(AzureAIAgentClient):
    """
    Extended AzureAIAgentClient that finds existing agents by name before creating new ones.
    
    This prevents duplicate agents from being created on each app restart or deployment.
    Uses a global cache to avoid repeated lookups on each request.
    """
    
    async def _get_agent_id_or_create(self, run_options: dict[str, Any] | None = None) -> str:
        """
        Find an existing agent by name, or create a new one if not found.
        
        Overrides the parent method to add agent lookup by name with global caching.
        """
        # If we already have an agent_id on this instance, use it
        if self.agent_id is not None:
            return self.agent_id
        
        run_options = run_options or {}
        agent_name = self.agent_name or "UnnamedAgent"
        
        # Check global cache first (avoids listing agents on every request)
        if agent_name in _AGENT_ID_CACHE:
            cached_id = _AGENT_ID_CACHE[agent_name]
            logger.info("Using cached agent ID for '%s': %s", agent_name, cached_id)
            self.agent_id = cached_id
            self._agent_created = False
            return self.agent_id
        
        # Search for existing agent by name
        try:
            logger.info("Searching for existing agent with name: %s", agent_name)
            async for agent in self.agents_client.list_agents():
                if agent.name == agent_name:
                    logger.info("Found existing agent '%s' with ID: %s", agent_name, agent.id)
                    self.agent_id = str(agent.id)
                    self._agent_definition = agent
                    self._agent_created = False  # Mark as not created by us
                    # Cache the agent ID globally
                    _AGENT_ID_CACHE[agent_name] = self.agent_id
                    return self.agent_id
            logger.info("No existing agent found with name: %s, will create new one", agent_name)
        except Exception as e:
            logger.warning("Error searching for existing agent '%s': %s", agent_name, e)
        
        # No existing agent found, delegate to parent to create one
        result = await super()._get_agent_id_or_create(run_options)
        # Cache the newly created agent ID
        _AGENT_ID_CACHE[agent_name] = result
        return result
