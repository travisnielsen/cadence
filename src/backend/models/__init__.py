"""
Shared models for backend components.

These models are used across multiple agents and the workflow.
All models are re-exported here for backward compatibility.
"""

from .execution import ClarificationInfo, NL2SQLResponse, SchemaSuggestion
from .extraction import (
    ClarificationRequest,
    MissingParameter,
    NL2SQLRequest,
    ParameterConfidence,
    ParameterExtractionRequest,
)
from .generation import (
    QueryBuilderRequest,
    SQLDraft,
)
from .scenario import (
    ChartSeriesDefinition,
    PromptHint,
    ScenarioAssumption,
    ScenarioAssumptionSet,
    ScenarioComputationResult,
    ScenarioIntent,
    ScenarioMetricValue,
    ScenarioNarrativeSummary,
    ScenarioVisualizationPayload,
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
    "ChartSeriesDefinition",
    "ClarificationInfo",
    "ClarificationRequest",
    "MissingParameter",
    "NL2SQLRequest",
    "NL2SQLResponse",
    "ParameterConfidence",
    "ParameterDefinition",
    "ParameterExtractionRequest",
    "ParameterNormalization",
    "ParameterValidation",
    "PromptHint",
    "QueryBuilderRequest",
    "QueryTemplate",
    "SQLDraft",
    "ScenarioAssumption",
    "ScenarioAssumptionSet",
    "ScenarioComputationResult",
    "ScenarioIntent",
    "ScenarioMetricValue",
    "ScenarioNarrativeSummary",
    "ScenarioVisualizationPayload",
    "SchemaSuggestion",
    "TableColumn",
    "TableMetadata",
]
