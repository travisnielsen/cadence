"""
NL2SQL Workflow - Orchestrates query processing with NL2SQL Controller.

This module provides the workflow for processing data queries.
The ConversationOrchestrator (in orchestrator/orchestrator.py) handles
user-facing chat, intent classification, and refinements - then invokes
this workflow for data query processing.

The workflow:
1. NL2SQLController receives data questions from ConversationOrchestrator
2. Searches query templates to understand intent
3. If high confidence match: routes to ParameterExtractorExecutor
4. ParameterExtractorExecutor extracts parameters and builds SQL
5. If no template match: searches tables and routes to QueryBuilderExecutor
6. QueryBuilderExecutor generates dynamic SQL from table metadata
7. NL2SQLController validates and executes SQL, returns results

Agent Management (V2 Responses API):
- Uses AzureAIClient with use_latest_version=True
- Agents are versioned by name - V2 automatically finds/creates latest version
- No manual agent cleanup needed (versioned agents are immutable)

Workflow Per-Request:
- The Agent Framework doesn't support concurrent workflow executions
- We create a fresh workflow instance per request
- Agent clients are reused across requests
"""

import logging
import os

from agent_framework import WorkflowBuilder
from agent_framework_azure_ai import AzureAIClient
from azure.identity.aio import DefaultAzureCredential
from entities.nl2sql_controller.executor import NL2SQLController
from entities.parameter_extractor.executor import ParameterExtractorExecutor
from entities.parameter_validator.executor import ParameterValidatorExecutor
from entities.query_builder.executor import QueryBuilderExecutor
from entities.query_validator.executor import QueryValidatorExecutor

logger = logging.getLogger(__name__)

# Module-level clients - reused across requests
_nl2sql_client: AzureAIClient | None = None
_param_extractor_client: AzureAIClient | None = None
_query_builder_client: AzureAIClient | None = None


def _get_clients() -> tuple[AzureAIClient, AzureAIClient, AzureAIClient]:
    """
    Get or create the agent clients (singleton pattern).

    V2 AzureAIClient uses agent versioning - with use_latest_version=True,
    it automatically finds or creates the latest version of named agents.
    """
    global _nl2sql_client, _param_extractor_client, _query_builder_client

    if (
        _nl2sql_client is not None
        and _param_extractor_client is not None
        and _query_builder_client is not None
    ):
        return _nl2sql_client, _param_extractor_client, _query_builder_client

    # Get Azure AI Foundry endpoint from environment
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise ValueError(
            "AZURE_AI_PROJECT_ENDPOINT environment variable is required. "
            "Set it to your Azure AI Foundry project endpoint."
        )

    # Create credential with managed identity support
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        credential = DefaultAzureCredential(managed_identity_client_id=client_id)
    else:
        credential = DefaultAzureCredential()

    # Get model deployment names (with fallback to default)
    nl2sql_model = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
    param_extractor_model = os.getenv("PARAM_EXTRACTOR_MODEL_DEPLOYMENT_NAME", nl2sql_model)
    query_builder_model = os.getenv("QUERY_BUILDER_MODEL_DEPLOYMENT_NAME", nl2sql_model)

    logger.info(
        "Creating agent clients: NL2SQL=%s, ParamExtractor=%s, QueryBuilder=%s",
        nl2sql_model,
        param_extractor_model,
        query_builder_model,
    )

    _nl2sql_client = AzureAIClient(
        project_endpoint=endpoint,
        credential=credential,
        model_deployment_name=nl2sql_model,
        use_latest_version=True,
    )

    _param_extractor_client = AzureAIClient(
        project_endpoint=endpoint,
        credential=credential,
        model_deployment_name=param_extractor_model,
        use_latest_version=True,
    )

    _query_builder_client = AzureAIClient(
        project_endpoint=endpoint,
        credential=credential,
        model_deployment_name=query_builder_model,
        use_latest_version=True,
    )

    return _nl2sql_client, _param_extractor_client, _query_builder_client


def create_nl2sql_workflow():
    """
    Create the NL2SQL workflow for processing data queries.

    This workflow is invoked by the ConversationOrchestrator for data queries.
    It starts at NL2SQLController and handles:
    - Template search and matching
    - Parameter extraction (via ParameterExtractor)
    - Parameter validation (via ParameterValidator)
    - Query validation (via QueryValidator)
    - Dynamic query generation (via QueryBuilder)
    - SQL execution

    Returns:
        Tuple of (workflow, nl2sql_controller, nl2sql_client)
    """
    nl2sql_client, param_extractor_client, query_builder_client = _get_clients()

    # Create fresh executors for this request
    nl2sql_controller = NL2SQLController(nl2sql_client)
    param_extractor_executor = ParameterExtractorExecutor(param_extractor_client)
    param_validator_executor = ParameterValidatorExecutor()
    query_builder_executor = QueryBuilderExecutor(query_builder_client)
    query_validator_executor = QueryValidatorExecutor()

    # Build workflow starting at NL2SQL controller
    # ConversationOrchestrator handles user-facing chat externally
    workflow = (
        WorkflowBuilder()
        .add_edge(nl2sql_controller, param_extractor_executor)
        .add_edge(param_extractor_executor, nl2sql_controller)
        .add_edge(nl2sql_controller, param_validator_executor)
        .add_edge(param_validator_executor, nl2sql_controller)
        .add_edge(nl2sql_controller, query_builder_executor)
        .add_edge(query_builder_executor, nl2sql_controller)
        .add_edge(nl2sql_controller, query_validator_executor)
        .add_edge(query_validator_executor, nl2sql_controller)
        .set_start_executor(nl2sql_controller)
        .build()
    )

    logger.info("Created NL2SQL workflow")
    return workflow, nl2sql_controller, nl2sql_client
