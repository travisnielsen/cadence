"""DataAssistant — manages user chat sessions and NL2SQL workflow invocation.

Usage:
    from entities.assistant import DataAssistant

    assistant = DataAssistant(agent, thread_id)
"""

from .assistant import (
    SCHEMA_SUGGESTIONS,
    ClassificationResult,
    ConversationContext,
    DataAssistant,
    _detect_schema_area,
    load_assistant_prompt,
)

__all__ = [
    "SCHEMA_SUGGESTIONS",
    "ClassificationResult",
    "ConversationContext",
    "DataAssistant",
    "_detect_schema_area",
    "load_assistant_prompt",
]
