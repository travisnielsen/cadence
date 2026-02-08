"""
NL2SQL Controller - Orchestrates NL2SQL query processing.

The controller:
1. Searches for cached queries matching user questions
2. Executes SQL against the Wide World Importers database
3. Returns structured results
"""

from agent_framework import ChatAgent

from .agent import agent, load_prompt


def get_agent() -> ChatAgent:
    """
    Get the NL2SQL agent.

    Returns:
        Configured ChatAgent with SQL tools
    """
    return agent


# Export for programmatic access and DevUI discovery
__all__ = ["agent", "get_agent", "load_prompt"]
