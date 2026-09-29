import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { ApiClient, AppName, Me, RefreshResult } from "./api";

export type LoginStep =
  | { kind: "done" }
  | { kind: "mfa"; mfaToken: string }
  | { kind: "mfa_setup"; mfaToken: string; otpauthUri: string; secret: string };

interface LoginResponse {
  status: "ok" | "mfa_required" | "mfa_setup_required";
  access_token?: string;
  expires_in?: number;
  user?: Me;
  mfa_token?: string;
  otpauth_uri?: string;
  totp_secret?: string;
}

interface AuthContextValue {
  status: "loading" | "anonymous" | "authenticated";
  user: Me | null;
  api: ApiClient;
  login(login: string, password: string): Promise<LoginStep>;
  verifyMfa(mfaToken: string, code: string): Promise<void>;
  logout(): Promise<void>;
  can(permission: string): boolean;
  setUser(user: Me): void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ api, app, children }: { api: ApiClient; app: AppName; children: ReactNode }) {
  const [status, setStatus] = useState<AuthContextValue["status"]>("loading");
  const [user, setUser] = useState<Me | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const schedule = useCallback(
    (expiresIn: number) => {
      clearTimeout(timer.current);
      // Refresh a minute before the access token expires so requests never see a 401 in normal use.
      timer.current = setTimeout(
        async () => {
          const r = await api.refresh();
          if (r) {
            setUser(r.user);
            schedule(r.expires_in);
          }
        },
        Math.max(10, expiresIn - 60) * 1000,
      );
    },
    [api],
  );

  const accept = useCallback(
    (r: RefreshResult) => {
      api.setToken(r.access_token);
      setUser(r.user);
      setStatus("authenticated");
      schedule(r.expires_in);
    },
    [api, schedule],
  );

  useEffect(() => {
    api.onUnauthorized(() => {
      clearTimeout(timer.current);
      setUser(null);
      setStatus("anonymous");
    });
    let cancelled = false;
    api.refresh().then((r) => {
      if (cancelled) return;
      if (r) accept(r);
      else setStatus("anonymous");
    });
    return () => {
      cancelled = true;
      clearTimeout(timer.current);
    };
  }, [api, accept]);

  const login = useCallback(
    async (loginName: string, password: string): Promise<LoginStep> => {
      const r = await api.post<LoginResponse>("/auth/login", { login: loginName, password, app });
      if (r.status === "ok" && r.access_token && r.user && r.expires_in) {
        accept({ access_token: r.access_token, expires_in: r.expires_in, user: r.user });
        return { kind: "done" };
      }
      if (r.status === "mfa_setup_required") {
        return { kind: "mfa_setup", mfaToken: r.mfa_token!, otpauthUri: r.otpauth_uri!, secret: r.totp_secret! };
      }
      return { kind: "mfa", mfaToken: r.mfa_token! };
    },
    [api, app, accept],
  );

  const verifyMfa = useCallback(
    async (mfaToken: string, code: string) => {
      const r = await api.post<RefreshResult>("/auth/mfa/verify", { mfa_token: mfaToken, code });
      accept(r);
    },
    [api, accept],
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout", { app });
    } finally {
      clearTimeout(timer.current);
      api.setToken(null);
      setUser(null);
      setStatus("anonymous");
    }
  }, [api, app]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      api,
      login,
      verifyMfa,
      logout,
      can: (p: string) => !!user?.permissions.includes(p),
      setUser,
    }),
    [status, user, api, login, verifyMfa, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
