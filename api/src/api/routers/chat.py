"""
Chat API routes with SSE streaming support.

This module provides chat endpoints that work with the workflow-based agent architecture.
The workflow processes user messages through:
1. ChatAgentExecutor - receives user input
2. NL2SQLAgentExecutor - processes data queries
3. ChatAgentExecutor - renders structured response
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, TYPE_CHECKING

from fastapi import APIRouter, Query, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
from opentelemetry import trace, context as otel_context

from agent_framework import (
    ChatMessage,
    Role,
    WorkflowOutputEvent,
    WorkflowStatusEvent,
    WorkflowRunState,
    ExecutorInvokedEvent,
    ExecutorCompletedEvent,
)

if TYPE_CHECKING:
    from agent_framework import ChatAgent, Workflow

from src.api.models import ChatRequest
from src.api.dependencies import get_optional_user_id
from src.api.step_events import set_step_queue, clear_step_queue, set_request_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Get tracer for workflow-level spans
tracer = trace.get_tracer(__name__)


async def generate_workflow_streaming_response(
    workflow: "Workflow",
    message: str,
    incoming_thread_id: str | None = None,
    user_id: str | None = None,
    title: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream response from the workflow using workflow events.

    The workflow runs:
    1. ChatAgentExecutor receives the user message
    2. NL2SQLAgentExecutor processes the query
    3. ChatAgentExecutor renders the final response

    The output is a JSON structure with 'text', 'thread_id', and optional 'tool_call' from Foundry.
    
    Tracing:
    - Creates a parent 'workflow.run' span that wraps the entire execution
    - All executor spans become children of this parent span for trace correlation
    """
    # Friendly names for executor steps (parent steps that contain tool steps)
    # Note: "chat" appears twice in the workflow (routing and rendering), 
    # we only want to show the rendering phase which happens after nl2sql
    executor_step_names = {
        "nl2sql": "Analyzing data and generating query...",
        "chat": "Preparing response...",
    }
    
    # Create a parent span for the entire workflow execution
    # This ensures all executor spans are correlated under a single trace in Foundry
    workflow_span = tracer.start_span(
        "workflow.run",
        attributes={
            "workflow.message": message[:100],  # Truncate for attribute limits
            "workflow.thread_id": incoming_thread_id or "new",
            "workflow.user_id": user_id or "anonymous",
        }
    )
    # Attach the span to current context so child spans are linked
    context_token = otel_context.attach(trace.set_span_in_context(workflow_span))

    # Set up step event queue for tool-level progress
    step_queue: asyncio.Queue = asyncio.Queue()
    set_step_queue(step_queue)
    logger.info("Step queue created and set in context")
    
    # Set user_id in context so executors can access it when creating threads
    set_request_user_id(user_id)
    logger.info("User ID set in request context: %s", user_id)
    
    # Use an async queue for real-time step event streaming
    # This allows us to emit step events as they happen, not just when workflow events occur
    step_output_queue: asyncio.Queue = asyncio.Queue()
    
    async def step_monitor():
        """Background task that monitors step_queue and forwards to output queue."""
        while True:
            try:
                # Wait for a step event with a short timeout
                event = await asyncio.wait_for(step_queue.get(), timeout=0.1)
                await step_output_queue.put(event)
                logger.info("Step monitor forwarded event: %s", event)
            except asyncio.TimeoutError:
                # Check if we should stop (sentinel value)
                continue
            except asyncio.CancelledError:
                # Drain any remaining events before exiting
                while not step_queue.empty():
                    try:
                        event = step_queue.get_nowait()
                        await step_output_queue.put(event)
                    except asyncio.QueueEmpty:
                        break
                break

    async def drain_step_queue():
        """Yield any pending step events from the output queue."""
        events = []
        while not step_output_queue.empty():
            try:
                event = step_output_queue.get_nowait()
                events.append(event)
                logger.info("Drained step event: %s", event)
            except asyncio.QueueEmpty:
                break
        return events

    def format_step_event(step_event: dict) -> dict:
        """Format a step event for SSE transmission."""
        result = {
            "step": step_event.get("step"),
            "done": False,
        }
        # Include status and duration if present
        if "status" in step_event:
            result["status"] = step_event["status"]
        if "duration_ms" in step_event:
            result["duration_ms"] = step_event["duration_ms"]
        return result

    # Initialize tasks for cleanup scope
    step_monitor_task: asyncio.Task | None = None
    next_event_task: asyncio.Task | None = None

    try:
        logger.info("Starting workflow stream for message: %s", message[:100])
        logger.info("Workflow stream with user_id=%s, thread_id=%s, title=%s", user_id, incoming_thread_id, title)

        # Create a ChatMessage to send to the workflow
        user_message = ChatMessage(role=Role.USER, text=message)

        # Track if we've received output
        output_received = False
        foundry_thread_id = incoming_thread_id  # Fall back to incoming if not returned
        
        # Track executor start times for duration calculation
        executor_start_times: dict[str, float] = {}
        
        # Track which executors have been seen (to distinguish routing vs rendering chat phases)
        seen_nl2sql = False
        
        # Start the step monitor background task
        step_monitor_task = asyncio.create_task(step_monitor())
        
        # Get the async iterator for the workflow
        # Pass incoming_thread_id as a kwarg - this survives shared_state.clear() in run_stream
        workflow_iter = workflow.run_stream(user_message, thread_id=incoming_thread_id).__aiter__()
        workflow_done = False
        
        # Create a task for the next workflow event
        next_event_task = asyncio.ensure_future(workflow_iter.__anext__())
        
        while not workflow_done:
            # Wait for either: next workflow event OR step event from queue
            step_wait_task = asyncio.create_task(step_output_queue.get())
            
            done, _ = await asyncio.wait(
                [next_event_task, step_wait_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Handle step events first (if any completed)
            if step_wait_task in done:
                step_event = step_wait_task.result()
                yield f"data: {json.dumps(format_step_event(step_event))}\n\n"
                logger.info("Emitted real-time tool step: %s", step_event)
            else:
                # Cancel the pending step wait task
                step_wait_task.cancel()
                try:
                    await step_wait_task
                except asyncio.CancelledError:
                    pass
            
            # Handle workflow event if it completed
            if next_event_task in done:
                try:
                    event = next_event_task.result()
                except StopAsyncIteration:
                    workflow_done = True
                    continue
                
                logger.info("Received workflow event: %s", type(event).__name__)
                
                # Drain any remaining step events before processing workflow event
                for step_event_data in await drain_step_queue():
                    yield f"data: {json.dumps(format_step_event(step_event_data))}\n\n"
                    logger.info("Emitted accumulated tool step: %s", step_event_data)

                if isinstance(event, ExecutorInvokedEvent):
                    # Track when nl2sql is seen (to distinguish chat routing vs rendering)
                    if event.executor_id == "nl2sql":
                        seen_nl2sql = True
                    
                    # Emit parent step event when executor starts
                    # Skip first "chat" invocation (routing phase) - only show after nl2sql
                    step_name = executor_step_names.get(event.executor_id)
                    should_emit = step_name and (event.executor_id != "chat" or seen_nl2sql)
                    if should_emit:
                        executor_start_times[event.executor_id] = time.time()
                        yield f"data: {json.dumps({'step': step_name, 'status': 'started', 'is_parent': True, 'done': False})}\n\n"
                    logger.info("Executor invoked: %s (emit=%s)", event.executor_id, should_emit)

                elif isinstance(event, ExecutorCompletedEvent):
                    logger.info("Executor completed: %s", event.executor_id)
                    
                    # Drain any remaining tool step events after executor completes
                    for step_event_data in await drain_step_queue():
                        yield f"data: {json.dumps(format_step_event(step_event_data))}\n\n"
                        logger.info("Emitted tool step (after executor): %s", step_event_data)
                    
                    # Emit parent step completion with duration (only if we emitted a start)
                    step_name = executor_step_names.get(event.executor_id)
                    if step_name and event.executor_id in executor_start_times:
                        start_time = executor_start_times.pop(event.executor_id)
                        duration_ms = int((time.time() - start_time) * 1000)
                        yield f"data: {json.dumps({'step': step_name, 'status': 'completed', 'is_parent': True, 'duration_ms': duration_ms, 'done': False})}\n\n"
                        logger.info("Executor %s completed in %dms", event.executor_id, duration_ms)

                elif isinstance(event, WorkflowOutputEvent):
                    # This is the final rendered response from ChatAgentExecutor
                    # It's a JSON structure with 'text', 'thread_id', and optional 'tool_call'
                    output_data = event.data
                    if isinstance(output_data, str):
                        try:
                            # Parse the structured output
                            parsed = json.loads(output_data)
                            output_text = parsed.get("text", "")
                            foundry_thread_id = parsed.get("thread_id") or foundry_thread_id
                            logger.info("Extracted Foundry thread_id: %s", foundry_thread_id)
                            
                            # Check for tool call data (NL2SQL response)
                            tool_call = parsed.get("tool_call")
                            if tool_call:
                                # Emit tool call event for frontend tool UI rendering
                                yield f"data: {json.dumps({'tool_call': tool_call, 'done': False})}\n\n"
                                logger.info("Emitted tool_call: %s", tool_call.get("tool_name"))
                                output_received = True
                            elif output_text:
                                # Stream the text content only if no tool call
                                chunk_size = 50
                                for i in range(0, len(output_text), chunk_size):
                                    chunk = output_text[i:i + chunk_size]
                                    yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                                    await asyncio.sleep(0.01)
                                output_received = True
                        except json.JSONDecodeError:
                            # Fallback if not JSON (backward compatibility)
                            output_text = output_data
                            chunk_size = 50
                            for i in range(0, len(output_text), chunk_size):
                                chunk = output_text[i:i + chunk_size]
                                yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                                await asyncio.sleep(0.01)
                            output_received = True

                elif isinstance(event, WorkflowStatusEvent):
                    if event.state == WorkflowRunState.IDLE:
                        logger.info("Workflow completed")
                        workflow_done = True
                        continue
                
                # Request next workflow event
                next_event_task = asyncio.ensure_future(workflow_iter.__anext__())

        if not output_received:
            yield f"data: {json.dumps({'content': 'No response generated', 'done': False})}\n\n"

        # Include thread_id in the done signal for the frontend
        yield f"data: {json.dumps({'done': True, 'thread_id': foundry_thread_id})}\n\n"
        
        # Mark span as successful
        workflow_span.set_status(trace.StatusCode.OK)

    except (ValueError, RuntimeError, OSError, TypeError) as e:
        logger.error("Workflow error: %s", e, exc_info=True)
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"
        
        # Record error on span
        workflow_span.set_status(trace.StatusCode.ERROR, str(e))
        workflow_span.record_exception(e)
    finally:
        # Cancel pending tasks
        if next_event_task is not None and not next_event_task.done():
            next_event_task.cancel()
            try:
                await next_event_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
        
        # Cancel the step monitor and drain remaining events
        if step_monitor_task is not None:
            step_monitor_task.cancel()
            try:
                await step_monitor_task
            except asyncio.CancelledError:
                pass
        
        # Emit any remaining step events
        for step_event in await drain_step_queue():
            yield f"data: {json.dumps(format_step_event(step_event))}\n\n"
            logger.info("Emitted final step: %s", step_event)
        
        # Clean up step queue
        clear_step_queue()
        
        # End the workflow span and detach context
        workflow_span.end()
        otel_context.detach(context_token)


async def generate_streaming_response(
    agent: "ChatAgent",
    thread_id: str | None,
    message: str,
    user_id: str | None = None,
    title: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream response using the WorkflowAgent.

    The agent wraps a workflow that processes messages through multiple executors.
    The final output is a JSON structure with 'text' and 'thread_id' from Foundry.
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

        # Emit initial step event
        yield f"data: {json.dumps({'step': 'Analyzing your request...', 'done': False})}\n\n"

        # Stream the response - WorkflowAgent.run_stream yields AgentRunResponseUpdate
        # The executor's final output is a JSON object with 'text' and 'thread_id'
        update_count = 0
        foundry_thread_id = thread_id  # Default to incoming thread_id
        
        async for update in agent.run_stream(message, thread=thread, metadata=thread_metadata):
            update_count += 1
            # AgentRunResponseUpdate has .text property that extracts text from contents
            text = update.text if hasattr(update, 'text') else None
            logger.debug("Stream update #%d: text=%s", update_count, text[:50] if text else None)
            if text:
                # Check if this is structured JSON output from the executor
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict) and "text" in parsed:
                        # Extract the actual text and Foundry thread ID
                        output_text = parsed.get("text", "")
                        foundry_thread_id = parsed.get("thread_id") or foundry_thread_id
                        logger.info("Extracted Foundry thread_id: %s", foundry_thread_id)
                        
                        # Check for tool call data (NL2SQL response)
                        tool_call = parsed.get("tool_call")
                        if tool_call:
                            # Emit step event indicating query generation is complete
                            yield f"data: {json.dumps({'step': 'Generating response...', 'done': False})}\n\n"
                            # Emit tool call event for frontend tool UI rendering
                            yield f"data: {json.dumps({'tool_call': tool_call, 'done': False})}\n\n"
                            logger.info("Emitted tool_call: %s", tool_call.get("tool_name"))
                            # Skip text streaming - tool UI will render instead
                        elif output_text:
                            # Stream the text content only if no tool call
                            chunk_size = 50
                            for i in range(0, len(output_text), chunk_size):
                                chunk = output_text[i:i + chunk_size]
                                yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                                await asyncio.sleep(0.01)
                    # If it's JSON but not our expected format, skip it (don't stream raw JSON)
                except json.JSONDecodeError:
                    # Not JSON - could be intermediate text, skip unless it looks like user-facing content
                    # Only stream if it doesn't look like internal agent communication
                    pass

        logger.info("Run complete after %d updates: foundry_thread_id=%s", update_count, foundry_thread_id)

        # Send the done signal with the Foundry thread ID
        yield f"data: {json.dumps({'done': True, 'thread_id': foundry_thread_id})}\n\n"
        logger.info("Sent done signal to client")

    except (ValueError, RuntimeError, OSError) as e:
        logger.error("Error: %s", e)
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"


@router.get("/stream")
async def chat_stream(
    message: str = Query(..., description="User message"),
    thread_id: str | None = Query(None, description="Foundry thread ID (omit for new thread)"),
    title: str | None = Query(None, description="Thread title (for new threads only)"),
    user_id: str | None = Depends(get_optional_user_id),
):
    """
    SSE streaming chat with workflow-based agent architecture.

    The workflow processes messages through:
    1. ChatAgentExecutor - receives user input
    2. NL2SQLAgentExecutor - processes data queries
    3. ChatAgentExecutor - renders structured response

    Thread support:
    - Omit thread_id for new thread - Foundry will create one
    - Include thread_id to continue existing thread
    - Response includes thread_id for use in subsequent requests
    """
    # Import here to avoid circular imports
    from src.entities.workflow import create_workflow_instance
    
    # Create a fresh workflow instance for this request
    # The Agent Framework doesn't support concurrent workflow executions
    workflow, _, _ = create_workflow_instance()

    # Use workflow streaming for executor-level progress events
    return StreamingResponse(
        generate_workflow_streaming_response(workflow, message, thread_id, user_id, title),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("")
async def chat(
    chat_request: ChatRequest,
    request: Request,
    user_id: str | None = Depends(get_optional_user_id),
):
    """
    Non-streaming chat with workflow-based agent architecture and thread support.

    The workflow processes messages through Chat -> NL2SQL -> Chat executors.
    Threads maintain conversation history across requests.
    """
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    try:
        # Get or create thread for conversation continuity
        thread = agent.get_new_thread(service_thread_id=chat_request.thread_id)

        # Set user_id metadata for new threads only
        thread_metadata = {"user_id": user_id} if user_id and not chat_request.thread_id else None

        # Run the agent with thread support
        response = await agent.run(chat_request.message, thread=thread, metadata=thread_metadata)

        return {
            "response": response.text or str(response),
            "thread_id": thread.service_thread_id,
        }
    except (ValueError, RuntimeError, OSError) as e:
        logger.error("Agent error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
