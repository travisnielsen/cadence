"""
Thread management API routes.

Note: V2 Foundry API uses 'conversations' instead of 'threads' internally,
but we maintain 'threads' terminology in the API for consistency.
"""

import logging
from typing import Any

from api.dependencies import (
    extract_message_text,
    get_project_client,
    get_thread_title,
    verify_thread_ownership,
)
from api.models import (
    MessageData,
    MessagesResponse,
    ThreadData,
    UpdateThreadRequest,
)
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.get("/{thread_id}", response_model=ThreadData)
async def get_thread(
    thread_id: str,
    ownership: dict = Depends(verify_thread_ownership),
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> ThreadData:
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
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> dict[str, bool]:
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
    except Exception as e:
        logger.exception("Error updating thread %s", thread_id)
        raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        return {"success": True}


@router.delete("/{thread_id}")
async def delete_thread(
    thread_id: str,
    _ownership: dict = Depends(verify_thread_ownership),
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> dict[str, bool]:
    """
    Delete a thread.

    Note: _ownership triggers ownership verification before delete.
    """
    try:
        openai_client = project_client.get_openai_client()
        openai_client.conversations.delete(thread_id)
    except Exception as e:
        logger.exception("Error deleting thread %s", thread_id)
        raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        return {"success": True}


@router.get("/{thread_id}/messages", response_model=MessagesResponse)
async def get_thread_messages(
    thread_id: str,
    _ownership: dict = Depends(verify_thread_ownership),
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> MessagesResponse:
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

            messages.append(
                MessageData(
                    id=getattr(item, "id", ""),
                    role=role,
                    content=content,
                    created_at=getattr(item, "created_at", None),
                )
            )

        return MessagesResponse(messages=messages)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error getting messages for thread %s", thread_id)
        raise HTTPException(status_code=500, detail=str(e)) from e
