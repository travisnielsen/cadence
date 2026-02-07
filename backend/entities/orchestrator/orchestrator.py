"""
Standalone Conversation Orchestrator that manages chat sessions.

This agent owns the Foundry thread and conversation history.
It classifies user intent, invokes the NL2SQL workflow for data questions,
and handles conversational refinements.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from agent_framework import AgentThread, ChatAgent as MAFChatAgent
from agent_framework_azure_ai import AzureAIClient

# Support both DevUI and FastAPI import patterns
try:
    from models import NL2SQLRequest, NL2SQLResponse  # type: ignore[import-not-found]
except ImportError:
    from models import NL2SQLRequest, NL2SQLResponse

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """
    Tracks the context of the current conversation for refinements.
    """
    # Template-based query context
    last_template_json: str | None = None
    last_params: dict = field(default_factory=dict)
    last_defaults_used: dict = field(default_factory=dict)
    last_query: str = ""
    
    # Dynamic query context
    last_sql: str | None = None
    last_tables: list = field(default_factory=list)  # Table names for logging
    last_tables_json: str | None = None  # Full TableMetadata JSON for reuse
    last_question: str = ""
    query_source: str = ""  # "template" or "dynamic"


@dataclass
class ClassificationResult:
    """Result of intent classification."""
    intent: str  # "data_query", "refinement", "conversation"
    query: str = ""  # The query to process (may be rewritten for refinements)
    param_overrides: dict = field(default_factory=dict)  # For refinements


def _load_prompt() -> str:
    """Load the conversation orchestrator prompt."""
    prompt_path = Path(__file__).parent / "orchestrator_prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


class ConversationOrchestrator:
    """
    Standalone orchestrator that manages conversation flow.
    
    Responsibilities:
    1. Own the Foundry thread and conversation history
    2. Classify user intent (data query, refinement, conversation)
    3. Invoke NL2SQL workflow for data questions
    4. Handle conversational refinements using previous context
    5. Render results back to the user
    """

    def __init__(self, client: AzureAIClient, thread_id: str | None = None):
        """
        Initialize the conversation orchestrator.
        
        Args:
            client: Azure AI Agent client for LLM calls
            thread_id: Optional existing Foundry thread ID to resume
        """
        self.client = client
        self.context = ConversationContext()
        self._thread: AgentThread | None = None
        self._initial_thread_id = thread_id
        
        # Create the underlying MAF agent for LLM calls
        self.agent = MAFChatAgent(
            name="ConversationOrchestrator",
            instructions=_load_prompt(),
            chat_client=client,
        )
        
        logger.info("ConversationOrchestrator initialized (thread_id=%s)", thread_id)

    @property
    def thread_id(self) -> str | None:
        """Get the current Foundry thread ID."""
        if self._thread and self._thread.service_thread_id:
            return self._thread.service_thread_id
        return self._initial_thread_id

    async def get_or_create_thread(self) -> AgentThread:
        """Get or create the Foundry thread."""
        if self._thread is not None:
            return self._thread
        
        if self._initial_thread_id:
            # Resume existing thread
            self._thread = self.agent.get_new_thread(service_thread_id=self._initial_thread_id)
            logger.info("Resumed thread: %s", self._initial_thread_id)
        else:
            # Create new thread
            self._thread = self.agent.get_new_thread()
            logger.info("Created new thread")
        
        return self._thread

    async def classify_intent(self, user_message: str) -> ClassificationResult:
        """
        Classify the user's intent using the LLM.
        
        The LLM considers conversation history to detect refinements.
        
        Returns:
            ClassificationResult with intent type and any extracted overrides
        """
        thread = await self.get_or_create_thread()
        
        # Build classification prompt with context
        context_info = ""
        if self.context.query_source == "template" and self.context.last_template_json:
            # Template-based query context
            try:
                template_data = json.loads(self.context.last_template_json)
                param_names = [p.get("name") for p in template_data.get("parameters", [])]
                context_info = f"""
Previous query context (TEMPLATE-BASED):
- Question: {self.context.last_query}
- Parameters used: {json.dumps(self.context.last_params)}
- Available parameters to modify: {param_names}
- Defaults that were applied: {json.dumps(self.context.last_defaults_used)}
"""
            except json.JSONDecodeError:
                pass
        elif self.context.query_source == "dynamic" and self.context.last_sql:
            # Dynamic query context
            context_info = f"""
Previous query context (DYNAMIC):
- Original question: {self.context.last_question}
- Tables used: {self.context.last_tables}
- SQL executed: {self.context.last_sql[:500]}...
"""
        
        classification_prompt = f"""Classify this user message and respond with JSON only.

{context_info}

User message: {user_message}

Respond with ONE of these JSON formats:

1. For a NEW data question (asking about business data like sales, orders, customers, products):
{{"intent": "data_query", "query": "<the question>"}}

2. For a REFINEMENT of the previous query (e.g., "show me for 90 days", "what about last month", "make it 20", "filter by X"):
{{"intent": "refinement", "query": "<describe what the user wants changed>"}}

3. For general CONVERSATION (greetings, jokes, help, off-topic):
{{"intent": "conversation"}}

Important: A refinement ONLY applies if there was a previous query and the user is asking to modify it.

JSON response:"""

        # Call LLM for classification
        result = await self.agent.run(
            classification_prompt,
            thread=thread,
        )
        
        # Parse the response - AgentRunResponse has .text property
        response_text = result.text or ""
        
        try:
            # Extract JSON from response
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(response_text[json_start:json_end])
                
                intent = parsed.get("intent", "conversation")
                query = parsed.get("query", user_message)
                overrides = parsed.get("param_overrides", {})
                
                logger.info("Classified intent: %s (query=%s, overrides=%s)", intent, query[:50], overrides)
                return ClassificationResult(
                    intent=intent,
                    query=query,
                    param_overrides=overrides,
                )
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse classification response: %s", e)
        
        # Default to conversation if classification fails
        return ClassificationResult(intent="conversation")

    async def handle_conversation(self, user_message: str) -> str:
        """
        Handle general conversation using the LLM.
        
        Returns:
            The agent's conversational response
        """
        thread = await self.get_or_create_thread()
        
        result = await self.agent.run(
            user_message,
            thread=thread,
        )
        
        # AgentRunResponse has .text property
        response_text = result.text or ""
        
        return response_text

    def build_nl2sql_request(
        self, 
        classification: ClassificationResult
    ) -> NL2SQLRequest:
        """
        Build an NL2SQL request based on the classification.
        
        For refinements, includes the previous template/query context.
        """
        if classification.intent == "refinement":
            if self.context.query_source == "template" and self.context.last_template_json:
                # Template-based refinement
                return NL2SQLRequest(
                    user_query=classification.query,
                    is_refinement=True,
                    previous_template_json=self.context.last_template_json,
                    base_params=self.context.last_params,
                    param_overrides=classification.param_overrides,
                )
            elif self.context.query_source == "dynamic" and self.context.last_sql:
                # Dynamic query refinement - pass full table metadata
                return NL2SQLRequest(
                    user_query=classification.query,
                    is_refinement=True,
                    previous_sql=self.context.last_sql,
                    previous_tables=self.context.last_tables,
                    previous_tables_json=self.context.last_tables_json,
                    previous_question=self.context.last_question,
                )
        
        # New query
        return NL2SQLRequest(
            user_query=classification.query,
            is_refinement=False,
        )

    def update_context(
        self, 
        response: NL2SQLResponse, 
        template_json: str | None, 
        params: dict
    ) -> None:
        """
        Update conversation context after a successful query.
        
        Args:
            response: The NL2SQL response
            template_json: JSON of the template used (if any)
            params: The parameters that were used
        """
        if not response.error and response.sql_query:
            # Track query source for refinement handling
            self.context.query_source = response.query_source
            
            if response.query_source == "template" and template_json:
                # Template-based query context
                self.context.last_template_json = template_json
                self.context.last_params = params
                self.context.last_defaults_used = response.defaults_used
                self.context.last_query = response.sql_query
                # Clear dynamic context
                self.context.last_sql = None
                self.context.last_tables = []
                self.context.last_tables_json = None
                self.context.last_question = ""
            else:
                # Dynamic query context
                self.context.last_sql = response.sql_query
                self.context.last_tables = response.tables_used
                self.context.last_tables_json = response.tables_metadata_json
                self.context.last_question = response.original_question
                # Clear template context
                self.context.last_template_json = None
                self.context.last_params = {}
                self.context.last_defaults_used = {}
                self.context.last_query = response.sql_query
            
            logger.info(
                "Updated conversation context for %s query refinement",
                self.context.query_source
            )

    def render_response(self, response: NL2SQLResponse) -> dict:
        """
        Render the NL2SQL response for the frontend.
        
        Returns:
            A structured dict with text, thread_id, and tool_call data
        """
        text = self._format_response_text(response)
        
        return {
            "text": text,
            "thread_id": self.thread_id,
            "tool_call": {
                "tool_name": "nl2sql_query",
                "tool_call_id": f"nl2sql_{id(response)}",
                "args": {},
                "result": {
                    "sql_query": response.sql_query,
                    "sql_response": response.sql_response,
                    "columns": response.columns,
                    "row_count": response.row_count,
                    "confidence_score": response.confidence_score,
                    "used_cached_query": response.used_cached_query,
                    "query_source": response.query_source,
                    "error": response.error,
                    "observations": None,
                    "needs_clarification": response.needs_clarification,
                    "clarification": response.clarification.model_dump() if response.clarification else None,
                    "defaults_used": response.defaults_used,
                }
            }
        }

    def _format_response_text(self, response: NL2SQLResponse) -> str:
        """Format the response as markdown text."""
        if response.needs_clarification and response.clarification:
            clarification = response.clarification
            lines = [f"**{clarification.prompt}**\n"]
            if clarification.allowed_values:
                lines.append("Valid options: " + ", ".join(clarification.allowed_values))
            return "\n".join(lines)
        
        if response.error:
            return f"**Error:** {response.error}"

        lines = []
        
        # Add note about defaults used if any
        if response.defaults_used:
            descriptions = list(response.defaults_used.values())
            if len(descriptions) == 1:
                lines.append(f"*Using default: {descriptions[0]}*\n")
            else:
                lines.append(f"*Using defaults: {', '.join(descriptions)}*\n")
        
        lines.append(f"**Query Results** ({response.row_count} rows)\n")

        if response.columns and response.sql_response:
            lines.append("| " + " | ".join(response.columns) + " |")
            lines.append("| " + " | ".join(["---"] * len(response.columns)) + " |")

            for row in response.sql_response[:10]:
                values = [str(row.get(col, "")) for col in response.columns]
                lines.append("| " + " | ".join(values) + " |")

        if response.sql_query:
            lines.append(f"\n<details><summary>SQL Query</summary>\n\n```sql\n{response.sql_query}\n```\n</details>")

        return "\n".join(lines)
