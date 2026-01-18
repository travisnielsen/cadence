"""
Shared models for entities.

These models are used across multiple agents and the workflow.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


# =============================================================================
# Parameter Extraction Models
# =============================================================================


class ParameterValidation(BaseModel):
    """Validation rules for a query template parameter."""

    type: str = Field(
        description="Data type: 'integer', 'string', 'date', etc."
    )
    min: int | float | str | None = Field(
        default=None,
        description="Minimum value (for integers/dates)"
    )
    max: int | float | str | None = Field(
        default=None,
        description="Maximum value (for integers/dates)"
    )
    allowed_values: list[str] | None = Field(
        default=None,
        description="List of allowed values (for string enums)"
    )


class ParameterNormalization(BaseModel):
    """Normalization rules for a parameter value."""

    lowercase: bool = Field(default=False)
    strip: bool = Field(default=True)


class ParameterDefinition(BaseModel):
    """Definition of a parameter in a query template."""

    name: str = Field(description="Parameter name matching %{{name}}% token")
    column: str | None = Field(
        default=None,
        description="Database column this parameter maps to"
    )
    required: bool = Field(default=True)
    ask_if_missing: bool = Field(
        default=False,
        description="If true and no default, ask user for clarification"
    )
    default_value: Any = Field(
        default=None,
        description="Default value if not provided/inferred"
    )
    default_policy: str | None = Field(
        default=None,
        description="Policy for computing default (e.g., 'current_date')"
    )
    confidence_weight: float = Field(
        default=0.0,
        description="Weight for confidence scoring"
    )
    normalization: ParameterNormalization | None = Field(default=None)
    validation: ParameterValidation | None = Field(default=None)


class QueryTemplate(BaseModel):
    """
    A query template retrieved from AI Search.

    The 'parameters' field is stored as stringified JSON in the index
    and hydrated to a list of ParameterDefinition objects.
    """

    id: str = Field(default="", description="Document ID from search index")
    intent: str = Field(description="Intent identifier for this template")
    question: str = Field(description="Example question this template answers")
    sql_template: str = Field(
        description="SQL template with %{{param}}% tokens"
    )
    reasoning: str = Field(
        default="",
        description="Explanation of what the query does"
    )
    parameters: list[ParameterDefinition] = Field(
        default_factory=list,
        description="Parameter definitions for token substitution"
    )
    allowed_tables: list[str] = Field(
        default_factory=list,
        description="Tables this query is allowed to access"
    )
    allowed_columns: list[str] = Field(
        default_factory=list,
        description="Columns this query is allowed to access"
    )
    score: float = Field(
        default=0.0,
        description="Search relevance score"
    )


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


class ExtractionResponseMessage(BaseModel):
    """
    A wrapper for parameter extraction responses.
    
    This type is used by the workflow to distinguish extraction responses
    from other string messages.
    """

    response_json: str = Field(description="The JSON-encoded extraction response")


class ParameterExtractionRequest(BaseModel):
    """Request to extract parameters from a user query using a template."""

    user_query: str = Field(description="The user's original question")
    template: QueryTemplate = Field(description="The matched query template")


class ParameterExtractionResponse(BaseModel):
    """Response from parameter extraction."""

    status: Literal["success", "needs_clarification", "error"] = Field(
        description="Extraction result status"
    )
    completed_sql: str | None = Field(
        default=None,
        description="The SQL query with all parameters substituted (if success)"
    )

    # For clarification flow
    missing_parameters: list[MissingParameter] | None = Field(
        default=None,
        description="Parameters that need user clarification"
    )
    clarification_prompt: str | None = Field(
        default=None,
        description="LLM-generated question asking for missing info"
    )

    # Preserve context for re-submission
    original_query: str | None = Field(
        default=None,
        description="Original user query (for context on retry)"
    )
    template_id: str | None = Field(
        default=None,
        description="Template ID to avoid re-searching"
    )

    # Extracted parameter values (for debugging/logging)
    extracted_parameters: dict[str, Any] | None = Field(
        default=None,
        description="Parameter name -> extracted value mapping"
    )

    error: str | None = Field(
        default=None,
        description="Error message if status is 'error'"
    )


# =============================================================================
# NL2SQL Response Model
# =============================================================================


class NL2SQLResponse(BaseModel):
    """
    Structured response from NL2SQL agent.

    Contains the SQL query, results, and metadata about the execution.
    Used by the workflow to pass data from data_agent to chat_agent.
    """

    sql_query: str = Field(
        default="",
        description="The SQL query that was executed"
    )

    sql_response: list[dict] = Field(
        default_factory=list,
        description="List of row dictionaries from the query result"
    )

    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score from cached query match (0-1)"
    )

    columns: list[str] = Field(
        default_factory=list,
        description="Column names from the result set"
    )

    row_count: int = Field(
        default=0,
        ge=0,
        description="Total number of rows returned"
    )

    used_cached_query: bool = Field(
        default=False,
        description="Whether a pre-cached query was used (deprecated, use query_source)"
    )

    query_source: str = Field(
        default="dynamic",
        description="Source of the query: 'template' (from query_templates), 'cached' (from cached queries), or 'dynamic' (generated)"
    )

    error: str | None = Field(
        default=None,
        description="Error message if the query failed"
    )


# =============================================================================
# Table Metadata Models (for dynamic query generation)
# =============================================================================


class TableColumn(BaseModel):
    """A column definition from the tables index."""

    name: str = Field(description="Column name")
    description: str = Field(default="", description="Column description")


class TableMetadata(BaseModel):
    """
    Table metadata retrieved from AI Search.

    Used by the query_builder to understand table structure
    when generating dynamic SQL queries.
    """

    id: str = Field(default="", description="Document ID from search index")
    table: str = Field(description="Fully qualified table name (e.g., 'Sales.Orders')")
    datasource: str = Field(default="", description="Database/datasource name")
    description: str = Field(default="", description="Table description")
    columns: list[TableColumn] = Field(
        default_factory=list,
        description="List of columns in the table"
    )
    score: float = Field(
        default=0.0,
        description="Search relevance score"
    )


# =============================================================================
# Query Builder Models (for dynamic SQL generation)
# =============================================================================


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


class QueryBuilderResponse(BaseModel):
    """Response from query builder."""

    status: Literal["success", "error"] = Field(
        description="Build result status"
    )
    completed_sql: str | None = Field(
        default=None,
        description="The generated SQL query (if success)"
    )
    user_query: str = Field(
        default="",
        description="The original user question (passed through for validation)"
    )
    retry_count: int = Field(
        default=0,
        description="Number of retry attempts (passed through from request)"
    )
    reasoning: str | None = Field(
        default=None,
        description="Explanation of how the query was constructed"
    )
    tables_used: list[str] = Field(
        default_factory=list,
        description="List of tables used in the query"
    )
    error: str | None = Field(
        default=None,
        description="Error message if status is 'error'"
    )


class QueryBuilderResponseMessage(BaseModel):
    """
    A wrapper for query builder responses.

    This type is used by the workflow to distinguish query builder responses
    from other message types.
    """

    response_json: str = Field(description="The JSON-encoded QueryBuilderResponse")


# =============================================================================
# Query Validator Models
# =============================================================================


class QueryValidationRequest(BaseModel):
    """Request to validate a SQL query before execution."""

    sql_query: str = Field(description="The SQL query to validate")
    user_query: str = Field(description="The original user question (for context)")
    tables_used: list[str] = Field(
        default_factory=list,
        description="Tables the query builder claims to use"
    )
    retry_count: int = Field(
        default=0,
        description="Number of times this query has been retried"
    )


class QueryValidationRequestMessage(BaseModel):
    """
    A wrapper for query validation requests.

    This type is used by the workflow to distinguish validation requests
    from other message types.
    """

    request_json: str = Field(description="The JSON-encoded QueryValidationRequest")


class QueryValidationResponse(BaseModel):
    """Response from query validation."""

    is_valid: bool = Field(description="Whether SQL passed validation")
    syntax_valid: bool = Field(description="Syntax check result")
    allowlist_valid: bool = Field(description="Catalog/schema/table allowlist check")
    statement_type: str = Field(description="Detected statement type (must be 'SELECT')")
    is_single_statement: bool = Field(description="Whether only one statement present")
    violations: list[str] = Field(
        default_factory=list,
        description="List of validation violations"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking warnings"
    )
    # Pass through fields for retry flow
    sql_query: str = Field(default="", description="The SQL query that was validated")
    user_query: str = Field(default="", description="The original user question")
    retry_count: int = Field(default=0, description="Current retry count")


class QueryValidationResponseMessage(BaseModel):
    """
    A wrapper for query validation responses.

    This type is used by the workflow to distinguish validation responses
    from other message types.
    """

    response_json: str = Field(description="The JSON-encoded QueryValidationResponse")
