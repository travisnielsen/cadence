"""
Data Agent Workflow - Orchestrates Chat, NL2SQL, and Parameter Extractor agents.

This module exports 'workflow' for DevUI auto-discovery.

The workflow:
1. ChatAgentExecutor receives user messages and triages them
2. For data questions: routes to NL2SQLAgentExecutor
3. NL2SQLAgentExecutor searches query templates to understand intent
4. If high confidence match: routes to ParameterExtractorExecutor
5. ParameterExtractorExecutor extracts parameters and builds SQL
6. NL2SQLAgentExecutor executes SQL and returns results
7. ChatAgentExecutor renders data results for the user
8. For clarification: user provides more info, flow repeats from step 5

Agent Reuse:
- Agents are found by name (deterministic) on first request
- If an agent with the matching name exists, it is reused
- If not found, a new agent is created
- Set should_cleanup_agent=False to prevent agent deletion on app shutdown

Workflow Per-Request:
- The Agent Framework doesn't support concurrent workflow executions
- We create a fresh workflow instance per request
- Agent clients are reused (they cache agent IDs globally)
"""

import logging
import os

from agent_framework import WorkflowBuilder
from azure.identity.aio import DefaultAzureCredential

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from chat_agent.executor import ChatAgentExecutor  # type: ignore[import-not-found]
    from data_agent.executor import NL2SQLAgentExecutor  # type: ignore[import-not-found]
    from parameter_extractor.executor import ParameterExtractorExecutor  # type: ignore[import-not-found]
    from shared.reusable_client import ReusableAgentClient  # type: ignore[import-not-found]
except ImportError:
    from src.entities.chat_agent.executor import ChatAgentExecutor
    from src.entities.data_agent.executor import NL2SQLAgentExecutor
    from src.entities.parameter_extractor.executor import ParameterExtractorExecutor
    from src.entities.shared.reusable_client import ReusableAgentClient

logger = logging.getLogger(__name__)

# Module-level clients - reused across requests (they cache agent IDs globally)
_chat_client: ReusableAgentClient | None = None
_nl2sql_client: ReusableAgentClient | None = None
_param_extractor_client: ReusableAgentClient | None = None


def _get_clients() -> tuple[ReusableAgentClient, ReusableAgentClient, ReusableAgentClient]:
    """
    Get or create the agent clients (singleton pattern).
    
    Clients are reused because they cache agent IDs globally,
    avoiding repeated agent lookups.
    """
    global _chat_client, _nl2sql_client, _param_extractor_client
    
    if _chat_client is not None and _nl2sql_client is not None and _param_extractor_client is not None:
        return _chat_client, _nl2sql_client, _param_extractor_client
    
    # Get Azure AI Foundry endpoint from environment
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise ValueError(
            "AZURE_AI_PROJECT_ENDPOINT environment variable is required. "
            "Set it to your Azure AI Foundry project endpoint."
        )

    # Create credential with optional managed identity
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        credential = DefaultAzureCredential(managed_identity_client_id=client_id)
    else:
        credential = DefaultAzureCredential()
    
    # Model deployments
    default_model = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    chat_model = os.getenv("AZURE_AI_CHAT_MODEL", default_model)
    nl2sql_model = os.getenv("AZURE_AI_NL2SQL_MODEL", default_model)
    param_extractor_model = os.getenv("AZURE_AI_PARAM_EXTRACTOR_MODEL", default_model)
    
    _chat_client = ReusableAgentClient(
        endpoint=endpoint,
        credential=credential,
        model_deployment_name=chat_model,
        should_cleanup_agent=False,
    )
    
    _nl2sql_client = ReusableAgentClient(
        endpoint=endpoint,
        credential=credential,
        model_deployment_name=nl2sql_model,
        should_cleanup_agent=False,
    )
    
    _param_extractor_client = ReusableAgentClient(
        endpoint=endpoint,
        credential=credential,
        model_deployment_name=param_extractor_model,
        should_cleanup_agent=False,
    )
    
    return _chat_client, _nl2sql_client, _param_extractor_client


def create_workflow_instance():
    """
    Create a fresh workflow instance for a single request.
    
    The Agent Framework doesn't support concurrent workflow executions,
    so we create a new workflow per request. The agent clients are reused
    (they cache agent IDs globally).
    
    Returns:
        Tuple of (workflow, chat_executor, chat_client)
    """
    chat_client, nl2sql_client, param_extractor_client = _get_clients()
    
    # Create fresh executors for this request
    chat_executor = ChatAgentExecutor(chat_client)
    nl2sql_executor = NL2SQLAgentExecutor(nl2sql_client)
    param_extractor_executor = ParameterExtractorExecutor(param_extractor_client)

    # Build fresh workflow with all edges
    # Chat <-> NL2SQL: For routing questions and receiving responses
    # NL2SQL <-> ParamExtractor: For parameter extraction workflow
    workflow = (
        WorkflowBuilder()
        .add_edge(chat_executor, nl2sql_executor)
        .add_edge(nl2sql_executor, chat_executor)
        .add_edge(nl2sql_executor, param_extractor_executor)
        .add_edge(param_extractor_executor, nl2sql_executor)
        .set_start_executor(chat_executor)
        .build()
    )

    return workflow, chat_executor, chat_client


def _create_workflow():
    """
    Create the data agent workflow (for DevUI and initial setup).

    Returns:
        Tuple of (workflow, chat_executor, chat_client)
    """
    return create_workflow_instance()


# Create workflow at module level for DevUI discovery
workflow, chat_executor, chat_client = _create_workflow()
