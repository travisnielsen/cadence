"""
Parameter extraction workflow models.

These models support extracting parameters from user queries
and handling clarification requests.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .schema import QueryTemplate


@dataclass
class ClarificationRequest:
    """
    Request for clarification sent via ctx.request_info().

    This is a dataclass (not Pydantic) for compatibility with Agent Framework's
    request_info/response_handler pattern. The workflow will pause when this
    is emitted, and resume when a response is provided via send_responses_streaming.
    """

    parameter_name: str
    """The name of the parameter that needs clarification."""

    prompt: str
    """Human-readable prompt explaining what's needed."""

    allowed_values: list[str] = field(default_factory=list)
    """Valid values the user can choose from."""

    original_question: str = ""
    """The user's original question for context."""

    template_id: str = ""
    """ID of the template being used."""

    template_json: str = ""
    """JSON-serialized template for resuming extraction."""

    extracted_parameters: dict = field(default_factory=dict)
    """Parameters already extracted from the query."""


class MissingParameter(BaseModel):
    """A parameter that could not be inferred and requires clarification."""

    name: str = Field(description="Parameter name")
    description: str = Field(default="", description="Human-readable description of what's needed")
    validation_hint: str = Field(
        default="", description="Hint about valid values (e.g., 'Enter a number between 1 and 100')"
    )


class ClarificationMessage(BaseModel):
    """
    A wrapper for user clarification responses.

    This type is used by the workflow to distinguish between new questions
    (sent as plain strings) and clarification responses (wrapped in this type).
    """

    clarification_text: str = Field(description="The user's clarification response")


class ExtractionRequestMessage(BaseModel):
    """
    A wrapper for parameter extraction requests.

    This type is used by the workflow to distinguish extraction requests
    from other string messages sent to NL2SQL.
    """

    request_json: str = Field(description="The JSON-encoded extraction request")


class ParameterExtractionRequest(BaseModel):
    """Request to extract parameters from a user query using a template."""

    user_query: str = Field(description="The user's original question")
    template: QueryTemplate = Field(description="The matched query template")


class NL2SQLRequest(BaseModel):
    """
    Request to execute an NL2SQL query.

    This model supports both new queries and refinements of previous queries.
    For refinements, the previous_template and base_params provide context.
    """

    user_query: str = Field(description="The user's question or refinement request")

    is_refinement: bool = Field(
        default=False, description="Whether this is a refinement of a previous query"
    )

    previous_template_json: str | None = Field(
        default=None, description="JSON-serialized template from previous query (for refinements)"
    )

    base_params: dict | None = Field(
        default=None,
        description="Parameters from the previous query to use as base (for refinements)",
    )

    param_overrides: dict | None = Field(
        default=None,
        description="Specific parameter overrides extracted from the refinement request",
    )

    # Dynamic query refinement context
    previous_sql: str | None = Field(
        default=None, description="The previous SQL query (for dynamic refinements)"
    )

    previous_tables: list[str] | None = Field(
        default=None, description="Table names used in the previous query (for logging)"
    )

    previous_tables_json: str | None = Field(
        default=None,
        description="JSON of TableMetadata objects from previous query (for refinements)",
    )

    previous_question: str | None = Field(
        default=None,
        description="The original question from the previous query (for dynamic refinements)",
    )
