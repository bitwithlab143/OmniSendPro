import {
  Badge,
  Button,
  Checkbox,
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
import { ArrowLeft, CheckCircle2, Inbox, KeyRound, Pencil, Plug, Power, PowerOff, RefreshCw, Trash2, Webhook, XCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";

import { api } from "../api";
import type { Assignment, BounceMailbox, Provider, User } from "../types";
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

interface WebhookSecret {
  webhook_secret: string;
  endpoint: string;
  inbound_endpoint: string;
  note: string;
}

interface PollResult {
  fetched: number;
  accepted: number;
  duplicates: number;
  unmatched: number;
  unrecognised: number;
  error: string | null;
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
  const [webhook, setWebhook] = useState<WebhookSecret | null>(null);
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
    success: (r) => `Connection OK${r.latency_ms ? ` (${r.latency_ms} ms)` : ""}`,
    failed: (r) => (r.ok ? null : `Test failed: ${r.message}`),
  });
  const toggle = useAction((action: "enable" | "disable") => api.post<Provider>(`/admin/providers/${id}/${action}`), { ...inv, success: (p) => `Provider is now ${p.status}` });
  const rotateWebhook = useAction(() => api.post<WebhookSecret>(`/admin/providers/${id}/webhook-secret`), { invalidate: [["providers"]], onSuccess: setWebhook });
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
                ["Connections per worker", p.max_connections ? fmt(p.max_connections) : "Worker default"],
                ["Usage", p.usage ? `${fmt(p.usage.hour)} this hour · ${fmt(p.usage.day)} today` : "—"],
                ["Last test", p.last_tested_at ? `${p.last_test_ok ? "Passed" : "Failed"} ${fmtRelative(p.last_tested_at)} — ${p.last_test_message ?? ""}` : "Never"],
                ["Delivery webhook", p.webhook_enabled ? <Badge tone="success" icon={<Webhook />}>Enabled</Badge> : <Badge>Not configured</Badge>],
                ["Delivered status", p.reports_delivery ? "Confirmed by provider events" : "SMTP acceptance counts as delivered"],
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

      <BounceCard provider={p} writable={writable} />

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
            <Field label="Delivery events (JSON)">{() => <CopyField value={`${window.location.origin}${webhook.endpoint}`} />}</Field>
            <Field label="Raw bounce / complaint emails">{() => <CopyField value={`${window.location.origin}${webhook.inbound_endpoint}`} />}</Field>
            <Field label="Signing secret">{() => <CopyField value={webhook.webhook_secret} />}</Field>
            <p className="text-fg-secondary">{webhook.note}</p>
          </div>
        )}
      </Dialog>
    </>
  );
}

function BounceCard({ provider: p, writable }: { provider: Provider; writable: boolean }) {
  const confirm = useConfirm();
  const [editing, setEditing] = useState(false);
  const base = `/admin/providers/${p.id}/bounce-mailbox`;
  const test = useAction(() => api.post<{ ok: boolean; message: string }>(`${base}/test`), {
    success: (r) => r.message,
    failed: (r) => (r.ok ? null : `Test failed: ${r.message}`),
  });
  const poll = useAction(() => api.post<PollResult>(`${base}/poll`), {
    invalidate: [["providers"]],
    success: (r) => `Fetched ${r.fetched} message(s): ${r.accepted} applied, ${r.duplicates} duplicate, ${r.unmatched} unmatched, ${r.unrecognised} left unread`,
    failed: (r) => (r.error ? `Polling failed: ${r.error}` : null),
  });
  const remove = useAction(() => api.del<Provider>(base), { invalidate: [["providers"]], success: "Bounce mailbox removed" });
  const mb = p.bounce_mailbox;
  return (
    <Card className="mt-6">
      <CardHeader
        title="Bounces & complaints"
        description="Asynchronous bounce reports (DSN) and feedback-loop complaints (ARF) are read from this mailbox and suppress the address automatically."
        actions={
          writable && (
            <Button size="sm" variant={mb ? "secondary" : "primary"} onClick={() => setEditing(true)}>
              <Inbox /> {mb ? "Edit mailbox" : "Connect mailbox"}
            </Button>
          )
        }
      />
      <CardBody>
        {mb ? (
          <>
            <DefinitionList
              items={[
                ["Mailbox", `${mb.username} @ ${mb.host}:${mb.port} (${mb.ssl ? "TLS" : "STARTTLS"})`],
                ["Folder", `${mb.folder}${mb.delete_processed ? " · processed reports are deleted" : " · processed reports are marked read"}`],
                ["Last polled", p.bounce_last_polled_at ? fmtRelative(p.bounce_last_polled_at) : "Not yet"],
                ["Reports applied", fmt(p.bounce_processed_total)],
                ...(p.bounce_last_error ? ([["Last error", <span className="text-danger">{p.bounce_last_error}</span>]] as [string, ReactNode][]) : []),
              ]}
            />
            {writable && (
              <div className="mt-5 flex flex-wrap gap-2 border-t border-border pt-4">
                <Button variant="secondary" size="sm" loading={test.isPending} onClick={() => test.mutate(undefined)}>
                  <Plug /> Test connection
                </Button>
                <Button variant="secondary" size="sm" loading={poll.isPending} onClick={() => poll.mutate(undefined)}>
                  <RefreshCw /> Poll now
                </Button>
                <Button
                  variant="danger-ghost"
                  size="sm"
                  onClick={async () => {
                    if (await confirm({ title: "Remove bounce mailbox?", message: "Bounces and complaints sent only by email will no longer be processed for this provider.", confirmLabel: "Remove", danger: true }))
                      remove.mutate(undefined);
                  }}
                >
                  <Trash2 /> Remove
                </Button>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-fg-secondary">
            {p.webhook_enabled
              ? "No mailbox connected. Reports can still be delivered to the signed inbound endpoint shown when the webhook secret is rotated."
              : "No mailbox connected. Connect the mailbox that receives this provider's bounces (Return-Path) and feedback-loop reports, or enable the webhook to post raw reports."}
          </p>
        )}
      </CardBody>
      {editing && <MailboxDialog id={p.id} mailbox={mb} onClose={() => setEditing(false)} />}
    </Card>
  );
}

function MailboxDialog({ id, mailbox, onClose }: { id: string; mailbox: BounceMailbox | null; onClose: () => void }) {
  const save = useAction((body: unknown) => api.put<Provider>(`/admin/providers/${id}/bounce-mailbox`, body), {
    invalidate: [["providers"]],
    success: "Bounce mailbox saved",
    onSuccess: onClose,
  });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Bounce mailbox (IMAP)"
      size="lg"
      description="Use a dedicated mailbox. Only delivery reports and feedback-loop reports are processed; anything else is left unread."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="bounce-mailbox" loading={save.isPending}>
            Save
          </Button>
        </>
      }
    >
      <form
        id="bounce-mailbox"
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          save.mutate({
            host: String(f.get("host")).trim(),
            port: Number(f.get("port")),
            ssl: f.get("ssl") === "on",
            username: String(f.get("username")).trim(),
            password: String(f.get("password") ?? "") || null,
            folder: String(f.get("folder") ?? "").trim() || "INBOX",
            delete_processed: f.get("delete_processed") === "on",
          });
        }}
      >
        <Field label="IMAP host">{(fid) => <Input id={fid} name="host" defaultValue={mailbox?.host} placeholder="imap.example.com" required />}</Field>
        <Field label="Port">{(fid) => <Input id={fid} name="port" type="number" min={1} max={65535} defaultValue={mailbox?.port ?? 993} required />}</Field>
        <Field label="Username">{(fid) => <Input id={fid} name="username" defaultValue={mailbox?.username} autoComplete="off" required />}</Field>
        <Field label="Password" optional={!!mailbox} hint={mailbox ? "Leave empty to keep the stored password." : "Encrypted at rest; never shown again."}>
          {(fid, d) => <Input id={fid} name="password" type="password" autoComplete="new-password" required={!mailbox} aria-describedby={d} />}
        </Field>
        <Field label="Folder">{(fid) => <Input id={fid} name="folder" defaultValue={mailbox?.folder ?? "INBOX"} />}</Field>
        <div className="flex flex-col justify-end gap-3 pb-1">
          <Checkbox name="ssl" defaultChecked={mailbox?.ssl ?? true} label="Implicit TLS (port 993); otherwise STARTTLS" />
          <Checkbox name="delete_processed" defaultChecked={mailbox?.delete_processed ?? false} label="Delete reports after processing" />
        </div>
      </form>
    </Dialog>
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
