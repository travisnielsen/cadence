/**
 * Foundry API Client
 *
 * Uses Foundry thread IDs directly - no local session management.
 * Thread ID is returned by the backend on first message.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Tool call data from the backend (e.g., NL2SQL results)
 */
export interface ToolCallData {
  tool_name: string;
  tool_call_id: string;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
}

/**
 * Step event data with optional timing
 */
export interface StepData {
  step: string;
  status?: "started" | "completed";
  duration_ms?: number;
  is_parent?: boolean; // True for executor-level steps that contain child tool steps
}

export interface StreamChunk {
  thread_id?: string; // Foundry thread ID (returned with done: true)
  content?: string;
  reasoning?: string; // Workflow step/reasoning info (deprecated)
  step?: string; // Step name
  status?: "started" | "completed"; // Step status
  duration_ms?: number; // Step duration in ms (for completed steps)
  is_parent?: boolean; // True for parent/executor steps
  tool_call?: ToolCallData; // Tool call data for generative UI
  done?: boolean;
  error?: string;
}

/**
 * Stream a chat message via SSE.
 *
 * @param threadId - Pass null for new thread, or existing Foundry thread ID
 * @param message - The user's message
 * @param onChunk - Called with each content chunk
 * @param onComplete - Called with Foundry thread_id when stream completes
 * @param onError - Called on error
 * @param onReasoning - Called with reasoning/step info for UI display (deprecated, use onStep)
 * @param onToolCall - Called with tool call data for generative UI
 * @param onStep - Called with step data including timing information
 * @param accessToken - Optional access token for authentication
 * @param title - Optional title for new threads (truncated first message)
 */
export function streamChat(
  threadId: string | null,
  message: string,
  onChunk: (content: string) => void,
  onComplete: (threadId: string) => void,
  onError: (error: string) => void,
  onReasoning?: (reasoning: string) => void,
  onToolCall?: (toolCall: ToolCallData) => void,
  onStep?: (stepData: StepData) => void,
  accessToken?: string | null,
  title?: string | null
): AbortController {
  const abortController = new AbortController();

  // Build URL - omit thread_id param if null (new thread)
  let url = `${API_BASE_URL}/api/chat/stream?message=${encodeURIComponent(message)}`;
  if (threadId) {
    url += `&thread_id=${encodeURIComponent(threadId)}`;
  }
  // For new threads, pass the title
  if (!threadId && title) {
    url += `&title=${encodeURIComponent(title)}`;
  }

  // Build headers with optional auth
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  fetch(url, {
    method: "GET",
    headers,
    signal: abortController.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error("No response body");
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const chunk: StreamChunk = JSON.parse(line.slice(6));

              if (chunk.error) {
                onError(chunk.error);
                return;
              }
              if (chunk.reasoning && onReasoning) {
                onReasoning(chunk.reasoning);
              }
              if (chunk.step && onStep) {
                onStep({
                  step: chunk.step,
                  status: chunk.status,
                  duration_ms: chunk.duration_ms,
                  is_parent: chunk.is_parent,
                });
              }
              if (chunk.tool_call && onToolCall) {
                onToolCall(chunk.tool_call);
              }
              if (chunk.content) {
                onChunk(chunk.content);
              }
              if (chunk.done && chunk.thread_id) {
                onComplete(chunk.thread_id);
                return;
              }
            } catch {
              // Skip malformed JSON
            }
          }
        }
      }
    })
    .catch((error) => {
      if (error.name !== "AbortError") {
        onError(error.message);
      }
    });

  return abortController;
}

// ============================================================================
// Thread List API
// ============================================================================

export interface ThreadData {
  thread_id: string;
  title: string | null;
  status: "regular" | "archived";
  created_at: string | null;
}

export interface ThreadListResponse {
  threads: ThreadData[];
}

export interface MessageData {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string | null;
}

export interface MessagesResponse {
  messages: MessageData[];
}

/**
 * Fetch all threads for the current user.
 */
export async function listThreads(
  accessToken?: string | null
): Promise<ThreadData[]> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${API_BASE_URL}/api/threads`, {
    method: "GET",
    headers,
  });

  if (!response.ok) {
    throw new Error(`Failed to list threads: ${response.status}`);
  }

  const data: ThreadListResponse = await response.json();
  return data.threads;
}

/**
 * Fetch all messages for a thread.
 * Returns messages in chronological order (oldest first).
 */
export async function getThreadMessages(
  threadId: string,
  accessToken?: string | null
): Promise<MessageData[]> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${API_BASE_URL}/api/threads/${threadId}/messages`, {
    method: "GET",
    headers,
  });

  if (!response.ok) {
    throw new Error(`Failed to get thread messages: ${response.status}`);
  }

  const data: MessagesResponse = await response.json();
  return data.messages;
}

/**
 * Get a specific thread by ID.
 */
export async function getThread(
  threadId: string,
  accessToken?: string | null
): Promise<ThreadData> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${API_BASE_URL}/api/threads/${threadId}`, {
    method: "GET",
    headers,
  });

  if (!response.ok) {
    throw new Error(`Failed to get thread: ${response.status}`);
  }

  return response.json();
}

/**
 * Update a thread's metadata (title, status).
 */
export async function updateThread(
  threadId: string,
  updates: { title?: string; status?: string },
  accessToken?: string | null
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${API_BASE_URL}/api/threads/${threadId}`, {
    method: "PATCH",
    headers,
    body: JSON.stringify(updates),
  });

  if (!response.ok) {
    throw new Error(`Failed to update thread: ${response.status}`);
  }
}

/**
 * Delete a thread.
 */
export async function deleteThread(
  threadId: string,
  accessToken?: string | null
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${API_BASE_URL}/api/threads/${threadId}`, {
    method: "DELETE",
    headers,
  });

  if (!response.ok) {
    throw new Error(`Failed to delete thread: ${response.status}`);
  }
}
