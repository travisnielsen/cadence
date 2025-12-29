/**
 * Local Thread Cache
 * 
 * Caches thread history in localStorage per user.
 * Avoids expensive API round-trips since Azure AI Agents API
 * doesn't support server-side metadata filtering.
 */

export interface CachedThread {
  id: string;           // Foundry thread ID
  title: string;        // Thread title (first message truncated)
  status: "regular" | "archived";
  createdAt: string;    // ISO timestamp
}

const CACHE_VERSION = "v1";

/**
 * Get the localStorage key for a user's thread cache
 */
function getCacheKey(userId: string): string {
  return `dataagent_threads_${CACHE_VERSION}_${userId}`;
}

/**
 * Load all cached threads for a user
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
    console.error("Failed to load thread cache:", error);
    return [];
  }
}

/**
 * Save all threads to cache for a user
 */
export function saveCachedThreads(userId: string, threads: CachedThread[]): void {
  if (typeof window === "undefined") return;
  
  try {
    const key = getCacheKey(userId);
    localStorage.setItem(key, JSON.stringify(threads));
  } catch (error) {
    console.error("Failed to save thread cache:", error);
  }
}

/**
 * Add a new thread to the cache (prepends to list)
 */
export function addThreadToCache(
  userId: string,
  thread: CachedThread
): CachedThread[] {
  const threads = loadCachedThreads(userId);
  // Avoid duplicates
  const filtered = threads.filter((t) => t.id !== thread.id);
  const updated = [thread, ...filtered];
  saveCachedThreads(userId, updated);
  return updated;
}

/**
 * Update a thread in the cache (e.g., archive/unarchive, rename)
 */
export function updateCachedThread(
  userId: string,
  threadId: string,
  updates: Partial<Pick<CachedThread, "title" | "status">>
): CachedThread[] {
  const threads = loadCachedThreads(userId);
  const updated = threads.map((t) =>
    t.id === threadId ? { ...t, ...updates } : t
  );
  saveCachedThreads(userId, updated);
  return updated;
}

/**
 * Remove a thread from the cache
 */
export function removeThreadFromCache(
  userId: string,
  threadId: string
): CachedThread[] {
  const threads = loadCachedThreads(userId);
  const updated = threads.filter((t) => t.id !== threadId);
  saveCachedThreads(userId, updated);
  return updated;
}

/**
 * Clear all cached threads for a user
 */
export function clearThreadCache(userId: string): void {
  if (typeof window === "undefined") return;
  
  try {
    const key = getCacheKey(userId);
    localStorage.removeItem(key);
  } catch (error) {
    console.error("Failed to clear thread cache:", error);
  }
}
