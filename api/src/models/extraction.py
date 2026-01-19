"""
Parameter extraction workflow models.

These models support extracting parameters from user queries
and handling clarification requests.
"""

from pydantic import BaseModel, Field

from .schema import QueryTemplate


class MissingParameter(BaseModel):
    """A parameter that could not be inferred and requires clarification."""

    name: str = Field(description="Parameter name")
    description: str = Field(
        default="",
        description="Human-readable description of what's needed"
    )
    validation_hint: str = Field(
        default="",
        description="Hint about valid values (e.g., 'Enter a number between 1 and 100')"
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
