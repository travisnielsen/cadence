"""
Parameter extraction workflow models.

These models support extracting parameters from user queries
and handling clarification requests.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from .schema import QueryTemplate


@dataclass
class ClarificationRequest:
    """Request for parameter clarification.

    A dataclass representing a request to the user for missing or
    ambiguous parameter information.
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


class ParameterConfidence(BaseModel):
    """Confidence score for a single resolved parameter."""

    name: str = Field(description="Parameter name")
    value: Any = Field(description="Resolved value")
    confidence: float = Field(description="Confidence score 0.0-1.0")
    resolution_method: Literal[
        "exact_match",
        "fuzzy_match",
        "llm_validated",
        "llm_unvalidated",
        "default_value",
        "default_policy",
        "llm_failed_validation",
    ] = Field(description="How the value was resolved")


class MissingParameter(BaseModel):
    """A parameter that could not be inferred and requires clarification."""

    name: str = Field(description="Parameter name")
    description: str = Field(default="", description="Human-readable description of what's needed")
    validation_hint: str = Field(
        default="", description="Hint about valid values (e.g., 'Enter a number between 1 and 100')"
    )
    best_guess: str | None = Field(default=None, description="Best guess for the parameter value")
    guess_confidence: float = Field(default=0.0, description="Confidence in the best guess 0.0-1.0")
    alternatives: list[str] | None = Field(
        default=None, description="Alternative values the user might mean"
    )


class ParameterExtractionRequest(BaseModel):
    """Request to extract parameters from a user query using a template."""

    user_query: str = Field(description="The user's original question")
    template: QueryTemplate = Field(description="The matched query template")
    previously_extracted: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters already extracted from prior turns (should not be re-extracted)",
    )


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
