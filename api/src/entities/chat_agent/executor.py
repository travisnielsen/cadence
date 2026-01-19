"""
Chat Agent Executor for workflow integration.

Note: Do NOT use 'from __future__ import annotations' in this module.
The Agent Framework's @handler decorator validates WorkflowContext type annotations
at class definition time, which is incompatible with PEP 563 stringified annotations.
"""

import json
import logging
from pathlib import Path
from typing import Any, Union

from agent_framework import (
    AgentThread,
    ChatAgent,
    ChatMessage,
    Executor,
    Role,
    WorkflowContext,
    handler,
)
from agent_framework_azure_ai import AzureAIClient

# Type alias for V2 client
AzureAIAgentClient = AzureAIClient
from typing_extensions import Never

# Support both DevUI (entities on path) and FastAPI (src on path) import patterns
try:
    from models import NL2SQLResponse, ClarificationMessage  # type: ignore[import-not-found]
except ImportError:
    from src.models import NL2SQLResponse, ClarificationMessage

logger = logging.getLogger(__name__)

# Type alias for messages that can be sent to NL2SQL executor
NL2SQLMessage = Union[str, ClarificationMessage]


def get_request_user_id() -> str | None:
    """
    Get the user ID from the request context.
    
    This is a lazy import wrapper to avoid circular imports.
    """
    try:
        from src.api.step_events import get_request_user_id as _get_request_user_id
        return _get_request_user_id()
    except ImportError:
        return None


# Shared state keys for thread management (V2 Foundry uses conversation_id internally)
FOUNDRY_CONVERSATION_ID_KEY = "foundry_conversation_id"

# Key used by Agent Framework for workflow.run_stream() kwargs
WORKFLOW_RUN_KWARGS_KEY = "_workflow_run_kwargs"

# Key for storing pending clarification state (shared with NL2SQL executor)
CLARIFICATION_STATE_KEY = "pending_clarification"

# Routing decision marker - indicates chat agent decided to route to NL2SQL
ROUTE_TO_NL2SQL = "nl2sql"

# Routing marker for clarification responses
ROUTE_TO_NL2SQL_CLARIFICATION = "nl2sql_clarification"

# Keywords that strongly indicate a data question (skip LLM triage)
DATA_QUESTION_KEYWORDS = {
    # Aggregation/analysis keywords
    "how many", "how much", "total", "average", "avg", "sum", "count", "max", "min",
    "top", "bottom", "best", "worst", "highest", "lowest", "most", "least",
    # Query action words
    "list", "show", "find", "get", "what are", "which", "who", "where",
    "give me", "tell me about", "display", "report",
    # Business entity keywords
    "sales", "orders", "invoice", "customer", "supplier", "product", "stock",
    "inventory", "purchase", "revenue", "quantity", "price",
}

# Keywords that indicate general chat (not data questions)
GENERAL_CHAT_KEYWORDS = {
    "hello", "hi", "hey", "thanks", "thank you", "bye", "goodbye",
    "joke", "help", "who are you", "what can you do", "your name",
}


def _is_likely_data_question(text: str) -> bool:
    """
    Fast keyword check to determine if message is likely a data question.
    
    Returns True if the message should be routed directly to NL2SQL
    without calling the LLM triage.
    """
    text_lower = text.lower().strip()
    
    # Short greetings are not data questions
    if len(text_lower) < 10:
        for kw in GENERAL_CHAT_KEYWORDS:
            if kw in text_lower:
                return False
    
    # Check for data question keywords
    for keyword in DATA_QUESTION_KEYWORDS:
        if keyword in text_lower:
            return True
    
    # If message ends with ? and is reasonably long, likely a question about data
    if text_lower.endswith("?") and len(text_lower) > 20:
        # But not if it's a general chat question
        for kw in GENERAL_CHAT_KEYWORDS:
            if kw in text_lower:
                return False
        return True
    
    return False


def _load_prompt() -> str:
    """Load prompt from prompt.md in this folder."""
    prompt_path = Path(__file__).parent / "prompt.md"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


def _parse_routing_decision(response_text: str) -> dict | None:
    """
    Parse the chat agent's response to extract routing decision.
    
    Returns:
        dict with 'route' and 'question' if routing JSON found, None otherwise
    """
    if not response_text:
        return None
    
    # Look for JSON routing decision in the response
    text = response_text.strip()
    
    # Try to parse as JSON directly
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("route") == ROUTE_TO_NL2SQL:
            return parsed
    except json.JSONDecodeError:
        pass
    
    # Look for JSON block in markdown code fence
    if "```json" in text:
        try:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                json_str = text[start:end].strip()
                parsed = json.loads(json_str)
                if isinstance(parsed, dict) and parsed.get("route") == ROUTE_TO_NL2SQL:
                    return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    
    return None


class ChatAgentExecutor(Executor):
    """
    Executor that handles user-facing chat interactions with intelligent routing.

    This executor:
    1. Receives user messages and triages them using the chat agent
    2. Routes data questions to the NL2SQL executor
    3. Handles general conversation directly
    4. Renders structured responses from NL2SQL for the user
    """

    agent: ChatAgent

    def __init__(self, chat_client: AzureAIAgentClient, executor_id: str = "chat"):
        """
        Initialize the Chat executor.

        Args:
            chat_client: The Azure AI agent client for creating the agent
            executor_id: Executor ID for workflow routing
        """
        instructions = _load_prompt()

        self.agent = ChatAgent(
            name="chat-agent",
            instructions=instructions,
            chat_client=chat_client,
        )

        super().__init__(id=executor_id)
        logger.info("ChatAgentExecutor initialized")

    async def _get_or_create_thread(self, ctx: WorkflowContext[Any, Any]) -> tuple[AgentThread, bool]:
        """
        Get existing Foundry thread from shared state or create a new one.
        
        Thread ID sources (checked in order):
        1. workflow.run_stream() kwargs (thread_id passed from API)
        2. Regular shared state (thread created by previous executor in this request)
        
        Returns:
            Tuple of (thread, is_new) where is_new indicates if this is a new thread
        """
        # First, check workflow run kwargs (set by chat.py via run_stream kwargs)
        try:
            run_kwargs = await ctx.get_shared_state(WORKFLOW_RUN_KWARGS_KEY)
            if run_kwargs and isinstance(run_kwargs, dict):
                thread_id = run_kwargs.get("thread_id")
                if thread_id:
                    logger.info("Using thread from run kwargs: %s", thread_id)
                    return self.agent.get_new_thread(service_thread_id=thread_id), False
        except KeyError:
            pass
        
        # Then, check regular shared state (may have been set by previous executor)
        try:
            thread_id = await ctx.get_shared_state(FOUNDRY_CONVERSATION_ID_KEY)
            if thread_id:
                logger.info("Using existing Foundry thread: %s", thread_id)
                return self.agent.get_new_thread(service_thread_id=thread_id), False
        except KeyError:
            pass
        
        # Create a new thread - Foundry will assign the thread ID on first run
        logger.info("Creating new Foundry thread")
        return self.agent.get_new_thread(), True
    
    async def _store_thread_id(self, ctx: WorkflowContext[Any, Any], thread: AgentThread) -> None:
        """Store the Foundry thread ID in shared state if it was created."""
        if thread.service_thread_id:
            try:
                existing = await ctx.get_shared_state(FOUNDRY_CONVERSATION_ID_KEY)
                if existing:
                    return  # Already stored
            except KeyError:
                pass
            await ctx.set_shared_state(FOUNDRY_CONVERSATION_ID_KEY, thread.service_thread_id)
            logger.info("Stored Foundry thread ID in shared state: %s", thread.service_thread_id)

    async def _triage_message(self, user_text: str, ctx: WorkflowContext[Any, Any]) -> tuple[str | None, str]:
        """
        Triage the user message - check for pending clarification, then fast keyword check, then LLM fallback.
        
        Returns:
            Tuple of (route_to, result):
            - If routing to NL2SQL as clarification: ("nl2sql_clarification", clarification_text)
            - If routing to NL2SQL as new question: ("nl2sql", question) 
            - If direct response: (None, json_response)
        """
        logger.info("Triaging user message: %s", user_text[:100])
        
        # First: Check if there's a pending clarification request
        try:
            clarification_state = await ctx.get_shared_state(CLARIFICATION_STATE_KEY)
            if clarification_state and isinstance(clarification_state, dict):
                # There's a pending clarification
                # Check if this looks like a new question or a response to clarification
                if _is_likely_data_question(user_text) and self._looks_like_new_question(user_text, clarification_state):
                    # This looks like a new question, not a clarification response
                    # Clear the clarification state
                    logger.info("Pending clarification exists but message looks like new question")
                    await ctx.set_shared_state(CLARIFICATION_STATE_KEY, None)
                else:
                    # This is likely a clarification response
                    logger.info("Routing as clarification response to NL2SQL")
                    return ROUTE_TO_NL2SQL_CLARIFICATION, user_text
        except KeyError:
            pass
        
        # Fast path: keyword-based routing for obvious data questions
        # This skips the LLM triage call and saves ~8-10 seconds
        # Note: Thread creation is deferred until first LLM call (param_extractor)
        # because Azure AI Foundry only creates threads on agent.run()
        if _is_likely_data_question(user_text):
            logger.info("Fast triage: detected data question via keywords, routing to NL2SQL")
            return ROUTE_TO_NL2SQL, user_text
        
        # Slow path: use LLM for ambiguous messages or general chat
        # This path creates the thread because we need to call the LLM
        logger.info("Using LLM triage for ambiguous message")
        
        # Get or create thread for this conversation
        thread, is_new_thread = await self._get_or_create_thread(ctx)
        
        # Set metadata for new threads to track ownership
        metadata = None
        if is_new_thread:
            user_id = get_request_user_id()
            if user_id:
                metadata = {"user_id": user_id}
                logger.info("Setting thread metadata with user_id: %s", user_id)
        
        # Run the chat agent to triage - this creates the thread in Foundry
        response = await self.agent.run(user_text, thread=thread, metadata=metadata)
        
        # Store thread ID after run
        await self._store_thread_id(ctx, thread)
        
        response_text = response.text or ""
        
        # Check if the agent decided to route to NL2SQL
        routing = _parse_routing_decision(response_text)
        if routing:
            question = routing.get("question", user_text)
            logger.info("Triage result: route to NL2SQL with question: %s", question[:100])
            return ROUTE_TO_NL2SQL, question
        
        # Agent responded directly - return the response
        logger.info("Triage result: direct response (no routing)")
        
        # Build output JSON with thread_id
        output = {
            "text": response_text,
            "thread_id": thread.service_thread_id,
        }
        return None, json.dumps(output)

    def _looks_like_new_question(self, user_text: str, clarification_state: dict) -> bool:  # noqa: ARG002
        """
        Determine if the user text looks like a new question rather than a clarification response.
        
        Heuristics:
        - If it contains question words and multiple data keywords, it's likely a new question
        - If it's short and doesn't contain question words, it's likely a clarification
        
        Note: clarification_state is kept for future enhancement (e.g., comparing to expected parameter types)
        
        Args:
            user_text: The user's message
            clarification_state: The stored clarification state (reserved for future use)
            
        Returns:
            True if this looks like a new question, False if it looks like a clarification response
        """
        text_lower = user_text.lower().strip()
        
        # Short responses are likely clarifications (e.g., "5", "Tailspin Toys", "DESC")
        if len(text_lower) < 30:
            return False
        
        # Count data keywords - if many, likely a new question
        keyword_count = sum(1 for kw in DATA_QUESTION_KEYWORDS if kw in text_lower)
        
        # If it has multiple data keywords and ends with ?, likely a new question
        if keyword_count >= 2 and "?" in text_lower:
            return True
        
        # If it starts with question words and is long, likely a new question
        question_starters = ["what", "which", "how", "show", "list", "find", "get", "tell"]
        for starter in question_starters:
            if text_lower.startswith(starter) and len(text_lower) > 40:
                return True
        
        return False

    @handler
    async def handle_chat_message(
        self,
        message: ChatMessage,
        ctx: WorkflowContext[NL2SQLMessage, str]
    ) -> None:
        """
        Handle a single ChatMessage with intelligent triage.

        The chat agent decides whether to:
        1. Route clarification responses to NL2SQL executor
        2. Route data questions to NL2SQL executor
        3. Respond directly to general conversation

        Args:
            message: Single chat message
            ctx: Workflow context for sending to next executor or yielding response
        """
        user_text = message.text or ""
        logger.info("ChatAgentExecutor received user message: %s", user_text[:100] if user_text else "(empty)")

        # Triage the message
        route_to, result = await self._triage_message(user_text, ctx)
        
        if route_to == ROUTE_TO_NL2SQL_CLARIFICATION:
            # Forward to NL2SQL executor as clarification using typed wrapper
            clarification_msg = ClarificationMessage(clarification_text=result)
            await ctx.send_message(clarification_msg, target_id="nl2sql")
        elif route_to == ROUTE_TO_NL2SQL:
            # Forward to NL2SQL executor as new question
            await ctx.send_message(result)
        else:
            # Direct response - yield as final output
            await ctx.yield_output(result)

    @handler
    async def handle_user_messages(
        self,
        messages: list[ChatMessage],
        ctx: WorkflowContext[NL2SQLMessage, str]
    ) -> None:
        """
        Handle a list of ChatMessages with intelligent triage.

        Args:
            messages: List of chat messages
            ctx: Workflow context for sending to next executor or yielding response
        """
        # Get the last user message
        user_text = ""
        for msg in reversed(messages):
            if msg.role == Role.USER and msg.text:
                user_text = msg.text
                break

        logger.info("ChatAgentExecutor received user messages: %s", user_text[:100] if user_text else "(empty)")

        # Triage the message
        route_to, result = await self._triage_message(user_text, ctx)
        
        if route_to == ROUTE_TO_NL2SQL_CLARIFICATION:
            # Forward to NL2SQL executor as clarification using typed wrapper
            clarification_msg = ClarificationMessage(clarification_text=result)
            await ctx.send_message(clarification_msg, target_id="nl2sql")
        elif route_to == ROUTE_TO_NL2SQL:
            # Forward to NL2SQL executor as new question
            await ctx.send_message(result)
        else:
            # Direct response - yield as final output
            await ctx.yield_output(result)

    @handler
    async def handle_nl2sql_response(
        self,
        response_json: str,
        ctx: WorkflowContext[Never, str]
    ) -> None:
        """
        Handle structured response from NL2SQL and render for user.

        Args:
            response_json: JSON string containing NL2SQL response with query results
            ctx: Workflow context for yielding final output
        """
        logger.info("ChatAgentExecutor rendering NL2SQL response")

        # Deserialize the JSON string back to NL2SQLResponse model
        response = NL2SQLResponse.model_validate_json(response_json)

        # Get thread ID from shared state (set during triage phase)
        foundry_thread_id = None
        try:
            foundry_thread_id = await ctx.get_shared_state(FOUNDRY_CONVERSATION_ID_KEY)
        except KeyError:
            pass

        # Yield structured output with text, thread ID, and raw NL2SQL data for tool UI
        # Skip the LLM render call - use direct rendering for ~10s faster response
        output = {
            "text": self._fallback_render(response),  # Direct render without LLM
            "thread_id": foundry_thread_id,
            "tool_call": {
                "tool_name": "nl2sql_query",
                "tool_call_id": f"nl2sql_{id(response)}",
                "args": {},  # Original question is not available here
                "result": {
                    "sql_query": response.sql_query,
                    "sql_response": response.sql_response,
                    "columns": response.columns,
                    "row_count": response.row_count,
                    "confidence_score": response.confidence_score,
                    "used_cached_query": response.used_cached_query,
                    "query_source": response.query_source,
                    "error": response.error,
                    "observations": None,  # No LLM-generated insights
                }
            }
        }
        await ctx.yield_output(json.dumps(output))

    def _build_render_prompt(self, response: NL2SQLResponse) -> str:
        """Build a prompt for the chat agent to render the response."""
        if response.error:
            return f"""Please help the user understand this error from the data query:

Error: {response.error}

SQL Query attempted: {response.sql_query or 'None'}

Provide a helpful explanation of what went wrong and suggest how they might rephrase their question."""

        # Format the data for the agent
        data_preview = ""
        if response.sql_response:
            sample_rows = response.sql_response[:10]
            data_preview = json.dumps(sample_rows, indent=2, default=str)

        cache_info = ""
        if response.used_cached_query:
            cache_info = f"This used a pre-tested cached query with confidence score: {response.confidence_score:.2f}"
        else:
            cache_info = "This query was generated for this specific question."

        return f"""Please present these data query results to the user in a clear, well-formatted way:

**Query Results Summary:**
- Total rows returned: {response.row_count}
- Columns: {', '.join(response.columns)}
- {cache_info}

**SQL Query Used:**
```sql
{response.sql_query}
```

**Data (sample of up to 10 rows):**
```json
{data_preview}
```

Format this nicely with a markdown table and helpful context. If the data is empty, explain that no matching records were found."""

    def _fallback_render(self, response: NL2SQLResponse) -> str:
        """Fallback rendering if the agent fails."""
        if response.error:
            return f"**Error:** {response.error}"

        lines = [f"**Query Results** ({response.row_count} rows)\n"]

        if response.columns and response.sql_response:
            lines.append("| " + " | ".join(response.columns) + " |")
            lines.append("| " + " | ".join(["---"] * len(response.columns)) + " |")

            for row in response.sql_response[:10]:
                values = [str(row.get(col, "")) for col in response.columns]
                lines.append("| " + " | ".join(values) + " |")

        if response.sql_query:
            lines.append(f"\n<details><summary>SQL Query</summary>\n\n```sql\n{response.sql_query}\n```\n</details>")

        return "\n".join(lines)
