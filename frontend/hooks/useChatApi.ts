/**
 * Foundry Chat Hook
 *
 * Creates an ExternalStoreRuntime for assistant-ui with SSE streaming.
 * Uses Foundry thread IDs directly - no local session management.
 * Includes thread list adapter with local caching for session history sidebar.
 */

"use client";

import { useCallback, useRef, useState, useEffect } from "react";
import {
  useExternalStoreRuntime,
  type ThreadMessageLike,
  type AppendMessage,
  type ExternalStoreThreadListAdapter,
  type ExternalStoreThreadData,
} from "@assistant-ui/react";
import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { streamChat, getThreadMessages, type ToolCallData, type StepData } from "@/lib/chatApi";
import { useAccessToken } from "@/lib/useAccessToken";
import {
  loadCachedThreads,
  addThreadToCache,
  updateCachedThread,
  removeThreadFromCache,
  type CachedThread,
} from "@/lib/threadCache";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string; // Workflow step/reasoning info (deprecated)
  steps?: StepData[]; // Accumulated workflow steps with timing
  toolCall?: ToolCallData; // Tool call data for generative UI
}

export function useChatApi() {
  // Get access token for authenticated API calls
  const { acquireToken } = useAccessToken();
  const isAuthenticated = useIsAuthenticated();
  const { accounts } = useMsal();
  
  // Get user ID from MSAL account (oid claim or localAccountId)
  const userId = accounts[0]?.localAccountId || accounts[0]?.homeAccountId || null;

  // Foundry thread ID - null until first message completes
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  
  // Ref to track current threadId for use in callbacks (avoids stale closure issues)
  const threadIdRef = useRef<string | null>(null);
  
  // Keep ref in sync with state
  useEffect(() => {
    threadIdRef.current = threadId;
  }, [threadId]);

  // Thread list state
  const [threads, setThreads] = useState<ExternalStoreThreadData<"regular">[]>([]);
  const [archivedThreads, setArchivedThreads] = useState<ExternalStoreThreadData<"archived">[]>([]);
  const [isLoadingThreads, setIsLoadingThreads] = useState(true);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);

  // Helper to convert CachedThread to ExternalStoreThreadData
  const toExternalThreadData = useCallback(
    (cached: CachedThread[]): {
      regular: ExternalStoreThreadData<"regular">[];
      archived: ExternalStoreThreadData<"archived">[];
    } => {
      const regular: ExternalStoreThreadData<"regular">[] = [];
      const archived: ExternalStoreThreadData<"archived">[] = [];

      for (const t of cached) {
        if (t.status === "archived") {
          archived.push({
            status: "archived",
            id: t.id,
            title: t.title || "New Chat",
          });
        } else {
          regular.push({
            status: "regular",
            id: t.id,
            title: t.title || "New Chat",
          });
        }
      }

      return { regular, archived };
    },
    []
  );

  // Load threads from local cache when authenticated
  const loadThreads = useCallback(() => {
    if (!isAuthenticated || !userId) {
      setThreads([]);
      setArchivedThreads([]);
      setIsLoadingThreads(false);
      return;
    }

    try {
      setIsLoadingThreads(true);
      const cached = loadCachedThreads(userId);
      const { regular, archived } = toExternalThreadData(cached);
      setThreads(regular);
      setArchivedThreads(archived);
    } catch (error) {
      console.error("Failed to load thread cache:", error);
      setThreads([]);
      setArchivedThreads([]);
    } finally {
      setIsLoadingThreads(false);
    }
  }, [isAuthenticated, userId, toExternalThreadData]);

  // Load threads when authentication state changes
  useEffect(() => {
    loadThreads();
  }, [loadThreads, isAuthenticated, userId]);

  // Convert our Message to assistant-ui format
  const convertMessage = useCallback(
    (m: Message): ThreadMessageLike => {
      // Build content parts array using assistant-ui's expected types
      const parts: Array<
        | { type: "text"; text: string }
        | { type: "reasoning"; text: string }
        | { 
            type: "tool-call"; 
            toolCallId: string; 
            toolName: string; 
            args: Record<string, string | number | boolean | null>; 
            result?: Record<string, unknown>;
          }
      > = [];
      
      // Add steps as reasoning parts (shows workflow step progress)
      // Use steps array first (new), fallback to reasoning (deprecated)
      if (m.steps && m.steps.length > 0) {
        // Pass all steps as a single reasoning part with the full list
        // The StepIndicator will parse and display them
        const stepsJson = JSON.stringify(m.steps);
        parts.push({ type: "reasoning", text: stepsJson });
      } else if (m.reasoning) {
        parts.push({ type: "reasoning", text: m.reasoning });
      }
      
      // Add tool call part if present (for generative UI)
      if (m.toolCall) {
        parts.push({
          type: "tool-call",
          toolCallId: m.toolCall.tool_call_id,
          toolName: m.toolCall.tool_name,
          args: m.toolCall.args as Record<string, string | number | boolean | null>,
          result: m.toolCall.result,
        });
      }
      
      // Add the main text content (if no tool call, or as fallback)
      // When we have a tool call, we can skip the text since the tool UI renders it
      if (!m.toolCall && m.content) {
        parts.push({ type: "text", text: m.content });
      }
      
      return {
        id: m.id,
        role: m.role,
        content: parts.length > 0 ? parts : [{ type: "text", text: m.content }],
      };
    },
    []
  );

  const onNew = useCallback(
    async (message: AppendMessage) => {
      // Extract text
      const text = message.content
        .filter((p) => p.type === "text")
        .map((p) => (p as { type: "text"; text: string }).text)
        .join("");

      if (!text.trim()) return;

      // Cancel any in-flight request
      abortRef.current?.abort();

      // Acquire access token for authenticated API call
      const accessToken = await acquireToken();

      // Add user message
      const userMsg: Message = {
        id: `user-${Date.now()}`,
        role: "user",
        content: text,
      };

      // Add empty assistant message placeholder
      const assistantMsg: Message = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: "",
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsRunning(true);

      // Use ref to get current threadId (avoids stale closure issues)
      const currentThreadId = threadIdRef.current;
      
      // For new threads, compute a title from the first message
      const isNewThread = !currentThreadId;
      const chatTitle = isNewThread
        ? text.length > 50
          ? text.slice(0, 50) + "..."
          : text
        : null;

      // Stream response - use currentThreadId from ref
      abortRef.current = streamChat(
        currentThreadId,
        text,
        // onChunk - append to assistant message
        (chunk) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                content: last.content + chunk,
              };
            }
            return updated;
          });
        },
        // onComplete - save Foundry thread ID and add to thread list/cache
        (newThreadId) => {
          setIsRunning(false);
          
          // Clear only legacy reasoning, keep steps for display
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant" && last.reasoning) {
              updated[updated.length - 1] = {
                ...last,
                reasoning: undefined,
              };
            }
            return updated;
          });
          
          // Only add to thread list if this is actually a new thread
          // Use isNewThread which was captured when the request started
          if (isNewThread) {
            const title = chatTitle || "New Chat";
            
            // Add to local cache if we have userId
            if (userId) {
              const cachedThread: CachedThread = {
                id: newThreadId,
                title,
                status: "regular",
                createdAt: new Date().toISOString(),
              };
              addThreadToCache(userId, cachedThread);
            }
            
            // Add to thread list
            setThreads((prev) => {
              // Double-check thread doesn't already exist (edge case)
              if (prev.some((t) => t.id === newThreadId)) {
                return prev;
              }
              const newThreadData: ExternalStoreThreadData<"regular"> = {
                status: "regular",
                id: newThreadId,
                title,
              };
              return [newThreadData, ...prev];
            });
          }
          
          // Update ref immediately so subsequent messages use correct threadId
          threadIdRef.current = newThreadId;
          
          // Only update state if it actually changed (for new threads)
          if (isNewThread) {
            setThreadId(newThreadId);
          }
        },
        // onError
        (error) => {
          setIsRunning(false);
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                content: `Error: ${error}`,
              };
            }
            return updated;
          });
        },
        // onReasoning - update reasoning/step info on assistant message
        (reasoning) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                reasoning,
              };
            }
            return updated;
          });
        },
        // onToolCall - store tool call data for generative UI
        (toolCall) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                toolCall,
                // Clear content since tool UI will render instead
                content: "",
              };
            }
            return updated;
          });
        },
        // onStep - accumulate steps in assistant message with timing
        (stepData) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              const existingSteps = last.steps || [];
              
              // Check if this is a completion event for an existing step
              if (stepData.status === "completed") {
                // Find and update the matching started step with duration
                const stepIndex = existingSteps.findIndex(
                  s => s.step === stepData.step && s.status === "started"
                );
                if (stepIndex !== -1) {
                  // Update existing started step to completed
                  const updatedSteps = [...existingSteps];
                  updatedSteps[stepIndex] = {
                    ...updatedSteps[stepIndex],
                    status: "completed",
                    duration_ms: stepData.duration_ms,
                    is_parent: stepData.is_parent,
                  };
                  updated[updated.length - 1] = {
                    ...last,
                    steps: updatedSteps,
                  };
                } else {
                  // No matching started step - add completed step directly
                  // This handles cases where events arrive out of order or start was missed
                  updated[updated.length - 1] = {
                    ...last,
                    steps: [...existingSteps, { ...stepData }],
                  };
                }
              } else {
                // New step starting - add if not already present
                const alreadyExists = existingSteps.some(s => s.step === stepData.step);
                if (!alreadyExists) {
                  updated[updated.length - 1] = {
                    ...last,
                    steps: [...existingSteps, { ...stepData }],
                  };
                }
              }
            }
            return updated;
          });
        },
        // accessToken for Authorization header
        accessToken,
        // title for new threads
        chatTitle
      );
    },
    [acquireToken, userId]
  );

  const onCancel = useCallback(async () => {
    abortRef.current?.abort();
    setIsRunning(false);
  }, []);

  // Reload/regenerate the last assistant response
  const onReload = useCallback(
    async (parentId: string | null) => {
      if (!parentId) return;
      
      // Find the last user message (the one before the assistant message being regenerated)
      const messageIndex = messages.findIndex((m) => m.id === parentId);
      if (messageIndex === -1) return;

      // The parent is the user message, find its content
      const userMessage = messages[messageIndex];
      if (userMessage.role !== "user") return;

      // Cancel any in-flight request
      abortRef.current?.abort();

      // Acquire access token
      const accessToken = await acquireToken();

      // Remove the old assistant response and add a new placeholder
      const assistantMsg: Message = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: "",
      };

      // Keep messages up to and including the user message, then add new assistant placeholder
      setMessages((prev) => [...prev.slice(0, messageIndex + 1), assistantMsg]);
      setIsRunning(true);

      // Use ref to get current threadId (avoids stale closure issues)
      const currentThreadId = threadIdRef.current;

      // Stream new response
      abortRef.current = streamChat(
        currentThreadId,
        userMessage.content,
        // onChunk
        (chunk) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                content: last.content + chunk,
              };
            }
            return updated;
          });
        },
        // onComplete
        (newThreadId) => {
          setIsRunning(false);
          
          // Clear only legacy reasoning, keep steps for display
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant" && last.reasoning) {
              updated[updated.length - 1] = {
                ...last,
                reasoning: undefined,
              };
            }
            return updated;
          });
          
          // Only update threadId if it actually changed
          if (newThreadId !== threadId) {
            setThreadId(newThreadId);
          }
        },
        // onError
        (error) => {
          setIsRunning(false);
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                content: `Error: ${error}`,
              };
            }
            return updated;
          });
        },
        // onReasoning - update reasoning/step info on assistant message
        (reasoning) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                reasoning,
              };
            }
            return updated;
          });
        },
        // onToolCall - store tool call data for generative UI
        (toolCall) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                toolCall,
                // Clear content since tool UI will render instead
                content: "",
              };
            }
            return updated;
          });
        },
        // onStep - accumulate steps in assistant message with timing
        (stepData) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              const existingSteps = last.steps || [];
              
              if (stepData.status === "completed") {
                const stepIndex = existingSteps.findIndex(
                  s => s.step === stepData.step && s.status === "started"
                );
                if (stepIndex !== -1) {
                  const updatedSteps = [...existingSteps];
                  updatedSteps[stepIndex] = {
                    ...updatedSteps[stepIndex],
                    status: "completed",
                    duration_ms: stepData.duration_ms,
                    is_parent: stepData.is_parent,
                  };
                  updated[updated.length - 1] = {
                    ...last,
                    steps: updatedSteps,
                  };
                }
              } else {
                const alreadyExists = existingSteps.some(s => s.step === stepData.step);
                if (!alreadyExists) {
                  updated[updated.length - 1] = {
                    ...last,
                    steps: [...existingSteps, { ...stepData }],
                  };
                }
              }
            }
            return updated;
          });
        },
        accessToken,
        null // No title update for regeneration
      );
    },
    [messages, acquireToken]
  );

  // Thread list adapter for sidebar
  const threadListAdapter: ExternalStoreThreadListAdapter = {
    threadId: threadId ?? undefined,
    threads,
    archivedThreads,
    isLoading: isLoadingThreads,

    onSwitchToNewThread: () => {
      // Start a fresh conversation
      threadIdRef.current = null;
      setThreadId(null);
      setMessages([]);
    },

    onSwitchToThread: async (switchThreadId: string) => {
      // Switch to existing thread and load its messages
      threadIdRef.current = switchThreadId;
      setThreadId(switchThreadId);
      setMessages([]); // Clear while loading
      setIsLoadingMessages(true);
      
      try {
        const accessToken = await acquireToken();
        const apiMessages = await getThreadMessages(switchThreadId, accessToken);
        
        // Convert API messages to our Message format, parsing tool calls from stored content
        const loadedMessages: Message[] = apiMessages.map((msg) => {
          const message: Message = {
            id: msg.id,
            role: msg.role as "user" | "assistant",
            content: msg.content,
          };
          
          // Keep original markdown content for stored messages
          // The markdown already includes formatted tables and collapsible SQL query
          // Tool UI rendering is only for live streaming responses
          
          return message;
        });
        
        setMessages(loadedMessages);
      } catch (error) {
        console.error("Failed to load thread messages:", error);
        // Keep empty messages on error - user can still send new messages
      } finally {
        setIsLoadingMessages(false);
      }
    },

    onRename: async (renameThreadId: string, newTitle: string) => {
      if (!userId) return;
      
      // Update local cache
      updateCachedThread(userId, renameThreadId, { title: newTitle });
      
      // Update UI state
      setThreads((prev) =>
        prev.map((t) =>
          t.id === renameThreadId ? { ...t, title: newTitle } : t
        )
      );
      setArchivedThreads((prev) =>
        prev.map((t) =>
          t.id === renameThreadId ? { ...t, title: newTitle } : t
        )
      );
    },

    onArchive: async (archiveThreadId: string) => {
      if (!userId) return;
      
      // Update local cache
      updateCachedThread(userId, archiveThreadId, { status: "archived" });
      
      // Move from threads to archivedThreads
      setThreads((prev) => {
        const thread = prev.find((t) => t.id === archiveThreadId);
        if (thread) {
          // Convert to archived status
          const archivedThread: ExternalStoreThreadData<"archived"> = {
            ...thread,
            status: "archived",
          };
          setArchivedThreads((archived) => [archivedThread, ...archived]);
        }
        return prev.filter((t) => t.id !== archiveThreadId);
      });
    },

    onUnarchive: async (unarchiveThreadId: string) => {
      if (!userId) return;
      
      // Update local cache
      updateCachedThread(userId, unarchiveThreadId, { status: "regular" });
      
      // Move from archivedThreads to threads
      setArchivedThreads((prev) => {
        const thread = prev.find((t) => t.id === unarchiveThreadId);
        if (thread) {
          // Convert to regular status
          const regularThread: ExternalStoreThreadData<"regular"> = {
            ...thread,
            status: "regular",
          };
          setThreads((regular) => [regularThread, ...regular]);
        }
        return prev.filter((t) => t.id !== unarchiveThreadId);
      });
    },

    onDelete: async (deleteThreadId: string) => {
      if (!userId) return;
      
      // Remove from local cache
      removeThreadFromCache(userId, deleteThreadId);
      
      // Remove from UI state
      setThreads((prev) => prev.filter((t) => t.id !== deleteThreadId));
      setArchivedThreads((prev) => prev.filter((t) => t.id !== deleteThreadId));
      
      // If we deleted the current thread, start fresh
      if (threadId === deleteThreadId) {
        setThreadId(null);
        setMessages([]);
      }
    },
  };

  // Feedback adapter for thumbs up/down
  const feedbackAdapter = {
    submit: ({ message, type }: { message: { id?: string }; type: "positive" | "negative" }) => {
      // Log feedback (you can extend this to send to an API)
      console.log(`Feedback submitted: ${type} for message ${message.id}`);
      
      // Optional: Send feedback to your backend
      // const accessToken = await acquireToken();
      // await fetch(`${API_BASE_URL}/api/feedback`, {
      //   method: "POST",
      //   headers: {
      //     "Content-Type": "application/json",
      //     ...(accessToken && { Authorization: `Bearer ${accessToken}` }),
      //   },
      //   body: JSON.stringify({ messageId: message.id, threadId, type }),
      // });
    },
  };

  // Create the runtime
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage,
    isRunning,
    onNew,
    onCancel,
    onReload,
    adapters: {
      threadList: threadListAdapter,
      feedback: feedbackAdapter,
    },
  });

  return { runtime, threadId, messages, isRunning, isLoadingThreads, isLoadingMessages };
}
