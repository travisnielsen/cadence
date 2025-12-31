"""
Chat API routes with SSE streaming support.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, TYPE_CHECKING

from fastapi import APIRouter, Query, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from agent_framework.azure import AzureAIAgentClient
    from agent_framework import ChatAgent

try:
    from src.models import ChatRequest
    from src.dependencies import get_optional_user_id
except ImportError:
    from models import ChatRequest
    from dependencies import get_optional_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


async def generate_streaming_response(
    agent: "ChatAgent",
    chat_client: "AzureAIAgentClient",
    thread_id: str | None,
    message: str,
    user_id: str | None = None,
    title: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream response using Foundry-managed threads.
    
    - If thread_id is None: creates new Foundry thread, returns its ID
    - If thread_id provided: reconnects to existing Foundry thread
    """
    try:
        # Get or create thread
        thread = agent.get_new_thread(service_thread_id=thread_id)
        logger.info("Thread created: service_thread_id=%s", thread.service_thread_id)
        
        # Set metadata for new threads only
        thread_metadata = None
        if not thread_id:
            thread_metadata = {}
            if user_id:
                thread_metadata["user_id"] = user_id
            if title:
                thread_metadata["title"] = title
        
        logger.info(
            "Running with user_id=%s, incoming thread_id=%s, title=%s, metadata=%s",
            user_id, thread_id, title, thread_metadata
        )
        
        # Stream the response with metadata
        async for update in agent.run_stream(message, thread=thread, metadata=thread_metadata):
            if update.text:
                yield f"data: {json.dumps({'content': update.text, 'done': False})}\n\n"
                await asyncio.sleep(0.01)

        # After run completes, thread has the Foundry ID
        foundry_thread_id = thread.service_thread_id
        logger.info("Run complete: foundry_thread_id=%s", foundry_thread_id)
        
        # Verify metadata was set (debug logging)
        if thread_metadata and foundry_thread_id:
            try:
                fetched_thread = await chat_client.agents_client.threads.get(foundry_thread_id)
                logger.info("Thread metadata verification: %s", fetched_thread.metadata)
            except (ValueError, RuntimeError, OSError) as verify_err:
                logger.warning("Could not verify thread metadata: %s", verify_err)
        
        yield f"data: {json.dumps({'done': True, 'thread_id': foundry_thread_id})}\n\n"

    except (ValueError, RuntimeError, OSError) as e:
        logger.error("Error: %s", e)
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"


@router.get("/stream")
async def chat_stream(
    request: Request,
    message: str = Query(..., description="User message"),
    thread_id: str | None = Query(None, description="Foundry thread ID (omit for new thread)"),
    title: str | None = Query(None, description="Thread title (for new threads only)"),
    user_id: str | None = Depends(get_optional_user_id),
):
    """
    SSE streaming chat with Foundry-managed threads.
    
    - Omit thread_id for new conversation - Foundry will create one
    - Include thread_id to continue existing conversation
    - Response includes thread_id for use in subsequent requests
    """
    agent = getattr(request.app.state, "agent", None)
    chat_client = getattr(request.app.state, "chat_client", None)
    
    if agent is None or chat_client is None:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': 'Agent not initialized', 'done': True})}\n\n"]),
            media_type="text/event-stream",
        )
    
    return StreamingResponse(
        generate_streaming_response(agent, chat_client, thread_id, message, user_id, title),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("")
async def chat(
    chat_request: ChatRequest,
    request: Request,
    user_id: str | None = Depends(get_optional_user_id),
):
    """Non-streaming chat with Foundry-managed threads."""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    
    thread = agent.get_new_thread(service_thread_id=chat_request.thread_id)
    
    # Set user_id metadata for new threads only
    thread_metadata = {"user_id": user_id} if user_id and not chat_request.thread_id else None
    response = await agent.run(chat_request.message, thread=thread, metadata=thread_metadata)

    return {
        "response": response.text or str(response),
        "thread_id": thread.service_thread_id,
    }
