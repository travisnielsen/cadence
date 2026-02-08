"""
Shared models for entities.

These models are used across multiple agents and the workflow.
All models are re-exported here for backward compatibility.
"""

from .execution import ClarificationInfo, NL2SQLResponse
from .extraction import (
    ClarificationMessage,
    ClarificationRequest,
    ExtractionRequestMessage,
    MissingParameter,
    NL2SQLRequest,
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
    # Execution (query results)
    "ClarificationInfo",
    "ClarificationMessage",
    "ClarificationRequest",
    "ExtractionRequestMessage",
    # Extraction (parameter extraction workflow)
    "MissingParameter",
    "NL2SQLRequest",
    "NL2SQLResponse",
    "ParameterDefinition",
    "ParameterExtractionRequest",
    "ParameterNormalization",
    # Schema (AI Search index models)
    "ParameterValidation",
    "QueryBuilderRequest",
    "QueryBuilderRequestMessage",
    "QueryTemplate",
    # Generation (SQL construction and validation)
    "SQLDraft",
    "SQLDraftMessage",
    "TableColumn",
    "TableMetadata",
]
