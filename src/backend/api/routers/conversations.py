"""
Conversation management API routes.
"""

import logging
from typing import Any, cast

from api.dependencies import (
    OwnershipContext,
    extract_message_text,
    get_conversation_title,
    get_project_client,
    get_user_id,
    verify_conversation_ownership,
)
from api.models import (
    ConversationData,
    ConversationListResponse,
    MessageData,
    MessagesResponse,
    UpdateConversationRequest,
)
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    user_id: str = Depends(get_user_id),
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> ConversationListResponse:
    """
    List conversations visible to the current user.

    The provider API may not support strict server-side ownership filtering, so we
    apply a best-effort filter using conversation metadata when present.
    """
    try:
        openai_client = project_client.get_openai_client()
        conversations: list[ConversationData] = []

        items = openai_client.conversations.list()
        for item in items:
            conversation_id = getattr(item, "id", None)
            if not conversation_id:
                continue

            metadata_obj = getattr(item, "metadata", {}) or {}
            metadata = cast(dict[str, Any], metadata_obj) if isinstance(metadata_obj, dict) else {}

            owner_id_val = metadata.get("user_id")
            owner_id = str(owner_id_val) if isinstance(owner_id_val, str) else None
            if owner_id and owner_id != user_id:
                continue

            title_val = metadata.get("title")
            title = str(title_val) if isinstance(title_val, str) else None

            status_val = metadata.get("status")
            status = "archived" if status_val == "archived" else "regular"

            conversations.append(
                ConversationData(
                    conversation_id=conversation_id,
                    title=title,
                    status=status,
                    created_at=getattr(item, "created_at", None),
                )
            )
    except Exception as e:
        logger.exception("Error listing conversations for user %s", user_id)
        raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        return ConversationListResponse(conversations=conversations)


@router.get("/{conversation_id}", response_model=ConversationData)
async def get_conversation(
    conversation_id: str,
    ownership: OwnershipContext = Depends(verify_conversation_ownership),
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> ConversationData:
    """
    Get a specific conversation by ID.
    """
    conversation = ownership["conversation"]
    metadata = ownership["metadata"]

    title = await get_conversation_title(project_client, conversation_id, metadata)

    return ConversationData(
        conversation_id=conversation_id,
        title=title,
        status=metadata.get("status", "regular"),
        created_at=getattr(conversation, "created_at", None),
    )


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    ownership: OwnershipContext = Depends(verify_conversation_ownership),
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> dict[str, bool]:
    """
    Update conversation metadata (title, status).

    Note: V2 API may have limited metadata update support.
    """
    metadata = dict(ownership["metadata"])

    if body.title is not None:
        metadata["title"] = body.title
    if body.status is not None:
        metadata["status"] = body.status

    try:
        openai_client = project_client.get_openai_client()
        openai_client.conversations.update(conversation_id, metadata=metadata)
    except Exception as e:
        logger.exception("Error updating conversation %s", conversation_id)
        raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        return {"success": True}


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    _ownership: OwnershipContext = Depends(verify_conversation_ownership),
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> dict[str, bool]:
    """
    Delete a conversation.

    Note: _ownership triggers ownership verification before delete.
    """
    try:
        openai_client = project_client.get_openai_client()
        openai_client.conversations.delete(conversation_id)
    except Exception as e:
        logger.exception("Error deleting conversation %s", conversation_id)
        raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        return {"success": True}


@router.get("/{conversation_id}/messages", response_model=MessagesResponse)
async def get_conversation_messages(
    conversation_id: str,
    _ownership: OwnershipContext = Depends(verify_conversation_ownership),
    project_client: Any = Depends(get_project_client),  # noqa: ANN401
) -> MessagesResponse:
    """
    Get all messages for a conversation.
    Returns messages in chronological order (oldest first).

    Note: _ownership triggers ownership verification.
    """
    try:
        openai_client = project_client.get_openai_client()
        messages: list[MessageData] = []
        seen_content: set[tuple[str, str]] = set()

        # List items in the conversation
        items = openai_client.conversations.items.list(conversation_id)
        for item in items:
            content = extract_message_text(item)

            if not content.strip():
                continue

            role: str | Any = getattr(item, "role", "unknown")
            role = str(role.value) if hasattr(role, "value") else str(role)  # type: ignore[union-attr]

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
        logger.exception("Error getting messages for conversation %s", conversation_id)
        raise HTTPException(status_code=500, detail=str(e)) from e
