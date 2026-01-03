"use client";

/**
 * Assistant Component
 *
 * Simple chat interface using assistant-ui with a custom backend based on Microsoft Agent Framework.
 * Uses ExternalStoreRuntime for SSE streaming with thread list sidebar.
 */

import { Loader2 } from "lucide-react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadListSidebar } from "@/components/assistant-ui/threadlist-sidebar";
import { useChatApi } from "@/hooks/useChatApi";
import { AuthButton } from "@/components/ui/authButton";
import { NL2SQLToolUI } from "@/components/assistant-ui/nl2sql-tool-ui";

export const Assistant = () => {
  const { runtime, isLoadingMessages } = useChatApi();

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {/* Register tool UIs for generative UI rendering */}
      <NL2SQLToolUI />
      
      <SidebarProvider>
        <ThreadListSidebar />
        <SidebarInset>
          <div className="flex h-dvh w-full flex-col">
            <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
              <h1 className="text-lg font-semibold">Data Agent Chat</h1>
              <AuthButton />
            </header>
            <div className="flex-1 overflow-hidden">
              {isLoadingMessages ? (
                <div className="flex h-full items-center justify-center">
                  <div className="flex flex-col items-center gap-2 text-muted-foreground">
                    <Loader2 className="h-8 w-8 animate-spin" />
                    <span>Loading conversation...</span>
                  </div>
                </div>
              ) : (
                <Thread />
              )}
            </div>
          </div>
        </SidebarInset>
      </SidebarProvider>
    </AssistantRuntimeProvider>
  );
};
