"use client";

import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { loginRequest } from "@/lib/msalConfig";

export function AuthButton() {
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();

  const handleSignIn = async () => {
    try {
      await instance.loginPopup(loginRequest);
    } catch (error) {
      console.error("Login failed:", error);
    }
  };

  const handleSignOut = async () => {
    try {
      await instance.logoutPopup({
        postLogoutRedirectUri: window.location.origin,
        mainWindowRedirectUri: window.location.origin,
      });
    } catch (error) {
      console.error("Logout failed:", error);
    }
  };

  if (isAuthenticated && accounts.length > 0) {
    const account = accounts[0];
    const displayName = account.name || account.username || "User";

    return (
      <button
        onClick={handleSignOut}
        className="text-gray-300 hover:text-white transition-colors flex items-center gap-2"
      >
        <span className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white text-sm font-medium">
          {displayName.charAt(0).toUpperCase()}
        </span>
        <span>{displayName}</span>
      </button>
    );
  }

  return (
    <button
      onClick={handleSignIn}
      className="text-gray-300 hover:text-white transition-colors"
    >
      Sign In
    </button>
  );
}
