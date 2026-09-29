import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ColumnChart,
  CopyField,
  DefinitionList,
  Dialog,
  EmptyRow,
  ErrorBanner,
  Field,
  Input,
  PageError,
  PageHeader,
  PageLoader,
  Select,
  StatusBadge,
  Table,
  TD,
  TH,
  THead,
  TR,
  errorMessage,
  fmt,
  fmtDate,
  fmtRelative,
  useAction,
  useAuth,
  useConfirm,
  type Page,
} from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, KeyRound, Pencil, Plug, Power, PowerOff, Trash2, Webhook, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "../api";
import type { Assignment, Provider, User } from "../types";
import { ProviderFields, providerBody } from "./providerForm";

interface HealthLog {
  window_start: string;
  attempts: number;
  successes: number;
  deferrals: number;
  bounces: number;
  auth_failures: number;
  connection_failures: number;
  timeouts: number;
  complaints: number;
  health_score: number;
  status: string;
  created_at: string;
}

interface DnsResult {
  domain: string;
  spf: string | null;
  dmarc: string | null;
  dkim: string | null;
  dkim_selector: string | null;
  ok: boolean;
  warnings: string[];
}

export function ProviderDetailPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const confirm = useConfirm();
  const writable = can("providers.write");
  const [editing, setEditing] = useState(false);
  const [secretOpen, setSecretOpen] = useState(false);
  const [webhook, setWebhook] = useState<{ webhook_secret: string; endpoint: string; note: string } | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);
  const [dns, setDns] = useState<DnsResult | null>(null);

  const q = useQuery({ queryKey: ["providers", id], queryFn: () => api.get<Provider>(`/admin/providers/${id}`), refetchInterval: 15_000 });
  const health = useQuery({ queryKey: ["providers", id, "health"], queryFn: () => api.get<HealthLog[]>(`/admin/providers/${id}/health`, { limit: 60 }), refetchInterval: 60_000 });
  const assignments = useQuery({
    queryKey: ["assignments", { provider: id }],
    queryFn: () => api.get<Page<Assignment>>("/admin/assignments", { provider_id: id, limit: 200 }),
  });
  const inv = { invalidate: [["providers"], ["dashboard"]] };
  const test = useAction(() => api.post<{ ok: boolean; message: string; latency_ms: number | null }>(`/admin/providers/${id}/test`), {
    invalidate: [["providers"]],
    success: (r) => (r.ok ? `Connection OK${r.latency_ms ? ` (${r.latency_ms} ms)` : ""}` : `Test failed: ${r.message}`),
  });
  const toggle = useAction((action: "enable" | "disable") => api.post<Provider>(`/admin/providers/${id}/${action}`), { ...inv, success: (p) => `Provider is now ${p.status}` });
  const rotateWebhook = useAction(() => api.post<{ webhook_secret: string; endpoint: string; note: string }>(`/admin/providers/${id}/webhook-secret`), { invalidate: [["providers"]], onSuccess: setWebhook });
  const disableWebhook = useAction(() => api.del<Provider>(`/admin/providers/${id}/webhook-secret`), { invalidate: [["providers"]], success: "Webhook disabled" });
  const checkDns = useAction((selector: string) => api.get<DnsResult>(`/admin/providers/${id}/dns`, { selector }), { onSuccess: setDns });
  const unassign = useAction((aid: string) => api.del(`/admin/assignments/${aid}`), { invalidate: [["assignments"], ["providers"]], success: "Assignment revoked" });

  if (q.isLoading) return <PageLoader />;
  if (q.error || !q.data) return <PageError error={q.error} />;
  const p = q.data;

  return (
    <>
      <PageHeader
        back={
          <Link to="/providers" className="inline-flex items-center gap-1 text-sm text-fg-secondary hover:text-fg">
            <ArrowLeft className="size-4" /> Providers
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            {p.provider_name} <StatusBadge status={p.status} />
          </span>
        }
        description={p.status_reason ?? `${p.host}:${p.port}`}
        actions={
          writable && (
            <>
              <Button variant="secondary" loading={test.isPending} onClick={() => test.mutate(undefined)}>
                <Plug /> Test connection
              </Button>
              <Button variant="secondary" onClick={() => setEditing(true)}>
                <Pencil /> Edit
              </Button>
              {p.status === "DISABLED" ? (
                <Button onClick={() => toggle.mutate("enable")} loading={toggle.isPending}>
                  <Power /> Enable
                </Button>
              ) : (
                <Button
                  variant="danger"
                  loading={toggle.isPending}
                  onClick={async () => {
                    if (await confirm({ title: "Disable provider?", message: "Workers stop claiming new jobs for this provider and in-flight jobs are asked to stop.", confirmLabel: "Disable", danger: true }))
                      toggle.mutate("disable");
                  }}
                >
                  <PowerOff /> Disable
                </Button>
              )}
            </>
          )
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Configuration" />
          <CardBody>
            <DefinitionList
              items={[
                ["Host", `${p.host}:${p.port} (${p.tls_mode.toUpperCase()})`],
                ["Username", p.username ?? "—"],
                ["From", `${p.from_name ? `${p.from_name} ` : ""}<${p.from_email}>`],
                ["Credential", p.has_secret ? <Badge tone="success" icon={<CheckCircle2 />}>Stored encrypted</Badge> : <Badge>None</Badge>],
                ["Limits", [p.per_second_limit && `${p.per_second_limit}/s`, p.hourly_limit && `${fmt(p.hourly_limit)}/hour`, p.daily_limit && `${fmt(p.daily_limit)}/day`].filter(Boolean).join(" · ") || "No limits"],
                ["Usage", p.usage ? `${fmt(p.usage.hour)} this hour · ${fmt(p.usage.day)} today` : "—"],
                ["Last test", p.last_tested_at ? `${p.last_test_ok ? "Passed" : "Failed"} ${fmtRelative(p.last_tested_at)} — ${p.last_test_message ?? ""}` : "Never"],
                ["Delivery webhook", p.webhook_enabled ? <Badge tone="success" icon={<Webhook />}>Enabled</Badge> : <Badge>Not configured</Badge>],
              ]}
            />
            {writable && (
              <div className="mt-5 flex flex-wrap gap-2 border-t border-border pt-4">
                <Button variant="secondary" size="sm" onClick={() => setSecretOpen(true)}>
                  <KeyRound /> {p.has_secret ? "Rotate credential" : "Set credential"}
                </Button>
                <Button variant="secondary" size="sm" loading={rotateWebhook.isPending} onClick={() => rotateWebhook.mutate(undefined)}>
                  <Webhook /> {p.webhook_enabled ? "Rotate webhook secret" : "Enable delivery webhook"}
                </Button>
                {p.webhook_enabled && (
                  <Button variant="danger-ghost" size="sm" onClick={() => disableWebhook.mutate(undefined)}>
                    Disable webhook
                  </Button>
                )}
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Sender authentication" description="SPF / DKIM / DMARC for the From domain" />
          <CardBody>
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                checkDns.mutate(String(new FormData(e.currentTarget).get("selector") ?? ""));
              }}
            >
              <Input name="selector" placeholder="DKIM selector (optional)" aria-label="DKIM selector" />
              <Button type="submit" variant="secondary" loading={checkDns.isPending}>
                Check
              </Button>
            </form>
            {dns && (
              <ul className="mt-4 space-y-2 text-sm">
                {(
                  [
                    ["SPF", dns.spf],
                    ["DMARC", dns.dmarc],
                    ...(dns.dkim_selector ? [["DKIM", dns.dkim] as const] : []),
                  ] as const
                ).map(([k, v]) => (
                  <li key={k} className="flex items-start gap-2">
                    {v ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <XCircle className="mt-0.5 size-4 shrink-0 text-danger" />}
                    <div className="min-w-0">
                      <div className="font-medium">{k}</div>
                      <div className="break-all text-xs text-fg-muted">{v ?? "Not found"}</div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader title="Health history" description="Rolling-window scores; providers are never disabled on a single failure." />
        <CardBody>
          <ColumnChart
            title="Provider attempts per health window"
            data={[...(health.data ?? [])].reverse().map((h) => ({ t: h.created_at, successes: h.successes, problems: h.attempts - h.successes }))}
            xKey="t"
            series={[
              { key: "successes", label: "Successful" },
              { key: "problems", label: "Deferred / failed" },
            ]}
            formatX={(v) => new Date(v).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
            height={200}
          />
        </CardBody>
      </Card>

      <Card className="mt-6">
        <CardHeader
          title="Assigned users"
          description="Only these users can send through this provider."
          actions={
            writable && (
              <Button size="sm" onClick={() => setAssignOpen(true)}>
                Assign user
              </Button>
            )
          }
        />
        <Table>
          <THead>
            <tr>
              <TH>User</TH>
              <TH>Assigned</TH>
              {writable && <TH className="text-right">Actions</TH>}
            </tr>
          </THead>
          <tbody>
            {assignments.data?.items.length === 0 && <EmptyRow colSpan={3}>Not assigned to any user yet.</EmptyRow>}
            {assignments.data?.items.map((a) => (
              <TR key={a.id}>
                <TD className="font-medium">{a.username}</TD>
                <TD className="text-fg-secondary">{fmtDate(a.assigned_at)}</TD>
                {writable && (
                  <TD className="text-right">
                    <Button
                      variant="danger-ghost"
                      size="sm"
                      onClick={async () => {
                        if (await confirm({ title: "Revoke assignment?", message: `${a.username} will no longer be able to start campaigns with this provider.`, confirmLabel: "Revoke", danger: true }))
                          unassign.mutate(a.id);
                      }}
                    >
                      <Trash2 /> Revoke
                    </Button>
                  </TD>
                )}
              </TR>
            ))}
          </tbody>
        </Table>
      </Card>

      {editing && <EditDialog provider={p} onClose={() => setEditing(false)} />}
      {secretOpen && <SecretDialog id={p.id} onClose={() => setSecretOpen(false)} />}
      {assignOpen && <AssignDialog providerId={p.id} assigned={new Set(assignments.data?.items.map((a) => a.user_id))} onClose={() => setAssignOpen(false)} />}
      <Dialog open={!!webhook} onClose={() => setWebhook(null)} title="Delivery webhook secret" description="Copy it now — it is shown only once.">
        {webhook && (
          <div className="space-y-4 text-sm">
            <Field label="Endpoint">{() => <CopyField value={`${window.location.origin}${webhook.endpoint}`} />}</Field>
            <Field label="Signing secret">{() => <CopyField value={webhook.webhook_secret} />}</Field>
            <p className="text-fg-secondary">{webhook.note}</p>
          </div>
        )}
      </Dialog>
    </>
  );
}

function EditDialog({ provider, onClose }: { provider: Provider; onClose: () => void }) {
  const save = useAction((body: unknown) => api.patch<Provider>(`/admin/providers/${provider.id}`, body), { invalidate: [["providers"]], success: "Provider updated", onSuccess: onClose });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Edit provider"
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="edit-provider" loading={save.isPending}>
            Save
          </Button>
        </>
      }
    >
      <form
        id="edit-provider"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(providerBody(new FormData(e.currentTarget), false));
        }}
      >
        <ProviderFields provider={provider} />
      </form>
    </Dialog>
  );
}

function SecretDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const save = useAction((password: string) => api.put(`/admin/providers/${id}/secret`, { password }), { invalidate: [["providers"]], success: "Credential saved", onSuccess: onClose });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Provider credential"
      size="sm"
      description="Stored with AES-256-GCM. It is never returned by the API."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="secret" loading={save.isPending}>
            Save
          </Button>
        </>
      }
    >
      <form
        id="secret"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(String(new FormData(e.currentTarget).get("password")));
        }}
      >
        <Field label="Password / API key">{(fid) => <Input id={fid} name="password" type="password" autoComplete="new-password" required />}</Field>
      </form>
    </Dialog>
  );
}

function AssignDialog({ providerId, assigned, onClose }: { providerId: string; assigned: Set<string>; onClose: () => void }) {
  const users = useQuery({ queryKey: ["users", "USER-role"], queryFn: () => api.get<Page<User>>("/admin/users", { role: "USER", status: "active", limit: 200 }) });
  const assign = useAction((userId: string) => api.post("/admin/assignments", { user_id: userId, provider_id: providerId }), { invalidate: [["assignments"], ["providers"]], success: "Provider assigned", onSuccess: onClose });
  const options = users.data?.items.filter((u) => !assigned.has(u.id)) ?? [];
  return (
    <Dialog
      open
      onClose={onClose}
      title="Assign provider to user"
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="assign" loading={assign.isPending} disabled={!options.length}>
            Assign
          </Button>
        </>
      }
    >
      {users.error && <ErrorBanner message={errorMessage(users.error)} />}
      <form
        id="assign"
        onSubmit={(e) => {
          e.preventDefault();
          assign.mutate(String(new FormData(e.currentTarget).get("user_id")));
        }}
      >
        <Field label="User">
          {(fid) => (
            <Select id={fid} name="user_id" required>
              {options.length === 0 && <option value="">No unassigned active users</option>}
              {options.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username} — {u.email}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </form>
    </Dialog>
  );
}
