/**
 * Local Conversation Cache
 *
 * Caches conversation history in localStorage per user.
 * Avoids expensive API round-trips since Azure AI Agents API
 * doesn't support server-side metadata filtering.
 */

export interface CachedThread {
  id: string;           // Conversation ID
  title: string;        // Conversation title (first message truncated)
  status: "regular" | "archived";
  createdAt: string;    // ISO timestamp
}

const CACHE_VERSION = "v1";

/**
 * Get the localStorage key for a user's conversation cache
 */
function getCacheKey(userId: string): string {
  return `cadence_threads_${CACHE_VERSION}_${userId}`;
}

/**
 * Load all cached conversations for a user
 */
export function loadCachedThreads(userId: string): CachedThread[] {
  if (typeof window === "undefined") return [];

  try {
    const key = getCacheKey(userId);
    const data = localStorage.getItem(key);
    if (!data) return [];

    const parsed = JSON.parse(data);
    // Validate it's an array
    if (!Array.isArray(parsed)) return [];

    return parsed;
  } catch (error) {
    console.error("Failed to load conversation cache:", error);
    return [];
  }
}

/**
 * Save all cached conversations for a user
 */
export function saveCachedThreads(userId: string, threads: CachedThread[]): void {
  if (typeof window === "undefined") return;

  try {
    const key = getCacheKey(userId);
    localStorage.setItem(key, JSON.stringify(threads));
  } catch (error) {
    console.error("Failed to save conversation cache:", error);
  }
}

/**
 * Add a new conversation to the cache (prepends to list)
 */
export function addThreadToCache(
  userId: string,
  conversation: CachedThread
): CachedThread[] {
  const conversations = loadCachedThreads(userId);
  // Avoid duplicates
  const filtered = conversations.filter((t) => t.id !== conversation.id);
  const updated = [conversation, ...filtered];
  saveCachedThreads(userId, updated);
  return updated;
}

/**
 * Update a conversation in the cache (e.g., archive/unarchive, rename)
 */
export function updateCachedThread(
  userId: string,
  conversationId: string,
  updates: Partial<Pick<CachedThread, "title" | "status">>
): CachedThread[] {
  const conversations = loadCachedThreads(userId);
  const updated = conversations.map((t) =>
    t.id === conversationId ? { ...t, ...updates } : t
  );
  saveCachedThreads(userId, updated);
  return updated;
}

/**
 * Remove a conversation from the cache
 */
export function removeThreadFromCache(
  userId: string,
  conversationId: string
): CachedThread[] {
  const conversations = loadCachedThreads(userId);
  const updated = conversations.filter((t) => t.id !== conversationId);
  saveCachedThreads(userId, updated);
  return updated;
}

/**
 * Clear all cached conversations for a user
 */
export function clearThreadCache(userId: string): void {
  if (typeof window === "undefined") return;

  try {
    const key = getCacheKey(userId);
    localStorage.removeItem(key);
  } catch (error) {
    console.error("Failed to clear conversation cache:", error);
  }
}
