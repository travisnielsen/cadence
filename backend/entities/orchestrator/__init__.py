"""
Conversation Orchestrator - Manages user chat sessions and NL2SQL workflow invocation.

The ConversationOrchestrator:
1. Owns the Foundry thread and conversation history
2. Classifies user intent (data query, refinement, or conversation)
3. Invokes the NL2SQL workflow for data questions
4. Handles conversational refinements with context

Usage:
    from src.entities.orchestrator import ConversationOrchestrator
    
    orchestrator = ConversationOrchestrator(chat_client, thread_id)
    response = await orchestrator.process_message(user_message)
"""

from .orchestrator import ConversationOrchestrator, ConversationContext, ClassificationResult


# Export for programmatic access
__all__ = ["ConversationOrchestrator", "ConversationContext", "ClassificationResult"]
