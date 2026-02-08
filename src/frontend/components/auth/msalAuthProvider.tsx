"use client";

import { MsalProvider } from "@azure/msal-react";
import { PublicClientApplication, EventType, EventMessage, AuthenticationResult } from "@azure/msal-browser";
import { msalConfig, loginRequest } from "@/lib/msalConfig";
import { useEffect, useState, useRef } from "react";

export function MsalAuthProvider({ children }: { children: React.ReactNode }) {
  const [isInitialized, setIsInitialized] = useState(false);
  const msalInstanceRef = useRef<PublicClientApplication | null>(null);

  useEffect(() => {
    const initializeMsal = async () => {
      // Create instance lazily on the client to avoid SSR "window is not defined" errors
      if (!msalInstanceRef.current) {
        msalInstanceRef.current = new PublicClientApplication(msalConfig);
      }
      const msalInstance = msalInstanceRef.current;

      await msalInstance.initialize();
      
      // Handle redirect response
      await msalInstance.handleRedirectPromise();

      // Set active account if there is one
      const accounts = msalInstance.getAllAccounts();
      if (accounts.length > 0) {
        msalInstance.setActiveAccount(accounts[0]);
        setIsInitialized(true);
      } else {
        // No accounts - trigger sign-in automatically
        msalInstance.loginRedirect(loginRequest);
        // Don't set initialized - we're redirecting away
        return;
      }

      // Listen for sign-in events
      msalInstance.addEventCallback((event: EventMessage) => {
        if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
          const payload = event.payload as AuthenticationResult;
          msalInstance.setActiveAccount(payload.account);
        }
      });
    };

    initializeMsal();
  }, []);

  if (!isInitialized || !msalInstanceRef.current) {
    return null; // Or a loading spinner
  }

  return (
    <MsalProvider instance={msalInstanceRef.current}>
      {children}
    </MsalProvider>
  );
}
