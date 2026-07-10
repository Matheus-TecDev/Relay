import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { fetchCurrentUser, login as loginRequest } from "../api/auth";
import { configureApiClient } from "../api/client";

type AuthUser = {
  username: string;
};

type AuthContextValue = {
  user: AuthUser | null;
  token: string | null;
  isAuthenticated: boolean;
  isInitializing: boolean;
  sessionExpired: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  clearSessionExpired: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);
const tokenStorageKey = "relay.auth.token";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => sessionStorage.getItem(tokenStorageKey));
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isInitializing, setIsInitializing] = useState(Boolean(token));
  const [sessionExpired, setSessionExpired] = useState(false);

  function clearSession(markExpired = false) {
    sessionStorage.removeItem(tokenStorageKey);
    setToken(null);
    setUser(null);
    setSessionExpired(markExpired);
  }

  useEffect(() => {
    configureApiClient({
      getToken: () => sessionStorage.getItem(tokenStorageKey),
      onUnauthorized: () => clearSession(true)
    });
  }, []);

  useEffect(() => {
    if (!token) {
      setIsInitializing(false);
      return;
    }

    setIsInitializing(true);
    fetchCurrentUser()
      .then((currentUser) => {
        setUser(currentUser);
        setSessionExpired(false);
      })
      .catch(() => {
        clearSession(true);
      })
      .finally(() => setIsInitializing(false));
  }, [token]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      isAuthenticated: Boolean(token && user),
      isInitializing,
      sessionExpired,
      login: async (username: string, password: string) => {
        const response = await loginRequest({ username, password });
        sessionStorage.setItem(tokenStorageKey, response.access_token);
        setToken(response.access_token);
        setUser({ username });
        setSessionExpired(false);
      },
      logout: () => clearSession(false),
      clearSessionExpired: () => setSessionExpired(false)
    }),
    [isInitializing, sessionExpired, token, user]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return value;
}
