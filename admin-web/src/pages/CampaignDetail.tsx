import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  ColumnChart,
  DefinitionList,
  Dialog,
  EmptyRow,
  ErrorBanner,
  LoadMore,
  PageError,
  PageHeader,
  PageLoader,
  Progress,
  Stat,
  StatusBadge,
  Table,
  TD,
  TH,
  THead,
  TR,
  errorMessage,
  fmt,
  fmtDate,
  fmtRate,
  fmtRelative,
  useAction,
  useAuth,
  useConfirm,
  usePaged,
  useToast,
  type Page,
  useLiveQuery,
} from "@omnisend/web-shared";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, Pause, Pencil, Play, Upload, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "../api";
import type { Campaign, CampaignStats, Job } from "../types";
import { CampaignFields, campaignBody } from "./campaignForm";

interface Report {
  by_status: Record<string, number>;
  top_errors: { code: string; count: number }[];
  hourly: { hour: string; sent: number; failed: number; bounced: number; deferred: number }[];
}

const RUNNING = ["QUEUED", "PROCESSING", "PAUSED"];

export function CampaignDetailPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const confirm = useConfirm();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState(false);
  const [starting, setStarting] = useState(false);

  const q = useQuery({ queryKey: ["campaigns", id], queryFn: () => api.get<Campaign>(`/admin/campaigns/${id}`) });
  const running = !!q.data && RUNNING.includes(q.data.status);
  const stats = useLiveQuery({
    api,
    queryKey: ["campaigns", id, "stats"],
    queryFn: () => api.get<CampaignStats>(`/admin/campaigns/${id}/stats`),
    streamPath: running ? `/admin/campaigns/${id}/stream` : null,
    pollMs: running ? 2000 : false,
    enabled: !!q.data,
  });
  const report = useQuery({ queryKey: ["campaigns", id, "report"], queryFn: () => api.get<Report>(`/admin/campaigns/${id}/report`), refetchInterval: running ? 10_000 : false, enabled: !!q.data });
  const jobs = usePaged<Job>(["campaigns", id, "jobs"], (cursor) => api.get<Page<Job>>(`/admin/campaigns/${id}/jobs`, { cursor }), { refetchInterval: running ? 5000 : undefined });
  const qc = useQueryClient();
  const statsStatus = stats.data?.status;
  useEffect(() => {
    if (statsStatus && q.data && statsStatus !== q.data.status) void qc.invalidateQueries({ queryKey: ["campaigns", id] });
  }, [statsStatus, q.data, qc, id]);
  const inv = { invalidate: [["campaigns"]] };
  const act = useAction((action: "pause" | "resume" | "cancel") => api.post<Campaign>(`/admin/campaigns/${id}/${action}`), { ...inv, success: (c) => `Campaign is ${c.status.toLowerCase()}` });
  const upload = useAction((file: File) => api.upload<{ imported: number; invalid: number; duplicates: number }>(`/admin/campaigns/${id}/recipients`, file), {
    ...inv,
    success: (r) => `Imported ${fmt(r.imported)} recipients (${fmt(r.invalid)} invalid, ${fmt(r.duplicates)} duplicates)`,
  });

  if (q.isLoading) return <PageLoader />;
  if (q.error || !q.data) return <PageError error={q.error} />;
  const c = q.data;
  const s = stats.data;
  const editable = ["DRAFT", "READY"].includes(c.status);
  const canWrite = can("campaigns.write");

  return (
    <>
      <PageHeader
        back={
          <Link to="/campaigns" className="inline-flex items-center gap-1 text-sm text-fg-secondary hover:text-fg">
            <ArrowLeft className="size-4" /> Campaigns
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            {c.name} <StatusBadge status={c.status} />
          </span>
        }
        description={`Owner ${c.username} · Provider ${c.provider_name ?? "not set"} · Created ${fmtDate(c.created_at)}`}
        actions={
          <>
            {editable && canWrite && (
              <>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".csv,.txt,text/csv"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) upload.mutate(f);
                    e.target.value = "";
                  }}
                />
                <Button variant="secondary" loading={upload.isPending} onClick={() => fileRef.current?.click()}>
                  <Upload /> Upload recipients
                </Button>
                <Button variant="secondary" onClick={() => setEditing(true)}>
                  <Pencil /> Edit
                </Button>
              </>
            )}
            {c.status === "READY" && can("campaigns.start") && (
              <Button onClick={() => setStarting(true)}>
                <Play /> Start
              </Button>
            )}
            {["QUEUED", "PROCESSING"].includes(c.status) && can("campaigns.stop") && (
              <Button variant="secondary" loading={act.isPending} onClick={() => act.mutate("pause")}>
                <Pause /> Pause
              </Button>
            )}
            {c.status === "PAUSED" && can("campaigns.start") && (
              <Button loading={act.isPending} onClick={() => act.mutate("resume")}>
                <Play /> Resume
              </Button>
            )}
            {[...RUNNING, "DRAFT", "READY"].includes(c.status) && can("campaigns.stop") && (
              <Button
                variant="danger-ghost"
                onClick={async () => {
                  if (await confirm({ title: "Cancel campaign?", message: "Unsent recipients are cancelled. This cannot be undone.", confirmLabel: "Cancel campaign", danger: true }))
                    act.mutate("cancel");
                }}
              >
                <XCircle /> Cancel
              </Button>
            )}
            <Button
              variant="ghost"
              onClick={() => api.download(`/admin/campaigns/${id}/report.csv`, `${c.name}.csv`).catch((e) => toast.error(errorMessage(e)))}
            >
              <Download /> CSV
            </Button>
          </>
        }
      />

      {c.missing && c.missing.length > 0 && (
        <div className="mb-6 rounded-lg border border-border bg-warning-soft px-4 py-3 text-sm text-warning">
          To start this campaign, add: {c.missing.join(", ")}.
        </div>
      )}
      {c.last_error && <div className="mb-6"><ErrorBanner message={c.last_error} /></div>}

      <Card className="mb-6">
        <CardBody>
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-sm text-fg-secondary">Progress</span>
            <span className="text-2xl font-semibold tabular">{s?.percent ?? c.progress?.percent ?? 0}%</span>
          </div>
          <Progress value={s?.percent ?? c.progress?.percent ?? 0} label="Campaign progress" />
          <div className="mt-2 flex flex-wrap justify-between gap-2 text-xs text-fg-muted">
            <span>{fmt(s?.remaining ?? c.progress?.remaining)} remaining</span>
            <span>Speed {running ? fmtRate(s?.speed ?? 0) : "—"}</span>
          </div>
        </CardBody>
      </Card>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-6">
        <Stat label="Recipients" value={fmt(c.total_recipients)} />
        <Stat label="Sent" value={fmt(s?.sent ?? c.sent)} />
        <Stat label="Delivered" value={fmt(s?.delivered ?? c.delivered)} />
        <Stat label="Failed" value={fmt((s?.failed ?? c.failed) + (s?.bounced ?? c.bounced))} hint={`${fmt(s?.bounced ?? c.bounced)} bounced`} />
        <Stat label="Suppressed" value={fmt(s?.skipped_suppressed ?? c.skipped_suppressed)} />
        <Stat label="Complaints" value={fmt(s?.complained ?? c.complained)} hint={`${fmt(s?.unsubscribed ?? c.unsubscribed)} unsubscribed`} />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Sending activity" description="Hourly, last 7 days" />
          <CardBody>
            <ColumnChart
              title="Hourly messages sent and failed for this campaign"
              data={(report.data?.hourly ?? []).map((h) => ({ hour: h.hour, sent: h.sent, failed: h.failed + h.bounced, deferred: h.deferred }))}
              xKey="hour"
              series={[
                { key: "sent", label: "Sent" },
                { key: "failed", label: "Failed / bounced" },
                { key: "deferred", label: "Deferred (retried)" },
              ]}
              formatX={(v) => new Date(v).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit" })}
              height={220}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Details" />
          <CardBody className="space-y-5">
            <DefinitionList
              columns={1}
              items={[
                ["Subject", c.subject || "—"],
                ["From", c.from_email ? `${c.from_name ?? ""} <${c.from_email}>` : "—"],
                ["Batch size", fmt(c.batch_size)],
                ["Invalid / duplicate rows", `${fmt(c.invalid_recipients)} / ${fmt(c.duplicate_recipients)}`],
                ["Started", fmtDate(c.started_at)],
                ["Completed", fmtDate(c.completed_at)],
              ]}
            />
            {report.data && report.data.top_errors.length > 0 && (
              <div>
                <h3 className="mb-2 text-sm font-medium">Top errors</h3>
                <ul className="space-y-1 text-sm">
                  {report.data.top_errors.map((e) => (
                    <li key={e.code} className="flex justify-between gap-2">
                      <code className="truncate text-xs text-fg-secondary">{e.code}</code>
                      <span className="tabular">{fmt(e.count)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader title="Jobs" description={s ? Object.entries(s.jobs).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(" · ") || "No jobs yet" : undefined} />
        <Table>
          <THead>
            <tr>
              <TH>Job</TH>
              <TH>Status</TH>
              <TH className="text-right">Batch</TH>
              <TH className="text-right">Attempts</TH>
              <TH>Available</TH>
              <TH>Last error</TH>
            </tr>
          </THead>
          <tbody>
            {jobs.items.length === 0 && <EmptyRow colSpan={6}>Jobs are created when the campaign starts.</EmptyRow>}
            {jobs.items.map((j) => (
              <TR key={j.id}>
                <TD className="font-mono text-xs">{j.id.slice(0, 8)}</TD>
                <TD>
                  <StatusBadge status={j.status} />
                </TD>
                <TD className="text-right tabular">{fmt(j.batch_size)}</TD>
                <TD className="text-right tabular">
                  {j.attempts} / {j.max_attempts}
                </TD>
                <TD className="text-fg-secondary">{fmtRelative(j.available_at)}</TD>
                <TD className="max-w-xs truncate text-xs text-fg-muted" title={j.last_error ?? ""}>
                  {j.last_error ?? "—"}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
        <LoadMore hasNext={!!jobs.hasNextPage} loading={jobs.isFetchingNextPage} onClick={() => jobs.fetchNextPage()} />
      </Card>

      {editing && <EditCampaignDialog campaign={c} onClose={() => setEditing(false)} />}
      {starting && <StartDialog campaign={c} onClose={() => setStarting(false)} />}
    </>
  );
}

function EditCampaignDialog({ campaign, onClose }: { campaign: Campaign; onClose: () => void }) {
  const save = useAction((body: unknown) => api.patch<Campaign>(`/admin/campaigns/${campaign.id}`, body), { invalidate: [["campaigns"]], success: "Campaign saved", onSuccess: onClose });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Edit campaign"
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="edit-campaign" loading={save.isPending}>
            Save
          </Button>
        </>
      }
    >
      <form
        id="edit-campaign"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(campaignBody(new FormData(e.currentTarget)));
        }}
      >
        <CampaignFields userId={campaign.user_id} campaign={campaign} />
      </form>
    </Dialog>
  );
}

function StartDialog({ campaign, onClose }: { campaign: Campaign; onClose: () => void }) {
  const [consent, setConsent] = useState(false);
  const start = useAction(() => api.post<Campaign>(`/admin/campaigns/${campaign.id}/start`, { consent_confirmed: consent }), {
    invalidate: [["campaigns"]],
    success: "Campaign queued",
    onSuccess: onClose,
  });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Start campaign"
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => start.mutate(undefined)} disabled={!consent} loading={start.isPending}>
            <Play /> Start sending
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <p className="text-fg-secondary">
          <strong className="text-fg">{fmt(campaign.total_recipients)}</strong> recipients will be queued via{" "}
          <strong className="text-fg">{campaign.provider_name}</strong>. Suppressed addresses are skipped automatically.
        </p>
        <Badge tone="info">Batch size {fmt(campaign.batch_size)}</Badge>
        <Checkbox checked={consent} onChange={(e) => setConsent(e.target.checked)} label="I confirm every recipient opted in to receive this email." />
      </div>
    </Dialog>
  );
}
