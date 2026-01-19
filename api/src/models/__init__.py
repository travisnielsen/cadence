"""
Shared models for entities.

These models are used across multiple agents and the workflow.
All models are re-exported here for backward compatibility.
"""

from .execution import NL2SQLResponse
from .extraction import (
    ClarificationMessage,
    ExtractionRequestMessage,
    MissingParameter,
    ParameterExtractionRequest,
)
from .generation import (
    QueryBuilderRequest,
    QueryBuilderRequestMessage,
    SQLDraft,
    SQLDraftMessage,
)
from .schema import (
    ParameterDefinition,
    ParameterNormalization,
    ParameterValidation,
    QueryTemplate,
    TableColumn,
    TableMetadata,
)

__all__ = [
    # Schema (AI Search index models)
    "ParameterValidation",
    "ParameterNormalization",
    "ParameterDefinition",
    "QueryTemplate",
    "TableColumn",
    "TableMetadata",
    # Extraction (parameter extraction workflow)
    "MissingParameter",
    "ClarificationMessage",
    "ExtractionRequestMessage",
    "ParameterExtractionRequest",
    # Generation (SQL construction and validation)
    "SQLDraft",
    "SQLDraftMessage",
    "QueryBuilderRequest",
    "QueryBuilderRequestMessage",
    # Execution (query results)
    "NL2SQLResponse",
]
