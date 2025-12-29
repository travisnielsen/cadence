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
import { streamChat, getThreadMessages } from "@/lib/chatApi";
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
    (m: Message): ThreadMessageLike => ({
      id: m.id,
      role: m.role,
      content: [{ type: "text", text: m.content }],
    }),
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

      // For new threads, compute a title from the first message
      const isNewThread = !threadId;
      const chatTitle = isNewThread
        ? text.length > 50
          ? text.slice(0, 50) + "..."
          : text
        : null;

      // Stream response
      abortRef.current = streamChat(
        threadId,
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
          setThreadId(newThreadId);
          setIsRunning(false);
          
          // If this was a new thread, add it to the cache and list
          if (isNewThread && userId) {
            const title = chatTitle || "New Chat";
            
            // Add to local cache
            const cachedThread: CachedThread = {
              id: newThreadId,
              title,
              status: "regular",
              createdAt: new Date().toISOString(),
            };
            addThreadToCache(userId, cachedThread);
            
            // Update UI state
            const newThreadData: ExternalStoreThreadData<"regular"> = {
              status: "regular",
              id: newThreadId,
              title,
            };
            setThreads((prev) => [newThreadData, ...prev]);
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
        // accessToken for Authorization header
        accessToken,
        // title for new threads
        chatTitle
      );
    },
    [threadId, acquireToken, userId]
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

      // Stream new response
      abortRef.current = streamChat(
        threadId,
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
          setThreadId(newThreadId);
          setIsRunning(false);
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
        accessToken,
        null // No title update for regeneration
      );
    },
    [threadId, messages, acquireToken]
  );

  // Thread list adapter for sidebar
  const threadListAdapter: ExternalStoreThreadListAdapter = {
    threadId: threadId ?? undefined,
    threads,
    archivedThreads,
    isLoading: isLoadingThreads,

    onSwitchToNewThread: () => {
      // Start a fresh conversation
      setThreadId(null);
      setMessages([]);
    },

    onSwitchToThread: async (switchThreadId: string) => {
      // Switch to existing thread and load its messages
      setThreadId(switchThreadId);
      setMessages([]); // Clear while loading
      setIsLoadingMessages(true);
      
      try {
        const accessToken = await acquireToken();
        const apiMessages = await getThreadMessages(switchThreadId, accessToken);
        
        // Convert API messages to our Message format
        const loadedMessages: Message[] = apiMessages.map((msg) => ({
          id: msg.id,
          role: msg.role as "user" | "assistant",
          content: msg.content,
        }));
        
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
