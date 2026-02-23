"""
Pydantic models for API request/response schemas.
"""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Request body for non-streaming chat endpoint."""

    message: str
    conversation_id: str | None = None


class ConversationData(BaseModel):
    """Conversation information returned by conversation endpoints."""

    conversation_id: str
    title: str | None = None
    status: str = "regular"  # "regular" or "archived"
    created_at: str | None = None


class ConversationListResponse(BaseModel):
    """Response for listing conversations."""

    conversations: list[ConversationData]


class UpdateConversationRequest(BaseModel):
    """Request body for updating conversation metadata."""

    title: str | None = None
    status: str | None = None


class MessageData(BaseModel):
    """Individual message in a conversation."""

    id: str
    role: str
    content: str
    created_at: str | None = None


class MessagesResponse(BaseModel):
    """Response for getting conversation messages."""

    messages: list[MessageData]
