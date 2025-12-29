"""
FastAPI server with Microsoft Agent Framework and SSE streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from azure.identity.aio import DefaultAzureCredential
from agent_framework.azure import AzureAIAgentClient  # type: ignore[attr-defined] # pylint: disable=no-name-in-module
from agent_framework import ChatAgent
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Import auth from same package - works both as module and direct run
try:
    from src.auth import azure_scheme, azure_ad_settings, AzureADAuthMiddleware
except ImportError:
    from auth import azure_scheme, azure_ad_settings, AzureADAuthMiddleware

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Reduce noise from Azure SDK and other libraries
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Check if Azure AD authentication is configured
AUTH_ENABLED = bool(azure_ad_settings.AZURE_AD_CLIENT_ID and azure_ad_settings.AZURE_AD_TENANT_ID)

def _build_chat_client() -> AzureAIAgentClient:
    """Build the Azure AI (Foundry) agent client."""
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    deployment = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini")
    agent_id = os.getenv("AZURE_AI_AGENT_ID")  # Optional: reuse existing agent

    if not endpoint:
        raise ValueError("AZURE_AI_PROJECT_ENDPOINT environment variable is required")

    logger.info("Using endpoint: %s, deployment: %s, agent_id: %s", endpoint, deployment, agent_id or "new")

    return AzureAIAgentClient(
        credential=DefaultAzureCredential(),
        project_endpoint=endpoint,
        model_deployment_name=deployment,
        agent_id=agent_id,  # If set, reuses existing agent; if None, creates new one
        should_cleanup_agent=agent_id is None,  # Only cleanup if we created it
    )


def _create_agent(chat_client: AzureAIAgentClient) -> ChatAgent:
    """Create the chat agent."""
    return ChatAgent(
        name="assistant",
        instructions="You are a helpful AI assistant. Be concise and friendly.",
        chat_client=chat_client,
    )


chat_client: AzureAIAgentClient | None = None
agent: ChatAgent | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_client, agent
    chat_client = _build_chat_client()
    agent = _create_agent(chat_client)
    logger.info("Agent initialized")
    yield
    if chat_client:
        await chat_client.close()
    if AUTH_ENABLED:
        logger.info("Azure AD authentication is ENABLED")
        if azure_scheme:
            await azure_scheme.openid_config.load_config()
    else:
        logger.warning("=" * 60)
        logger.warning("WARNING: Azure AD authentication is NOT configured!")
        logger.warning("The API will respond to ANONYMOUS connections.")
        logger.warning("Set AZURE_AD_CLIENT_ID and AZURE_AD_TENANT_ID to enable auth.")
        logger.warning("=" * 60)
    yield


app = FastAPI(
    title="Enterprise Data Agent",
    lifespan=lifespan,
    swagger_ui_oauth2_redirect_url="/oauth2-redirect",
    swagger_ui_init_oauth={
        "usePkceWithAuthorizationCodeGrant": True,
        "clientId": azure_ad_settings.AZURE_AD_CLIENT_ID,
    } if AUTH_ENABLED else None,
)

# Add Azure AD authentication middleware
if AUTH_ENABLED:
    app.add_middleware(AzureADAuthMiddleware, settings=azure_ad_settings)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "agent_ready": agent is not None}


async def generate_streaming_response(
    thread_id: str | None, 
    message: str,
    user_id: str | None = None,
    title: str | None = None
) -> AsyncGenerator[str, None]:
    """
    Stream response using Foundry-managed threads.
    
    - If thread_id is None: creates new Foundry thread, returns its ID
    - If thread_id provided: reconnects to existing Foundry thread
    """
    if agent is None:
        yield f"data: {json.dumps({'error': 'Agent not initialized'})}\n\n"
        return

    try:
        # Get or create thread
        # If thread_id provided, reconnects to existing Foundry thread
        # If None, creates a new one (ID assigned after first message)
        thread = agent.get_new_thread(service_thread_id=thread_id)
        logger.info("Thread created: service_thread_id=%s", thread.service_thread_id)
        
        # Set metadata for new threads only (when no thread_id was provided)
        thread_metadata = None
        if not thread_id:
            thread_metadata = {}
            if user_id:
                thread_metadata["user_id"] = user_id
            if title:
                thread_metadata["title"] = title
        logger.info("Running with user_id=%s, incoming thread_id=%s, title=%s, metadata=%s", user_id, thread_id, title, thread_metadata)
        
        # Stream the response with metadata
        async for update in agent.run_stream(message, thread=thread, metadata=thread_metadata):
            if update.text:
                yield f"data: {json.dumps({'content': update.text, 'done': False})}\n\n"
                await asyncio.sleep(0.01)

        # After run completes, thread has the Foundry ID
        # Send it so client can use it for subsequent requests
        foundry_thread_id = thread.service_thread_id
        logger.info("Run complete: foundry_thread_id=%s", foundry_thread_id)
        
        # Verify metadata was set (debug logging)
        if thread_metadata and chat_client and foundry_thread_id:
            try:
                fetched_thread = await chat_client.agents_client.threads.get(foundry_thread_id)
                logger.info("Thread metadata verification: %s", fetched_thread.metadata)
            except Exception as verify_err:
                logger.warning("Could not verify thread metadata: %s", verify_err)
        
        yield f"data: {json.dumps({'done': True, 'thread_id': foundry_thread_id})}\n\n"

    except Exception as e:
        logger.error("Error: %s", e)
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"


@app.get("/api/chat/stream")
async def chat_stream(
    request: Request,
    message: str = Query(..., description="User message"),
    thread_id: str | None = Query(None, description="Foundry thread ID (omit for new thread)"),
    title: str | None = Query(None, description="Thread title (for new threads only)"),
):
    """
    SSE streaming chat with Foundry-managed threads.
    
    - Omit thread_id for new conversation - Foundry will create one
    - Include thread_id to continue existing conversation
    - Response includes thread_id for use in subsequent requests
    """
    # Get user_id from auth middleware (set from oid claim)
    user_id = getattr(request.state, "user_id", None)
    
    return StreamingResponse(
        generate_streaming_response(thread_id, message, user_id, title),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ThreadData(BaseModel):
    thread_id: str
    title: str | None = None
    status: str = "regular"  # "regular" or "archived"
    created_at: str | None = None


class ThreadListResponse(BaseModel):
    threads: list[ThreadData]


@app.get("/api/threads", response_model=ThreadListResponse)
async def list_threads(request: Request):
    """
    List threads for the current user (filtered by user_id metadata).
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if chat_client is None:
        raise HTTPException(status_code=503, detail="Chat client not initialized")
    
    try:
        # List threads (limited to 1000, newest first) and filter by user_id metadata
        user_threads: list[ThreadData] = []
        
        async for thread in chat_client.agents_client.threads.list(limit=100, order="desc"):
            metadata = getattr(thread, "metadata", {}) or {}
            if metadata.get("user_id") == user_id:
                # Get title from metadata, or fetch first user message
                title = metadata.get("title")
                if not title:
                    # Try to get first user message as title
                    try:
                        async for msg in chat_client.agents_client.messages.list(thread_id=thread.id):
                            if msg.role.value == "user" and msg.content:
                                for part in msg.content:
                                    if hasattr(part, "text") and part.text:
                                        # part.text is a MessageTextDetails object with .value attribute
                                        text_value = getattr(part.text, "value", "") or str(part.text)
                                        # Truncate to reasonable title length
                                        title = text_value[:50] + "..." if len(text_value) > 50 else text_value
                                        break
                                if title:
                                    break
                    except Exception as msg_err:
                        logger.warning("Could not fetch messages for thread %s: %s", thread.id, msg_err)
                
                title = title or "New Chat"
                status = metadata.get("status", "regular")
                # created_at is a datetime object
                created_at = thread.created_at.isoformat() if thread.created_at else None
                
                user_threads.append(ThreadData(
                    thread_id=thread.id,
                    title=title,
                    status=status,
                    created_at=created_at,
                ))
        
        return ThreadListResponse(threads=user_threads)
    
    except Exception as e:
        logger.error("Error listing threads: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/threads/{thread_id}")
async def get_thread(thread_id: str, request: Request):
    """
    Get a specific thread by ID.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if chat_client is None:
        raise HTTPException(status_code=503, detail="Chat client not initialized")
    
    try:
        thread = await chat_client.agents_client.threads.get(thread_id)
        metadata = getattr(thread, "metadata", {}) or {}
        
        # Verify ownership
        if metadata.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get title from metadata, or fetch first user message
        title = metadata.get("title")
        if not title:
            try:
                async for msg in chat_client.agents_client.messages.list(thread_id=thread.id):
                    if msg.role.value == "user" and msg.content:
                        for part in msg.content:
                            if hasattr(part, "text") and part.text:
                                # part.text is a MessageTextDetails object with .value attribute
                                text_value = getattr(part.text, "value", "") or str(part.text)
                                title = text_value[:50] + "..." if len(text_value) > 50 else text_value
                                break
                        if title:
                            break
            except Exception as msg_err:
                logger.warning("Could not fetch messages for thread %s: %s", thread.id, msg_err)
        
        return ThreadData(
            thread_id=thread.id,
            title=title or "New Chat",
            status=metadata.get("status", "regular"),
            created_at=thread.created_at.isoformat() if thread.created_at else None,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e))


class UpdateThreadRequest(BaseModel):
    title: str | None = None
    status: str | None = None


@app.patch("/api/threads/{thread_id}")
async def update_thread(thread_id: str, body: UpdateThreadRequest, request: Request):
    """
    Update thread metadata (title, status).
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if chat_client is None:
        raise HTTPException(status_code=503, detail="Chat client not initialized")
    
    try:
        # Get current thread to verify ownership and get current metadata
        thread = await chat_client.agents_client.threads.get(thread_id)
        metadata = dict(getattr(thread, "metadata", {}) or {})
        
        # Verify ownership
        if metadata.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Update metadata
        if body.title is not None:
            metadata["title"] = body.title
        if body.status is not None:
            metadata["status"] = body.status
        
        # Update the thread
        await chat_client.agents_client.threads.update(thread_id, metadata=metadata)
        
        return {"success": True}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/threads/{thread_id}")
async def delete_thread(thread_id: str, request: Request):
    """
    Delete a thread.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if chat_client is None:
        raise HTTPException(status_code=503, detail="Chat client not initialized")
    
    try:
        # Get current thread to verify ownership
        thread = await chat_client.agents_client.threads.get(thread_id)
        metadata = getattr(thread, "metadata", {}) or {}
        
        # Verify ownership
        if metadata.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Delete the thread
        await chat_client.agents_client.threads.delete(thread_id)
        
        return {"success": True}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e))


class MessageData(BaseModel):
    id: str
    role: str
    content: str
    created_at: str | None = None


class MessagesResponse(BaseModel):
    messages: list[MessageData]


@app.get("/api/threads/{thread_id}/messages")
async def get_thread_messages(thread_id: str, request: Request):
    """
    Get all messages for a thread.
    Returns messages in chronological order (oldest first).
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if chat_client is None:
        raise HTTPException(status_code=503, detail="Chat client not initialized")
    
    try:
        # Get thread to verify ownership
        thread = await chat_client.agents_client.threads.get(thread_id)
        metadata = getattr(thread, "metadata", {}) or {}
        
        # Verify ownership
        if metadata.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Fetch all messages
        messages: list[MessageData] = []
        seen_content: set[tuple[str, str]] = set()  # (role, content) to deduplicate
        
        async for msg in chat_client.agents_client.messages.list(thread_id=thread_id):
            # Extract text content
            content = ""
            if msg.content:
                for part in msg.content:
                    if hasattr(part, "text") and part.text:
                        text_value = getattr(part.text, "value", "") or str(part.text)
                        content += text_value
            
            # Skip empty messages
            if not content.strip():
                continue
            
            # Deduplicate by role + content (Azure AI Agents sometimes creates duplicate user messages)
            content_key = (msg.role.value, content)
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            
            messages.append(MessageData(
                id=msg.id,
                role=msg.role.value,  # "user" or "assistant"
                content=content,
                created_at=msg.created_at.isoformat() if msg.created_at else None,
            ))
        
        # Reverse to get chronological order (API returns newest first)
        messages.reverse()
        
        return MessagesResponse(messages=messages)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting messages for thread %s: %s", thread_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat")
async def chat(chat_request: ChatRequest, request: Request):
    """Non-streaming chat with Foundry-managed threads."""
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    # Get user_id from auth middleware (set from oid claim)
    user_id = getattr(request.state, "user_id", None)
    
    thread = agent.get_new_thread(service_thread_id=chat_request.thread_id)
    
    # Set user_id metadata for new threads only
    thread_metadata = {"user_id": user_id} if user_id and not chat_request.thread_id else None
    response = await agent.run(chat_request.message, thread=thread, metadata=thread_metadata)

    return {
        "response": response.text or str(response),
        "thread_id": thread.service_thread_id,
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
