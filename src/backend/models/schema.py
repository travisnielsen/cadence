"""
Schema models deserialized from AI Search indexes.

These models represent query templates and table metadata
stored in Azure AI Search.
"""

from typing import Any

from pydantic import BaseModel, Field


class ParameterValidation(BaseModel):
    """Validation rules for a query template parameter."""

    type: str = Field(description="Data type: 'integer', 'string', 'date', etc.")
    min: int | float | str | None = Field(
        default=None, description="Minimum value (for integers/dates)"
    )
    max: int | float | str | None = Field(
        default=None, description="Maximum value (for integers/dates)"
    )
    allowed_values: list[str] | None = Field(
        default=None, description="List of allowed values (for string enums)"
    )
    regex: str | None = Field(default=None, description="Regex pattern for string validation")


class ParameterNormalization(BaseModel):
    """Normalization rules for a parameter value."""

    lowercase: bool = Field(default=False)
    strip: bool = Field(default=True)


class ParameterDefinition(BaseModel):
    """Definition of a parameter in a query template."""

    name: str = Field(description="Parameter name matching %{{name}}% token")
    column: str | None = Field(default=None, description="Database column this parameter maps to")
    required: bool = Field(default=True)
    ask_if_missing: bool = Field(
        default=False, description="If true and no default, ask user for clarification"
    )
    default_value: Any = Field(default=None, description="Default value if not provided/inferred")
    default_policy: str | None = Field(
        default=None, description="Policy for computing default (e.g., 'current_date')"
    )
    confidence_weight: float = Field(default=0.0, description="Weight for confidence scoring")
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
    sql_template: str = Field(description="SQL template with %{{param}}% tokens")
    reasoning: str = Field(default="", description="Explanation of what the query does")
    parameters: list[ParameterDefinition] = Field(
        default_factory=list, description="Parameter definitions for token substitution"
    )
    allowed_tables: list[str] = Field(
        default_factory=list, description="Tables this query is allowed to access"
    )
    allowed_columns: list[str] = Field(
        default_factory=list, description="Columns this query is allowed to access"
    )
    score: float = Field(default=0.0, description="Search relevance score")


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
        default_factory=list, description="List of columns in the table"
    )
    score: float = Field(default=0.0, description="Search relevance score")
