"use client";

import { MsalAuthProvider } from "@/components/auth/msalAuthProvider";

export function Providers({ children }: { children: React.ReactNode }) {
  return <MsalAuthProvider>{children}</MsalAuthProvider>;
}
