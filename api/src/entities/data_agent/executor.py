"""
NL2SQL Agent Executor for workflow integration.

Note: Do NOT use 'from __future__ import annotations' in this module.
The Agent Framework's @handler decorator validates WorkflowContext type annotations
at class definition time, which is incompatible with PEP 563 stringified annotations.
"""

import json
import logging
from pathlib import Path
from typing import Union

from agent_framework import (
    ChatAgent,
    Executor,
    Role,
    WorkflowContext,
    handler,
)
from agent_framework_azure_ai import AzureAIClient

# Type alias for V2 client
AzureAIAgentClient = AzureAIClient

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from models import (  # type: ignore[import-not-found]
        NL2SQLResponse,
        QueryTemplate,
        ParameterExtractionRequest,
        ParameterExtractionResponse,
        ClarificationMessage,
        ExtractionRequestMessage,
        ExtractionResponseMessage,
    )
except ImportError:
    from src.entities.models import (
        NL2SQLResponse,
        QueryTemplate,
        ParameterExtractionRequest,
        ParameterExtractionResponse,
        ClarificationMessage,
        ExtractionRequestMessage,
        ExtractionResponseMessage,
    )

from .tools import execute_sql, search_query_templates

logger = logging.getLogger(__name__)

# Type alias for NL2SQL output messages
# NL2SQL sends str (JSON) to chat and ExtractionRequestMessage to param_extractor
NL2SQLOutputMessage = Union[str, ExtractionRequestMessage]

# Key for storing pending clarification state
CLARIFICATION_STATE_KEY = "pending_clarification"


def _load_prompt() -> str:
    """Load prompt from prompt.md in this folder."""
    prompt_path = Path(__file__).parent / "prompt.md"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


class NL2SQLAgentExecutor(Executor):
    """
    Executor that handles NL2SQL data queries with template-based query generation.

    This executor orchestrates a multi-step workflow:
    1. Search query_templates index to understand user intent
    2. If high confidence match: route to ParameterExtractor for SQL generation
    3. Execute the generated SQL and return results
    4. Handle clarification requests from ParameterExtractor

    If no high confidence template match is found, asks for clarification.
    """

    agent: ChatAgent

    def __init__(self, chat_client: AzureAIAgentClient, executor_id: str = "nl2sql"):
        """
        Initialize the NL2SQL executor.

        Args:
            chat_client: The Azure AI agent client for creating the agent
            executor_id: Executor ID for workflow routing
        """
        instructions = _load_prompt()

        # Agent uses search_query_templates to understand intent
        # SQL execution happens directly in the executor after parameter extraction
        self.agent = ChatAgent(
            name="nl2sql-agent",
            instructions=instructions,
            chat_client=chat_client,
            tools=[search_query_templates, execute_sql],
        )

        super().__init__(id=executor_id)
        logger.info("NL2SQLAgentExecutor initialized with tools: ['search_query_templates', 'execute_sql']")

    @handler
    async def handle_question(
        self,
        question: str,
        ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Handle a user question by searching for query templates and orchestrating extraction.

        Workflow:
        1. Search query_templates for matching intent
        2. If high confidence: route to ParameterExtractor
        3. If low confidence: ask for clarification

        Args:
            question: The user's natural language question
            ctx: Workflow context for sending the response (as JSON string)
        """
        logger.info("NL2SQLAgentExecutor processing question: %s", question[:100])

        try:
            # Step 1: Search for query templates
            # Note: AIFunction wrapper is awaitable but type checker doesn't understand it
            search_result = await search_query_templates(question)  # type: ignore[misc]

            if search_result.get("has_high_confidence_match") and search_result.get("best_match"):
                # High confidence AND unambiguous match found - route to parameter extractor
                best_match = search_result["best_match"]
                template = QueryTemplate.model_validate(best_match)

                logger.info(
                    "High confidence unambiguous template match: '%s' (score: %.3f, gap: %.3f)",
                    template.intent,
                    template.score,
                    search_result.get("ambiguity_gap", 0.0)
                )

                # Build extraction request and route to parameter extractor
                extraction_request = ParameterExtractionRequest(
                    user_query=question,
                    template=template,
                )

                # Store the original question for potential clarification flow
                await ctx.set_shared_state(
                    CLARIFICATION_STATE_KEY,
                    {
                        "original_question": question,
                        "template": template.model_dump(),
                    }
                )

                # Route to parameter extractor
                request_msg = ExtractionRequestMessage(
                    request_json=extraction_request.model_dump_json()
                )
                await ctx.send_message(request_msg, target_id="param_extractor")

            else:
                # Either low confidence or ambiguous match - ask for clarification
                is_ambiguous = search_result.get("is_ambiguous", False)
                confidence_score = search_result.get("confidence_score", 0)
                confidence_threshold = search_result.get("confidence_threshold", 0.75)
                
                if is_ambiguous:
                    # Ambiguous match - multiple templates with similar high scores
                    all_matches = search_result.get("all_matches", [])
                    matching_intents = [m.get("intent", "unknown") for m in all_matches[:3] if m.get("score", 0) >= confidence_threshold]
                    
                    logger.info(
                        "Ambiguous template match (gap: %.3f < %.3f). Top matches: %s",
                        search_result.get("ambiguity_gap", 0),
                        search_result.get("ambiguity_gap_threshold", 0.05),
                        matching_intents
                    )
                    
                    # Build clarification message listing possible interpretations
                    intent_list = ", ".join(f"'{intent}'" for intent in matching_intents)
                    error_message = (
                        f"Your question could match multiple query types: {intent_list}. "
                        "Could you please be more specific about what data you're looking for?"
                    )
                else:
                    # Low confidence - no good match found
                    logger.info(
                        "No high confidence template match (best score: %.3f, threshold: %.3f)",
                        confidence_score,
                        confidence_threshold
                    )
                    error_message = (
                        "I couldn't understand your question well enough to query the database. "
                        "Could you please rephrase or provide more details about what data you're looking for?"
                    )

                # Build a clarification response
                nl2sql_response = NL2SQLResponse(
                    sql_query="",
                    error=error_message,
                    confidence_score=confidence_score,
                )

                await ctx.send_message(nl2sql_response.model_dump_json())

        except Exception as e:
            logger.error("NL2SQL execution error: %s", e)
            nl2sql_response = NL2SQLResponse(
                sql_query="",
                error=str(e)
            )
            await ctx.send_message(nl2sql_response.model_dump_json())

    @handler
    async def handle_extraction_response(
        self,
        extraction_response_msg: ExtractionResponseMessage,
        ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Handle the response from ParameterExtractor.

        This is called after the ParameterExtractor has processed the template
        and either extracted parameters or requested clarification.

        Args:
            extraction_response_msg: Wrapped JSON string containing ParameterExtractionResponse
            ctx: Workflow context for sending the response
        """
        logger.info("NL2SQLAgentExecutor received extraction response")

        try:
            # Parse the extraction response
            response_data = json.loads(extraction_response_msg.response_json)
            extraction_response = ParameterExtractionResponse.model_validate(response_data)

            if extraction_response.status == "success":
                # Parameters extracted successfully - execute the SQL
                completed_sql = extraction_response.completed_sql

                if not completed_sql:
                    raise ValueError("Extraction succeeded but no SQL was generated")

                logger.info("Executing extracted SQL: %s", completed_sql[:200])

                # Execute the SQL query
                # Note: AIFunction wrapper is awaitable but type checker doesn't understand it
                sql_result = await execute_sql(completed_sql)  # type: ignore[misc]

                # Clear clarification state since we succeeded
                try:
                    await ctx.set_shared_state(CLARIFICATION_STATE_KEY, None)
                except Exception:
                    pass

                # Build successful response
                nl2sql_response = NL2SQLResponse(
                    sql_query=completed_sql,
                    sql_response=sql_result.get("rows", []),
                    columns=sql_result.get("columns", []),
                    row_count=sql_result.get("row_count", 0),
                    confidence_score=1.0,  # High confidence since we used a template
                    used_cached_query=True,  # Template-based is similar to cached
                    query_source="template",  # Query derived from query_templates
                    error=sql_result.get("error") if not sql_result.get("success") else None,
                )

                logger.info(
                    "NL2SQL completed via template: rows=%d",
                    nl2sql_response.row_count
                )

            elif extraction_response.status == "needs_clarification":
                # Need clarification from user
                clarification_prompt = extraction_response.clarification_prompt or \
                    "I need more information to answer your question."

                # Store clarification state for the follow-up
                clarification_state = {
                    "original_question": extraction_response.original_query,
                    "template_id": extraction_response.template_id,
                    "missing_parameters": [
                        mp.model_dump() for mp in (extraction_response.missing_parameters or [])
                    ],
                    "extracted_parameters": extraction_response.extracted_parameters,
                }

                # Try to get the template from previous state
                try:
                    prev_state = await ctx.get_shared_state(CLARIFICATION_STATE_KEY)
                    if prev_state and "template" in prev_state:
                        clarification_state["template"] = prev_state["template"]
                except KeyError:
                    pass

                await ctx.set_shared_state(CLARIFICATION_STATE_KEY, clarification_state)

                # Return clarification request to user
                nl2sql_response = NL2SQLResponse(
                    sql_query="",
                    error=clarification_prompt,
                    confidence_score=0.5,  # Medium confidence - we found a template but need info
                )

                logger.info("Requesting clarification from user: %s", clarification_prompt)

            else:
                # Error during extraction
                nl2sql_response = NL2SQLResponse(
                    sql_query="",
                    error=extraction_response.error or "Unknown error during parameter extraction",
                )

                logger.error("Extraction failed: %s", extraction_response.error)

        except Exception as e:
            logger.error("Error handling extraction response: %s", e)
            nl2sql_response = NL2SQLResponse(
                sql_query="",
                error=str(e)
            )

        # Send response to chat agent
        await ctx.send_message(nl2sql_response.model_dump_json())

    @handler
    async def handle_clarification(
        self,
        clarification_msg: ClarificationMessage,
        ctx: WorkflowContext[NL2SQLOutputMessage]
    ) -> None:
        """
        Handle a clarification response from the user.

        This is called when the user provides additional information
        after we requested clarification.

        Args:
            clarification_msg: The user's clarification wrapped in ClarificationMessage
            ctx: Workflow context for sending the response
        """
        clarification = clarification_msg.clarification_text
        logger.info("NL2SQLAgentExecutor received clarification: %s", clarification[:100])

        try:
            # Get the stored clarification state
            clarification_state = await ctx.get_shared_state(CLARIFICATION_STATE_KEY)

            if not clarification_state:
                # No pending clarification - treat as new question
                logger.warning("No clarification state found, treating as new question")
                await self.handle_question(clarification, ctx)
                return

            original_question = clarification_state.get("original_question", "")
            template_data = clarification_state.get("template")

            if not template_data:
                # No template stored - treat as new question
                logger.warning("No template in clarification state, treating as new question")
                await self.handle_question(clarification, ctx)
                return

            # Reconstruct the template
            template = QueryTemplate.model_validate(template_data)

            # Combine original question with clarification
            enriched_query = f"{original_question} (Additional info: {clarification})"

            logger.info(
                "Re-submitting with clarification. Original: '%s', Clarification: '%s'",
                original_question[:50],
                clarification[:50]
            )

            # Build new extraction request with enriched query
            extraction_request = ParameterExtractionRequest(
                user_query=enriched_query,
                template=template,
            )

            # Route to parameter extractor again
            request_msg = ExtractionRequestMessage(
                request_json=extraction_request.model_dump_json()
            )
            await ctx.send_message(request_msg, target_id="param_extractor")

        except KeyError:
            # No clarification state - treat as new question
            logger.warning("KeyError getting clarification state, treating as new question")
            await self.handle_question(clarification, ctx)

        except Exception as e:
            logger.error("Error handling clarification: %s", e)
            nl2sql_response = NL2SQLResponse(
                sql_query="",
                error=str(e)
            )
            await ctx.send_message(nl2sql_response.model_dump_json())

    def _parse_agent_response(self, response) -> NL2SQLResponse:
        """Parse the agent's response to extract structured data."""
        sql_query = ""
        sql_response: list[dict] = []
        columns: list[str] = []
        row_count = 0
        confidence_score = 0.0
        used_cached_query = False
        query_source = "dynamic"  # Default to dynamic, update if cached match found
        error = None

        # Extract data from tool call results in the messages
        for message in response.messages:
            if message.role == Role.TOOL:
                for content in message.contents:
                    if hasattr(content, 'result'):
                        result = content.result
                        # Parse JSON string if needed
                        if isinstance(result, str):
                            try:
                                result = json.loads(result)
                            except json.JSONDecodeError:
                                continue

                        if isinstance(result, dict):
                            # Check for execute_sql result
                            if 'rows' in result and result.get('success', False):
                                sql_response = result.get('rows', [])
                                columns = result.get('columns', [])
                                row_count = result.get('row_count', len(sql_response))

                            # Check for search result with confidence
                            if 'has_high_confidence_match' in result:
                                used_cached_query = result.get('has_high_confidence_match', False)
                                if 'best_match' in result and result['best_match']:
                                    best_match = result['best_match']
                                    confidence_score = best_match.get('score', 0.0)
                                    if used_cached_query:
                                        sql_query = best_match.get('query', '')
                                        query_source = "cached"  # Query from cached queries

                            # Check for error
                            if not result.get('success', True) and 'error' in result:
                                error = result['error']

            # Look for function calls to get the SQL query
            if message.role == Role.ASSISTANT:
                for content in message.contents:
                    if hasattr(content, 'name') and content.name == 'execute_sql':
                        if hasattr(content, 'arguments'):
                            args = content.arguments
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    pass
                            if isinstance(args, dict) and 'query' in args:
                                sql_query = args['query']

        return NL2SQLResponse(
            sql_query=sql_query,
            sql_response=sql_response,
            columns=columns,
            row_count=row_count,
            confidence_score=confidence_score,
            used_cached_query=used_cached_query,
            query_source=query_source,
            error=error
        )
