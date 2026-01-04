"""
FastAPI dependencies for authentication and shared resources.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import Request, HTTPException, Depends

if TYPE_CHECKING:
    from agent_framework.azure import AzureAIAgentClient
    from agent_framework import ChatAgent

logger = logging.getLogger(__name__)


def get_user_id(request: Request) -> str:
    """
    Get authenticated user ID from request state.

    Raises HTTPException 401 if not authenticated.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


def get_optional_user_id(request: Request) -> str | None:
    """Get user ID from request state, or None if not authenticated."""
    return getattr(request.state, "user_id", None)


def get_chat_client(request: Request) -> "AzureAIAgentClient":
    """
    Get the chat client from app state.

    Raises HTTPException 503 if not initialized.
    """
    chat_client = getattr(request.app.state, "chat_client", None)
    if chat_client is None:
        raise HTTPException(status_code=503, detail="Chat client not initialized")
    return chat_client


def get_agent(request: Request) -> "ChatAgent":
    """
    Get the agent from app state.

    Raises HTTPException 503 if not initialized.
    """
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return agent


async def verify_thread_ownership(
    thread_id: str,
    user_id: str = Depends(get_user_id),
    chat_client: "AzureAIAgentClient" = Depends(get_chat_client),
) -> dict:
    """
    Verify that the current user owns the specified thread.

    Returns the thread metadata if ownership is verified.
    Raises HTTPException 403 if access is denied.
    """
    try:
        thread = await chat_client.agents_client.threads.get(thread_id)
        metadata = getattr(thread, "metadata", {}) or {}
        thread_owner = metadata.get("user_id")

        # Log ownership check details for debugging
        logger.info(
            "Thread ownership check: thread_id=%s, thread_owner=%s, current_user=%s",
            thread_id, thread_owner, user_id
        )

        # Check ownership - threads without user_id (legacy) are accessible to no one
        # To migrate legacy threads, you'd need to update their metadata with the owner's user_id
        if thread_owner is None:
            logger.warning(
                "Thread %s has no user_id in metadata (legacy thread). Access denied.",
                thread_id
            )
            raise HTTPException(
                status_code=403,
                detail="This thread was created before user tracking was enabled. It cannot be accessed."
            )

        if thread_owner != user_id:
            logger.warning(
                "Thread ownership mismatch: thread_id=%s, owner=%s, requester=%s",
                thread_id, thread_owner, user_id
            )
            raise HTTPException(status_code=403, detail="Access denied")

        return {"thread": thread, "metadata": metadata, "user_id": user_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error verifying thread ownership for %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


def extract_message_text(msg) -> str:
    """Extract text content from a message object."""
    content = ""
    if msg.content:
        for part in msg.content:
            if hasattr(part, "text") and part.text:
                text_value = getattr(part.text, "value", "") or str(part.text)
                content += text_value
    return content


async def get_thread_title(chat_client: "AzureAIAgentClient", thread_id: str, metadata: dict) -> str:
    """
    Get thread title from metadata or first user message.

    Returns "New Chat" if no title can be determined.
    """
    title = metadata.get("title")
    if title:
        return title

    try:
        async for msg in chat_client.agents_client.messages.list(thread_id=thread_id):
            if msg.role.value == "user" and msg.content:
                for part in msg.content:
                    if hasattr(part, "text") and part.text:
                        text_value = getattr(part.text, "value", "") or str(part.text)
                        return text_value[:50] + "..." if len(text_value) > 50 else text_value
    except (ValueError, RuntimeError, OSError) as e:
        logger.warning("Could not fetch messages for thread %s: %s", thread_id, e)

    return "New Chat"
