"""
SQL generation and validation models.

These models represent SQL drafts being constructed and validated
through the workflow pipeline.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from .extraction import MissingParameter
from .schema import ParameterDefinition, TableMetadata


class SQLDraft(BaseModel):
    """
    Unified response from SQL generation (template-based or dynamic).

    Represents a draft SQL query ready for validation/execution,
    or a clarification request if parameters are missing.
    """

    status: Literal["success", "needs_clarification", "error"] = Field(
        description="Draft result status"
    )

    source: Literal["template", "dynamic"] = Field(
        description="How the SQL was generated: 'template' (parameter extraction) or 'dynamic' (query builder)"
    )

    completed_sql: str | None = Field(
        default=None,
        description="The SQL query with all parameters substituted (if success)"
    )

    user_query: str = Field(
        default="",
        description="The original user question"
    )

    reasoning: str | None = Field(
        default=None,
        description="Explanation of how the query was constructed"
    )

    retry_count: int = Field(
        default=0,
        description="Number of retry attempts (for validation retry flow)"
    )

    # Template-based fields (source="template")
    template_id: str | None = Field(
        default=None,
        description="Template ID used (for re-submission on clarification)"
    )
    template_json: str | None = Field(
        default=None,
        description="Full template JSON (for resumption after clarification)"
    )
    extracted_parameters: dict[str, Any] | None = Field(
        default=None,
        description="Parameter name -> extracted value mapping"
    )

    # Clarification flow (status="needs_clarification")
    missing_parameters: list[MissingParameter] | None = Field(
        default=None,
        description="Parameters that need user clarification"
    )
    clarification_prompt: str | None = Field(
        default=None,
        description="LLM-generated question asking for missing info"
    )

    # Dynamic generation fields (source="dynamic")
    tables_used: list[str] = Field(
        default_factory=list,
        description="Tables used in the query (for validation)"
    )

    # Parameter validation fields
    params_validated: bool = Field(
        default=False,
        description="Whether parameter values have been validated"
    )
    parameter_definitions: list[ParameterDefinition] = Field(
        default_factory=list,
        description="Parameter definitions for validation (passed from template)"
    )
    parameter_violations: list[str] = Field(
        default_factory=list,
        description="List of parameter validation failures"
    )

    # Query validation fields
    query_validated: bool = Field(
        default=False,
        description="Whether the SQL query has been validated"
    )
    query_violations: list[str] = Field(
        default_factory=list,
        description="List of query validation failures"
    )
    query_warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking query validation warnings"
    )

    error: str | None = Field(
        default=None,
        description="Error message if status is 'error'"
    )


class SQLDraftMessage(BaseModel):
    """
    A wrapper for SQL draft responses.

    This type is used by the workflow to distinguish SQL draft responses
    from other message types.
    """

    source: str = Field(
        default="",
        description="The executor that sent this message (e.g., 'param_extractor', 'param_validator', 'query_validator')"
    )
    response_json: str = Field(description="The JSON-encoded SQLDraft")


class QueryBuilderRequest(BaseModel):
    """Request to build a SQL query from table metadata."""

    user_query: str = Field(description="The user's original question")
    tables: list[TableMetadata] = Field(
        description="Relevant tables to use for query generation"
    )
    retry_count: int = Field(
        default=0,
        description="Number of retry attempts (used for validation retry flow)"
    )


class QueryBuilderRequestMessage(BaseModel):
    """
    A wrapper for query builder requests.

    This type is used by the workflow to distinguish query builder requests
    from other message types.
    """

    request_json: str = Field(description="The JSON-encoded QueryBuilderRequest")
