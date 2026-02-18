"""DataAssistant — manages chat sessions and invokes the NL2SQL workflow.

This agent owns the Foundry thread and conversation history.
It classifies user intent, invokes the NL2SQL workflow for data questions,
and handles conversational refinements.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_framework import AgentThread, ChatAgent
from models import NL2SQLRequest, NL2SQLResponse, SchemaSuggestion

logger = logging.getLogger(__name__)

SCHEMA_SUGGESTIONS: dict[str, list[SchemaSuggestion]] = {
    "sales": [
        SchemaSuggestion(
            title="Order trends",
            prompt="Show me order trends over the last 6 months",
        ),
        SchemaSuggestion(
            title="Invoice details",
            prompt="Drill into invoice line items for the most recent orders",
        ),
        SchemaSuggestion(
            title="Customer categories",
            prompt="Compare total revenue across customer buying groups",
        ),
        SchemaSuggestion(
            title="Special deals",
            prompt="Show active special deals and their discount percentages",
        ),
    ],
    "purchasing": [
        SchemaSuggestion(
            title="PO status",
            prompt="Track purchase order status and expected delivery dates",
        ),
        SchemaSuggestion(
            title="Supplier performance",
            prompt="Analyze supplier categories and order volumes",
        ),
        SchemaSuggestion(
            title="Supplier transactions",
            prompt="Review recent supplier transaction history",
        ),
    ],
    "warehouse": [
        SchemaSuggestion(
            title="Stock levels",
            prompt="Check current stock levels and holdings across warehouses",
        ),
        SchemaSuggestion(
            title="Stock categories",
            prompt="Explore stock groups and item categories",
        ),
        SchemaSuggestion(
            title="Stock transactions",
            prompt="Review stock transaction history for the last 30 days",
        ),
        SchemaSuggestion(
            title="Package types",
            prompt="Analyze color and package type distributions for stock items",
        ),
    ],
    "application": [
        SchemaSuggestion(
            title="People & contacts",
            prompt="Look up people, their roles, and contact information",
        ),
        SchemaSuggestion(
            title="Geographic data",
            prompt="Explore cities, states, and countries in the system",
        ),
        SchemaSuggestion(
            title="Delivery methods",
            prompt="Review available delivery and payment methods",
        ),
    ],
}


def _detect_schema_area(tables: list[str]) -> str | None:
    """Detect schema area from fully-qualified table names.

    Uses the FROM clause's primary table (first in list). Extracts the schema
    prefix (e.g., 'Sales.Orders' -> 'sales').

    Args:
        tables: List of fully-qualified table names (e.g., ['Sales.Orders', 'Application.People'])

    Returns:
        Lowercase schema area name, or None if undetectable.
    """
    if not tables:
        return None
    first_table = tables[0]
    if "." not in first_table:
        return None
    area = first_table.split(".")[0].lower()
    if area not in SCHEMA_SUGGESTIONS:
        return None
    return area


@dataclass
class ConversationContext:
    """Tracks the context of the current conversation for refinements."""

    # Template-based query context
    last_template_json: str | None = None
    last_params: dict[str, Any] = field(default_factory=dict)
    last_defaults_used: dict[str, Any] = field(default_factory=dict)
    last_query: str = ""

    # Dynamic query context
    last_sql: str | None = None
    last_tables: list[str] = field(default_factory=list)
    last_tables_json: str | None = None
    last_question: str = ""
    query_source: str = ""  # "template" or "dynamic"

    # Schema area context
    current_schema_area: str | None = None
    schema_exploration_depth: int = 0


@dataclass
class ClassificationResult:
    """Result of intent classification."""

    intent: str  # "data_query", "refinement", "conversation"
    query: str = ""  # The query to process (may be rewritten for refinements)
    param_overrides: dict = field(default_factory=dict)  # For refinements


def load_assistant_prompt() -> str:
    """Load the DataAssistant instructions prompt."""
    prompt_path = Path(__file__).parent / "assistant_prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


class DataAssistant:
    """Manages conversation flow and invokes the NL2SQL workflow.

    Responsibilities:
    1. Own the Foundry thread and conversation history
    2. Classify user intent (data query, refinement, conversation)
    3. Invoke NL2SQL workflow for data questions
    4. Handle conversational refinements using previous context
    5. Render results back to the user
    """

    def __init__(self, agent: ChatAgent, thread_id: str | None = None) -> None:
        """Initialize the DataAssistant.

        Args:
            agent: Pre-configured ChatAgent for LLM calls
            thread_id: Optional existing Foundry thread ID to resume
        """
        self.agent = agent
        self.context = ConversationContext()
        self._thread: AgentThread | None = None
        self._initial_thread_id = thread_id

        logger.info("DataAssistant initialized (thread_id=%s)", thread_id)

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
            self._thread = self.agent.get_new_thread(service_thread_id=self._initial_thread_id)
            logger.info("Resumed thread: %s", self._initial_thread_id)
        else:
            self._thread = self.agent.get_new_thread()
            logger.info("Created new thread")

        return self._thread

    async def classify_intent(self, user_message: str) -> ClassificationResult:
        """Classify the user's intent using the LLM.

        The LLM considers conversation history to detect refinements.

        Returns:
            ClassificationResult with intent type and any extracted overrides.
        """
        thread = await self.get_or_create_thread()

        context_info = ""
        if self.context.query_source == "template" and self.context.last_template_json:
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

        result = await self.agent.run(
            classification_prompt,
            thread=thread,
        )

        response_text = result.text or ""

        try:
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(response_text[json_start:json_end])

                intent = parsed.get("intent", "conversation")
                query = parsed.get("query", user_message)
                overrides = parsed.get("param_overrides", {})

                logger.info(
                    "Classified intent: %s (query=%s, overrides=%s)", intent, query[:50], overrides
                )
                return ClassificationResult(
                    intent=intent,
                    query=query,
                    param_overrides=overrides,
                )
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse classification response: %s", e)

        return ClassificationResult(intent="conversation")

    async def handle_conversation(self, user_message: str) -> str:
        """Handle general conversation using the LLM.

        Returns:
            The agent's conversational response.
        """
        thread = await self.get_or_create_thread()

        result = await self.agent.run(
            user_message,
            thread=thread,
        )

        return result.text or ""

    def build_nl2sql_request(self, classification: ClassificationResult) -> NL2SQLRequest:
        """Build an NL2SQL request based on the classification.

        For refinements, includes the previous template/query context.
        """
        if classification.intent == "refinement":
            if self.context.query_source == "template" and self.context.last_template_json:
                return NL2SQLRequest(
                    user_query=classification.query,
                    is_refinement=True,
                    previous_template_json=self.context.last_template_json,
                    base_params=self.context.last_params,
                    param_overrides=classification.param_overrides,
                )
            if self.context.query_source == "dynamic" and self.context.last_sql:
                return NL2SQLRequest(
                    user_query=classification.query,
                    is_refinement=True,
                    previous_sql=self.context.last_sql,
                    previous_tables=self.context.last_tables,
                    previous_tables_json=self.context.last_tables_json,
                    previous_question=self.context.last_question,
                )

        return NL2SQLRequest(
            user_query=classification.query,
            is_refinement=False,
        )

    _CROSS_AREA_DEPTH_THRESHOLD = 3

    @staticmethod
    def _build_suggestions(
        schema_area: str | None,
        depth: int,
        *,
        has_results: bool = True,
    ) -> list[SchemaSuggestion]:
        """Select contextual follow-up suggestions based on schema area and depth.

        Args:
            schema_area: Current schema area (e.g., 'sales') or None.
            depth: How many consecutive queries in this area.
            has_results: Whether the query returned data (for empty-result recovery).

        Returns:
            2-3 relevant suggestions, or empty list if area is None.
        """
        if schema_area is None:
            return []

        area_suggestions = SCHEMA_SUGGESTIONS.get(schema_area, [])
        if not area_suggestions:
            return []

        count = len(area_suggestions)
        start = (depth - 1) % count
        rotated = [*area_suggestions[start:], *area_suggestions[:start]]
        selected = rotated[:3]

        if depth >= DataAssistant._CROSS_AREA_DEPTH_THRESHOLD:
            sorted_areas = sorted(SCHEMA_SUGGESTIONS.keys())
            current_idx = sorted_areas.index(schema_area)
            next_area = sorted_areas[(current_idx + 1) % len(sorted_areas)]
            cross_suggestion = SCHEMA_SUGGESTIONS[next_area][0]
            selected = [*selected[:2], cross_suggestion]

        if not has_results:
            recovery = SchemaSuggestion(
                title="Try broader filters",
                prompt=f"Show me all data in the {schema_area} area",
            )
            selected = [recovery, *selected[:2]]

        return selected

    def update_context(
        self, response: NL2SQLResponse, template_json: str | None, params: dict
    ) -> None:
        """Update conversation context after a successful query.

        Args:
            response: The NL2SQL response
            template_json: JSON of the template used (if any)
            params: The parameters that were used
        """
        if not response.error and response.sql_query:
            self.context.query_source = response.query_source

            if response.query_source == "template" and template_json:
                self.context.last_template_json = template_json
                self.context.last_params = params
                self.context.last_defaults_used = response.defaults_used
                self.context.last_query = response.sql_query
                self.context.last_sql = None
                self.context.last_tables = []
                self.context.last_tables_json = None
                self.context.last_question = ""
            else:
                self.context.last_sql = response.sql_query
                self.context.last_tables = response.tables_used
                self.context.last_tables_json = response.tables_metadata_json
                self.context.last_question = response.original_question
                self.context.last_template_json = None
                self.context.last_params = {}
                self.context.last_defaults_used = {}
                self.context.last_query = response.sql_query

            if response.tables_used:
                tables = response.tables_used
            else:
                tables = re.findall(r"(?:FROM|JOIN)\s+([\w.]+)", response.sql_query, re.IGNORECASE)

            detected_area = _detect_schema_area(tables)
            if detected_area == self.context.current_schema_area:
                self.context.schema_exploration_depth += 1
            else:
                self.context.schema_exploration_depth = 1
            self.context.current_schema_area = detected_area

            logger.info(
                "Updated context for %s query refinement (schema_area=%s, depth=%d)",
                self.context.query_source,
                self.context.current_schema_area,
                self.context.schema_exploration_depth,
            )

    def enrich_response(self, response: NL2SQLResponse) -> NL2SQLResponse:
        """Add contextual suggestions to the response based on schema area.

        Called after update_context() so schema area is current.
        """
        if not response.error and not response.needs_clarification:
            has_results = len(response.sql_response) > 0
            suggestions = self._build_suggestions(
                self.context.current_schema_area,
                self.context.schema_exploration_depth,
                has_results=has_results,
            )
            response.suggestions = suggestions
        return response

    def render_response(self, response: NL2SQLResponse) -> dict:
        """Render the NL2SQL response for the frontend.

        Returns:
            A structured dict with text, thread_id, and tool_call data.
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
                    "query_source": response.query_source,
                    "error": response.error,
                    "observations": None,
                    "needs_clarification": response.needs_clarification,
                    "clarification": response.clarification.model_dump()
                    if response.clarification
                    else None,
                    "defaults_used": response.defaults_used,
                    "suggestions": [s.model_dump() for s in response.suggestions],
                    "hidden_columns": response.hidden_columns,
                    "query_summary": response.query_summary or None,
                    "query_confidence": response.query_confidence,
                    "error_suggestions": [s.model_dump() for s in response.error_suggestions],
                },
            },
        }

    @staticmethod
    def _format_response_text(response: NL2SQLResponse) -> str:
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
            lines.append(
                f"\n<details><summary>SQL Query</summary>\n\n```sql\n{response.sql_query}\n```\n</details>"
            )

        return "\n".join(lines)
