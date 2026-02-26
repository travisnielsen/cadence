/**
 * Foundry Chat Hook
 *
 * Creates an ExternalStoreRuntime for assistant-ui with SSE streaming.
 * Uses backend-issued conversation IDs for continuity.
 * Includes assistant-ui list adapter with local conversation cache for sidebar history.
 */

"use client";

import {
  getConversationMessages,
  streamChat,
  type ClarificationData,
  type StepData,
  type ToolCallData,
} from "@/lib/chatApi";
import { trackEvent, trackException } from "@/lib/telemetry";
import {
  addThreadToCache as addConversationToCache,
  loadCachedThreads as loadCachedConversations,
  removeThreadFromCache as removeConversationFromCache,
  updateCachedThread as updateCachedConversation,
  type CachedThread,
} from "@/lib/threadCache";
import { useAccessToken } from "@/lib/useAccessToken";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type ExternalStoreThreadData,
  type ExternalStoreThreadListAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { useCallback, useEffect, useRef, useState } from "react";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning?: string; // Workflow step/reasoning info (deprecated)
  steps?: StepData[]; // Accumulated workflow steps with timing
  stepsComplete?: boolean; // True when all steps are done (sent before stream ends)
  toolCall?: ToolCallData; // Tool call data for generative UI
}

export function useChatApi() {
  // Get access token for authenticated API calls
  const { acquireToken } = useAccessToken();
  const isAuthenticated = useIsAuthenticated();
  const { accounts } = useMsal();

  // Get user ID from MSAL account (oid claim or localAccountId)
  const userId = accounts[0]?.localAccountId || accounts[0]?.homeAccountId || null;

  // Active conversation ID - null until first message completes
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Pending clarification request state - stores request_id for HITL flow
  const [pendingClarification, setPendingClarification] = useState<ClarificationData | null>(null);
  const pendingClarificationRef = useRef<ClarificationData | null>(null);

  // Keep ref in sync with state
  useEffect(() => {
    pendingClarificationRef.current = pendingClarification;
  }, [pendingClarification]);

  // Ref to track current conversation ID for use in callbacks (avoids stale closure issues)
  const conversationIdRef = useRef<string | null>(null);

  // Keep ref in sync with state
  useEffect(() => {
    conversationIdRef.current = conversationId;
  }, [conversationId]);

  // Conversation list state (mapped to assistant-ui thread-list contract)
  const [conversationItems, setConversationItems] = useState<ExternalStoreThreadData<"regular">[]>([]);
  const [archivedConversationItems, setArchivedConversationItems] = useState<
    ExternalStoreThreadData<"archived">[]
  >([]);
  const [isLoadingThreads, setIsLoadingThreads] = useState(true);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);

  // Helper to convert cached conversations to assistant-ui thread-list entries
  const toExternalConversationData = useCallback(
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

  // Load conversations from local cache when authenticated
  const loadConversations = useCallback(() => {
    if (!isAuthenticated || !userId) {
      setConversationItems([]);
      setArchivedConversationItems([]);
      setIsLoadingThreads(false);
      return;
    }

    try {
      setIsLoadingThreads(true);
      const cached = loadCachedConversations(userId);
      const { regular, archived } = toExternalConversationData(cached);
      setConversationItems(regular);
      setArchivedConversationItems(archived);
    } catch (error) {
      console.error("Failed to load conversation cache:", error);
      setConversationItems([]);
      setArchivedConversationItems([]);
    } finally {
      setIsLoadingThreads(false);
    }
  }, [isAuthenticated, userId, toExternalConversationData]);

  // Load conversation list when authentication state changes
  useEffect(() => {
    loadConversations();
  }, [loadConversations, isAuthenticated, userId]);

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
        // Pass all steps and stepsComplete flag as a single reasoning part
        // The StepIndicator will parse and display them
        const stepsData = {
          steps: m.steps,
          stepsComplete: m.stepsComplete || false,
        };
        const stepsJson = JSON.stringify(stepsData);
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

      trackEvent("ChatMessageSent", {
        isNewConversation: String(!conversationIdRef.current),
        messageLength: String(text.length),
      });

      // Use ref to get current conversation ID (avoids stale closure issues)
      const currentConversationId = conversationIdRef.current;

      // For new conversations, compute a title from the first message
      const isNewConversation = !currentConversationId;
      const chatTitle = isNewConversation
        ? text.length > 50
          ? text.slice(0, 50) + "..."
          : text
        : null;

      // Stream response using the current conversation ID from ref
      abortRef.current = streamChat(
        currentConversationId,
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
        // onComplete - persist conversation ID and add to cached/sidebar conversations
        (newConversationId) => {
          setIsRunning(false);

          trackEvent("ChatResponseReceived", {
            conversationId: newConversationId ?? "unknown",
            isNewConversation: String(isNewConversation),
          });

          // Handle edge case where stream ended without conversation_id
          if (!newConversationId) {
            console.warn("Stream completed without conversation_id");
            return;
          }

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

          // Only add to list if this is actually a new conversation
          // Use isNewConversation captured when the request started
          if (isNewConversation) {
            const title = chatTitle || "New Chat";

            // Add to local cache if we have userId
            if (userId) {
              const cachedConversation: CachedThread = {
                id: newConversationId,
                title,
                status: "regular",
                createdAt: new Date().toISOString(),
              };
              addConversationToCache(userId, cachedConversation);
            }

            // Add to conversation list (assistant-ui thread list adapter)
            setConversationItems((prev) => {
              // Double-check conversation doesn't already exist (edge case)
              if (prev.some((t) => t.id === newConversationId)) {
                return prev;
              }
              const newConversationData: ExternalStoreThreadData<"regular"> = {
                status: "regular",
                id: newConversationId,
                title,
              };
              return [newConversationData, ...prev];
            });
          }

          // Update ref immediately so subsequent messages use correct conversation ID
          conversationIdRef.current = newConversationId;

          // Only update state if it actually changed (for new conversations)
          if (isNewConversation) {
            setConversationId(newConversationId);
          }
        },
        // onError
        (error) => {
          setIsRunning(false);
          trackException(new Error(error), { source: "ChatStream" });
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
          trackEvent("QueryExecuted", { toolName: toolCall.tool_name });
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
        // title for new conversations
        chatTitle,
        // onStepsComplete - mark steps as complete for step indicator
        () => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                stepsComplete: true,
              };
            }
            return updated;
          });
        },
        // requestId - pass pending clarification request_id if this is a response
        pendingClarificationRef.current?.request_id || null,
        // onClarification - store clarification request for HITL flow
        (clarification) => {
          console.log("Received clarification request:", clarification);
          setPendingClarification(clarification);

          // Update assistant message to show clarification UI via toolCall
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              // Convert clarification to a tool_call-like structure for UI rendering
              updated[updated.length - 1] = {
                ...last,
                content: "", // Clear content - clarification UI renders via toolCall
                toolCall: {
                  tool_name: "nl2sql_query",
                  tool_call_id: `clarification-${clarification.request_id}`,
                  args: { question: "" },
                  result: {
                    needs_clarification: true,
                    clarification: {
                      prompt: clarification.prompt,
                      allowed_values: clarification.allowed_values,
                    },
                  },
                },
              };
            }
            return updated;
          });
        }
      );

      // Clear pending clarification after sending (it was used)
      if (pendingClarificationRef.current) {
        setPendingClarification(null);
      }
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

      // Use ref to get current conversation ID (avoids stale closure issues)
      const currentConversationId = conversationIdRef.current;

      // Stream new response
      abortRef.current = streamChat(
        currentConversationId,
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
        (newConversationId) => {
          setIsRunning(false);

          // Handle edge case where stream ended without conversation_id
          if (!newConversationId) {
            console.warn("Stream completed without conversation_id (reload)");
            return;
          }

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

          // Only update conversation ID if it actually changed
          if (newConversationId !== conversationId) {
            setConversationId(newConversationId);
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
        null, // No title update for regeneration
        // onStepsComplete - mark steps as complete for step indicator
        () => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                stepsComplete: true,
              };
            }
            return updated;
          });
        },
        // No request_id for regeneration
        null,
        // onClarification for regeneration (same as regular send)
        (clarification) => {
          console.log("Received clarification request (reload):", clarification);
          setPendingClarification(clarification);
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                content: "",
                toolCall: {
                  tool_name: "nl2sql_query",
                  tool_call_id: `clarification-${clarification.request_id}`,
                  args: { question: "" },
                  result: {
                    needs_clarification: true,
                    clarification: {
                      prompt: clarification.prompt,
                      allowed_values: clarification.allowed_values,
                    },
                  },
                },
              };
            }
            return updated;
          });
        }
      );
    },
    [messages, acquireToken]
  );

  // assistant-ui thread-list adapter (backs conversation sidebar)
  const conversationListAdapter: ExternalStoreThreadListAdapter = {
    threadId: conversationId ?? undefined,
    threads: conversationItems,
    archivedThreads: archivedConversationItems,
    isLoading: isLoadingThreads,

    onSwitchToNewThread: () => {
      // Start a fresh conversation
      conversationIdRef.current = null;
      setConversationId(null);
      setMessages([]);
    },

    onSwitchToThread: async (switchConversationId: string) => {
      trackEvent("ConversationSwitched", { conversationId: switchConversationId });
      // Switch to existing conversation and load its messages
      conversationIdRef.current = switchConversationId;
      setConversationId(switchConversationId);
      setMessages([]); // Clear while loading
      setIsLoadingMessages(true);

      try {
        const accessToken = await acquireToken();
        const apiMessages = await getConversationMessages(switchConversationId, accessToken);

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
        console.error("Failed to load conversation messages:", error);
        trackException(
          error instanceof Error ? error : new Error(String(error)),
          { source: "ConversationLoad", conversationId: switchConversationId },
        );
        // Keep empty messages on error - user can still send new messages
      } finally {
        setIsLoadingMessages(false);
      }
    },

    onRename: async (renameConversationId: string, newTitle: string) => {
      if (!userId) return;

      // Update local cache
      updateCachedConversation(userId, renameConversationId, { title: newTitle });

      // Update UI state
      setConversationItems((prev) =>
        prev.map((t) =>
          t.id === renameConversationId ? { ...t, title: newTitle } : t
        )
      );
      setArchivedConversationItems((prev) =>
        prev.map((t) =>
          t.id === renameConversationId ? { ...t, title: newTitle } : t
        )
      );
    },

    onArchive: async (archiveConversationId: string) => {
      if (!userId) return;

      // Update local cache
      updateCachedConversation(userId, archiveConversationId, { status: "archived" });

      // Move from active list to archived list
      setConversationItems((prev) => {
        const conversation = prev.find((t) => t.id === archiveConversationId);
        if (conversation) {
          // Convert to archived status
          const archivedConversation: ExternalStoreThreadData<"archived"> = {
            ...conversation,
            status: "archived",
          };
          setArchivedConversationItems((archived) => [archivedConversation, ...archived]);
        }
        return prev.filter((t) => t.id !== archiveConversationId);
      });
    },

    onUnarchive: async (unarchiveConversationId: string) => {
      if (!userId) return;

      // Update local cache
      updateCachedConversation(userId, unarchiveConversationId, { status: "regular" });

      // Move from archived list back to active list
      setArchivedConversationItems((prev) => {
        const conversation = prev.find((t) => t.id === unarchiveConversationId);
        if (conversation) {
          // Convert to regular status
          const regularConversation: ExternalStoreThreadData<"regular"> = {
            ...conversation,
            status: "regular",
          };
          setConversationItems((regular) => [regularConversation, ...regular]);
        }
        return prev.filter((t) => t.id !== unarchiveConversationId);
      });
    },

    onDelete: async (deleteConversationId: string) => {
      if (!userId) return;

      // Remove from local cache
      removeConversationFromCache(userId, deleteConversationId);

      // Remove from UI state
      setConversationItems((prev) => prev.filter((t) => t.id !== deleteConversationId));
      setArchivedConversationItems((prev) => prev.filter((t) => t.id !== deleteConversationId));

      // If we deleted the current conversation, start fresh
      if (conversationId === deleteConversationId) {
        setConversationId(null);
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
      //   body: JSON.stringify({ messageId: message.id, conversationId, type }),
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
      threadList: conversationListAdapter,
      feedback: feedbackAdapter,
    },
  });

  return {
    runtime,
    conversationId,
    // Compatibility alias for assistant-ui consumer expectations.
    threadId: conversationId,
    messages,
    isRunning,
    isLoadingThreads,
    isLoadingMessages,
  };
}
