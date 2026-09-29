/**
 * API client (design DS-09 / ADR-005).
 * - Access token lives in memory only (never localStorage).
 * - Refresh token is an httpOnly cookie; refresh is single-flight and sends X-Requested-With (CSRF guard).
 * - Errors surface the backend's {"error": {"code", "message", "details"}} shape.
 */

export type AppName = "admin" | "user";

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export interface ApiClient {
  get<T>(path: string, query?: Query): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  put<T>(path: string, body?: unknown): Promise<T>;
  patch<T>(path: string, body?: unknown): Promise<T>;
  del<T>(path: string): Promise<T>;
  upload<T>(path: string, file: File, query?: Query): Promise<T>;
  download(path: string, filename: string): Promise<void>;
  setToken(token: string | null): void;
  getToken(): string | null;
  refresh(): Promise<RefreshResult | null>;
  onUnauthorized(fn: () => void): void;
}

export interface RefreshResult {
  access_token: string;
  expires_in: number;
  user: Me;
}

export interface Me {
  id: string;
  email: string;
  username: string;
  full_name: string | null;
  role: string;
  permissions: string[];
  totp_enabled: boolean;
  last_login_at: string | null;
}

const BASE = "/api/v1";

function qs(query?: Query): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

async function parseError(res: Response): Promise<ApiError> {
  let code = "http_error";
  let message = res.statusText || "Request failed";
  let details: unknown;
  try {
    const body = await res.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
    }
  } catch {
    /* non-JSON */
  }
  if (res.status === 0 || res.status >= 502) message = "The server is unavailable. Please try again.";
  return new ApiError(res.status, code, message, details);
}

export function createApi(app: AppName): ApiClient {
  let token: string | null = null;
  let refreshing: Promise<RefreshResult | null> | null = null;
  let unauthorized: () => void = () => {};

  async function doRefresh(): Promise<RefreshResult | null> {
    try {
      const res = await fetch(`${BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
        body: JSON.stringify({ app }),
      });
      if (!res.ok) return null;
      const data = (await res.json()) as RefreshResult;
      token = data.access_token;
      return data;
    } catch {
      return null;
    }
  }

  function refresh(): Promise<RefreshResult | null> {
    if (!refreshing) {
      refreshing = doRefresh().finally(() => {
        refreshing = null;
      });
    }
    return refreshing;
  }

  async function request<T>(method: string, path: string, init: RequestInit = {}, retry = true): Promise<T> {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    headers.set("X-Requested-With", "XMLHttpRequest");
    let res: Response;
    try {
      res = await fetch(`${BASE}${path}`, { ...init, method, headers, credentials: "include" });
    } catch {
      throw new ApiError(0, "network_error", "Cannot reach the server. Check your connection.");
    }
    if (res.status === 401 && retry && !path.startsWith("/auth/")) {
      const refreshed = await refresh();
      if (refreshed) return request<T>(method, path, init, false);
      token = null;
      unauthorized();
    }
    if (!res.ok) throw await parseError(res);
    if (res.status === 204) return undefined as T;
    const type = res.headers.get("content-type") ?? "";
    return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
  }

  const json = (body: unknown): RequestInit => ({
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  return {
    get: (path, query) => request("GET", path + qs(query)),
    post: (path, body) => request("POST", path, json(body ?? {})),
    put: (path, body) => request("PUT", path, json(body ?? {})),
    patch: (path, body) => request("PATCH", path, json(body ?? {})),
    del: (path) => request("DELETE", path),
    upload: (path, file, query) => {
      const form = new FormData();
      form.append("file", file);
      return request("POST", path + qs(query), { body: form });
    },
    async download(path, filename) {
      const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
      let res = await fetch(`${BASE}${path}`, { headers, credentials: "include" });
      if (res.status === 401 && (await refresh())) {
        res = await fetch(`${BASE}${path}`, { headers: { Authorization: `Bearer ${token}` }, credentials: "include" });
      }
      if (!res.ok) throw await parseError(res);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    setToken: (t) => {
      token = t;
    },
    getToken: () => token,
    refresh,
    onUnauthorized: (fn) => {
      unauthorized = fn;
    },
  };
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "validation_error" && Array.isArray(err.details) && err.details.length) {
      const first = err.details[0] as { loc?: unknown[]; msg?: string };
      const field = (first.loc ?? []).filter((p) => p !== "body").join(".");
      return field ? `${field}: ${first.msg}` : (first.msg ?? err.message);
    }
    return err.message;
  }
  return err instanceof Error ? err.message : "Something went wrong";
}
