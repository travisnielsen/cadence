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
    ParameterConfidence,
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
    "ClarificationInfo",
    "ClarificationMessage",
    "ClarificationRequest",
    "ExtractionRequestMessage",
    "MissingParameter",
    "NL2SQLRequest",
    "NL2SQLResponse",
    "ParameterConfidence",
    "ParameterDefinition",
    "ParameterExtractionRequest",
    "ParameterNormalization",
    "ParameterValidation",
    "QueryBuilderRequest",
    "QueryBuilderRequestMessage",
    "QueryTemplate",
    "SQLDraft",
    "SQLDraftMessage",
    "TableColumn",
    "TableMetadata",
]
