import {
  ApiKeyTable,
  Button,
  Card,
  Checkbox,
  CreateApiKeyDialog,
  ErrorBanner,
  Field,
  NewApiKeyDialog,
  PageHeader,
  Select,
  apiKeyBody,
  errorMessage,
  useAction,
  useAuth,
  useConfirm,
  type ApiKey,
  type ApiKeyCreated,
  type Page,
} from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";

import { api } from "../api";
import type { User } from "../types";

export function ApiKeysPage() {
  const { can } = useAuth();
  const confirm = useConfirm();
  const writable = can("users.write");
  const [userId, setUserId] = useState("");
  const [includeRevoked, setIncludeRevoked] = useState(false);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const users = useQuery({ queryKey: ["users", "USER-role"], queryFn: () => api.get<Page<User>>("/admin/users", { role: "USER", limit: 200 }) });
  const keys = useQuery({
    queryKey: ["api-keys", userId, includeRevoked],
    queryFn: () => api.get<ApiKey[]>("/admin/api-keys", { user_id: userId || undefined, include_revoked: includeRevoked }),
  });
  const create = useAction((body: unknown) => api.post<ApiKeyCreated>("/admin/api-keys", body), {
    invalidate: [["api-keys"], ["audit"]],
    onSuccess: (k) => {
      setCreating(false);
      setCreated(k);
    },
  });
  const revoke = useAction((id: string) => api.del(`/admin/api-keys/${id}`), { invalidate: [["api-keys"]], success: "API key revoked" });
  const activeUsers = users.data?.items.filter((u) => u.status === "active") ?? [];

  return (
    <>
      <PageHeader
        title="API keys"
        description="Programmatic access to the User API on behalf of a user. Keys are limited to the user's own campaigns and to the selected scopes, and stop working while the user is suspended."
        actions={
          writable && (
            <Button onClick={() => setCreating(true)}>
              <KeyRound /> Create key
            </Button>
          )
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <Select value={userId} onChange={(e) => setUserId(e.target.value)} className="w-60" aria-label="Filter by user">
          <option value="">All users</option>
          {users.data?.items.map((u) => (
            <option key={u.id} value={u.id}>
              {u.username}
            </option>
          ))}
        </Select>
        <Checkbox checked={includeRevoked} onChange={(e) => setIncludeRevoked(e.target.checked)} label="Show revoked keys" />
      </div>
      {keys.error && <ErrorBanner message={errorMessage(keys.error)} />}
      <Card>
        <ApiKeyTable
          keys={keys.data}
          loading={keys.isLoading}
          showOwner
          onRevoke={
            writable
              ? async (k) => {
                  if (await confirm({ title: `Revoke “${k.name}”?`, message: `Integrations using ${k.username ?? "this user"}'s key stop working immediately.`, confirmLabel: "Revoke", danger: true }))
                    revoke.mutate(k.id);
                }
              : undefined
          }
        />
      </Card>
      {creating && (
        <CreateApiKeyDialog
          onClose={() => setCreating(false)}
          pending={create.isPending}
          onSubmit={(f) => create.mutate({ ...apiKeyBody(f), user_id: f.get("user_id") })}
          extra={
            <Field label="User">
              {(id) => (
                <Select id={id} name="user_id" defaultValue={userId} required>
                  <option value="" disabled>
                    {activeUsers.length ? "Select a user" : "No active users"}
                  </option>
                  {activeUsers.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.username} — {u.email}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          }
        />
      )}
      <NewApiKeyDialog created={created} onClose={() => setCreated(null)} />
    </>
  );
}
