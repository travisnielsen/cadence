"""
Thread management API routes.

Note: V2 Foundry API uses 'conversations' instead of 'threads' internally,
but we maintain 'threads' terminology in the API for consistency.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from src.api.models import (
    ThreadData,
    UpdateThreadRequest,
    MessageData,
    MessagesResponse,
)
from src.api.dependencies import (
    get_project_client,
    verify_thread_ownership,
    get_thread_title,
    extract_message_text,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.get("/{thread_id}", response_model=ThreadData)
async def get_thread(
    thread_id: str,
    ownership: dict = Depends(verify_thread_ownership),
    project_client = Depends(get_project_client),
):
    """
    Get a specific thread by ID.
    """
    thread = ownership["thread"]
    metadata = ownership["metadata"]

    title = await get_thread_title(project_client, thread_id, metadata)

    return ThreadData(
        thread_id=thread_id,
        title=title,
        status=metadata.get("status", "regular"),
        created_at=getattr(thread, "created_at", None),
    )


@router.patch("/{thread_id}")
async def update_thread(
    thread_id: str,
    body: UpdateThreadRequest,
    ownership: dict = Depends(verify_thread_ownership),
    project_client = Depends(get_project_client),
):
    """
    Update thread metadata (title, status).
    
    Note: V2 API may have limited metadata update support.
    """
    metadata = dict(ownership["metadata"])

    if body.title is not None:
        metadata["title"] = body.title
    if body.status is not None:
        metadata["status"] = body.status

    try:
        openai_client = project_client.get_openai_client()
        # Attempt to update the thread (conversation in V2 API)
        openai_client.conversations.update(thread_id, metadata=metadata)
        return {"success": True}
    except Exception as e:
        logger.error("Error updating thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{thread_id}")
async def delete_thread(
    thread_id: str,
    _ownership: dict = Depends(verify_thread_ownership),
    project_client = Depends(get_project_client),
):
    """
    Delete a thread.

    Note: _ownership triggers ownership verification before delete.
    """
    try:
        openai_client = project_client.get_openai_client()
        openai_client.conversations.delete(thread_id)
        return {"success": True}
    except Exception as e:
        logger.error("Error deleting thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{thread_id}/messages", response_model=MessagesResponse)
async def get_thread_messages(
    thread_id: str,
    _ownership: dict = Depends(verify_thread_ownership),
    project_client = Depends(get_project_client),
):
    """
    Get all messages for a thread.
    Returns messages in chronological order (oldest first).

    Note: _ownership triggers ownership verification.
    """
    try:
        openai_client = project_client.get_openai_client()
        messages: list[MessageData] = []
        seen_content: set[tuple[str, str]] = set()

        # List items in the thread (conversation in V2 API)
        items = openai_client.conversations.items.list(thread_id)
        for item in items:
            content = extract_message_text(item)

            if not content.strip():
                continue

            role = getattr(item, "role", "unknown")
            if hasattr(role, "value"):
                role = role.value  # type: ignore[union-attr]

            # Deduplicate by role + content
            content_key = (role, content)
            if content_key in seen_content:
                continue
            seen_content.add(content_key)

            messages.append(MessageData(
                id=getattr(item, "id", ""),
                role=role,
                content=content,
                created_at=getattr(item, "created_at", None),
            ))

        return MessagesResponse(messages=messages)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting messages for thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
