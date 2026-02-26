"use client";

import { MsalAuthProvider } from "@/components/auth/msalAuthProvider";
import { AppInsightsContext, reactPlugin } from "@/lib/telemetry";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <MsalAuthProvider>
      <AppInsightsContext.Provider value={reactPlugin}>
        {children}
      </AppInsightsContext.Provider>
    </MsalAuthProvider>
  );
}
