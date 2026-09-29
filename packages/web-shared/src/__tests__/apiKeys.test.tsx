import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiKeyTable, apiKeyBody, type ApiKey } from "../ui/apiKeys";

const base: ApiKey = {
  id: "k1",
  user_id: "u1",
  username: "alice",
  name: "CRM sync",
  prefix: "ab12cd34",
  scopes: ["campaigns.read"],
  created_by: null,
  created_at: "2026-09-29T10:00:00Z",
  expires_at: null,
  revoked_at: null,
  last_used_at: null,
  last_used_ip: null,
  active: true,
};

describe("API keys", () => {
  it("builds the create payload from the form", () => {
    const f = new FormData();
    f.append("name", "  CRM sync ");
    f.append("scopes", "campaigns.read");
    f.append("scopes", "reports.read");
    f.append("expires_in_days", "");
    expect(apiKeyBody(f)).toEqual({ name: "CRM sync", scopes: ["campaigns.read", "reports.read"], expires_in_days: null });
    f.set("expires_in_days", "90");
    expect(apiKeyBody(f).expires_in_days).toBe(90);
  });

  it("shows only the prefix, the state in words, and revokes active keys only", () => {
    const onRevoke = vi.fn();
    render(<ApiKeyTable keys={[base, { ...base, id: "k2", name: "Old", revoked_at: "2026-09-29T11:00:00Z", active: false }]} showOwner onRevoke={onRevoke} />);
    expect(screen.getAllByText("osk_ab12cd34_…")).toHaveLength(2);
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.getByText("Revoked")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Revoke Old" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Revoke CRM sync" }));
    expect(onRevoke).toHaveBeenCalledWith(base);
  });
});
