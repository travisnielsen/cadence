"""
Query execution and results models.

These models represent the final response returned to users
after SQL execution.
"""

from pydantic import BaseModel, Field


class ClarificationInfo(BaseModel):
    """Information about a parameter that needs clarification."""
    
    parameter_name: str = Field(description="Name of the missing parameter")
    prompt: str = Field(description="User-friendly prompt asking for the value")
    allowed_values: list[str] = Field(
        default_factory=list,
        description="Valid options the user can choose from"
    )


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

    defaults_used: dict[str, str] = Field(
        default_factory=dict,
        description="Parameters that used default values (name -> human-readable description)"
    )
    
    # Clarification flow fields
    needs_clarification: bool = Field(
        default=False,
        description="Whether the agent needs more information from the user"
    )
    
    clarification: ClarificationInfo | None = Field(
        default=None,
        description="Details about what information is needed from the user"
    )
