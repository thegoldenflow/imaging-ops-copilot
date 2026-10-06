import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, getToken, post, setToken } from "./api";
import type { StaffUser } from "./types";

interface AuthState {
  user: StaffUser | null;
  loading: boolean;
  login: (userId: string, passcode?: string) => Promise<StaffUser>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<StaffUser | null>(null);
  const [loading, setLoading] = useState(Boolean(getToken()));
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!getToken()) return;
    api<StaffUser>("/api/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(
    async (userId: string, passcode?: string) => {
      const res = await post<{ token: string; user: StaffUser }>("/api/auth/login", { user_id: userId, passcode });
      setToken(res.token);
      queryClient.clear();
      setUser(res.user);
      return res.user;
    },
    [queryClient],
  );

  const logout = useCallback(() => {
    setToken(null);
    queryClient.clear();
    setUser(null);
  }, [queryClient]);

  return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
