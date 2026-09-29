import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, createApi, errorMessage } from "../lib/api";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

describe("createApi", () => {
  it("sends the bearer token and CSRF header", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { ok: true }));
    const api = createApi("admin");
    api.setToken("abc");
    await api.get("/admin/users", { limit: 5, q: "", cursor: undefined });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/admin/users?limit=5");
    const headers = new Headers((init as RequestInit).headers);
    expect(headers.get("Authorization")).toBe("Bearer abc");
    expect(headers.get("X-Requested-With")).toBe("XMLHttpRequest");
  });

  it("refreshes once on 401 and retries the request", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(401, { error: { code: "invalid_token", message: "expired" } }))
      .mockResolvedValueOnce(json(200, { access_token: "new", expires_in: 900, user: {} }))
      .mockResolvedValueOnce(json(200, { items: [] }));
    const api = createApi("user");
    api.setToken("old");
    const result = await api.get<{ items: unknown[] }>("/user/campaigns");
    expect(result.items).toEqual([]);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/refresh");
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({ app: "user" });
    expect(new Headers((fetchMock.mock.calls[2][1] as RequestInit).headers).get("Authorization")).toBe("Bearer new");
  });

  it("calls onUnauthorized when refresh fails", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(401, { error: { code: "invalid_token", message: "expired" } }))
      .mockResolvedValueOnce(json(401, { error: { code: "invalid_refresh", message: "no" } }));
    const api = createApi("admin");
    const onUnauth = vi.fn();
    api.onUnauthorized(onUnauth);
    await expect(api.get("/admin/users")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauth).toHaveBeenCalledOnce();
    expect(api.getToken()).toBeNull();
  });

  it("deduplicates concurrent refreshes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(200, { access_token: "t", expires_in: 900, user: {} }));
    const api = createApi("admin");
    await Promise.all([api.refresh(), api.refresh(), api.refresh()]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("maps backend error envelopes", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      json(422, { error: { code: "validation_error", message: "Request validation failed", details: [{ loc: ["body", "email"], msg: "value is not a valid email" }] } }),
    );
    const api = createApi("admin");
    const err = (await api.post("/admin/users", {}).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(errorMessage(err)).toBe("email: value is not a valid email");
  });
});
