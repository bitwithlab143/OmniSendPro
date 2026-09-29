import {
  Button,
  Card,
  CardBody,
  CardHeader,
  ColumnChart,
  EmptyRow,
  ErrorBanner,
  LoadMore,
  PageError,
  PageHeader,
  PageLoader,
  Progress,
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
  fmtRate,
  titleCase,
  useAction,
  useConfirm,
  usePaged,
  useToast,
  type Page,
} from "@omnisend/web-shared";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, Pause, Play, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Navigate, useParams } from "react-router";

import { api } from "../api";
import type { Campaign, CampaignStats, Recipient } from "../types";

const LIVE = ["QUEUED", "PROCESSING"];

interface Report {
  by_status: Record<string, number>;
  top_errors: { code: string; count: number }[];
  hourly: { hour: string; sent: number; failed: number; bounced: number; deferred: number }[];
}

export function CampaignMonitorPage() {
  const { id = "" } = useParams();
  const confirm = useConfirm();
  const toast = useToast();
  const [recipientStatus, setRecipientStatus] = useState("");
  const campaign = useQuery({ queryKey: ["campaigns", id], queryFn: () => api.get<Campaign>(`/user/campaigns/${id}`) });
  const live = !!campaign.data && LIVE.includes(campaign.data.status);
  const stats = useQuery({
    queryKey: ["campaigns", id, "stats"],
    queryFn: () => api.get<CampaignStats>(`/user/campaigns/${id}/stats`),
    refetchInterval: live ? 2000 : campaign.data?.status === "PAUSED" ? 5000 : false,
    enabled: !!campaign.data,
  });
  // Keep the campaign's own status in sync with the live stats.
  const statsStatus = stats.data?.status;
  const campaignStatus = campaign.data?.status;
  const qc = useQueryClient();
  useEffect(() => {
    // Status changed (e.g. PROCESSING → COMPLETED): refresh everything that stopped polling.
    if (statsStatus && campaignStatus && statsStatus !== campaignStatus) void qc.invalidateQueries({ queryKey: ["campaigns", id] });
  }, [statsStatus, campaignStatus, qc, id]);
  const report = useQuery({ queryKey: ["campaigns", id, "report"], queryFn: () => api.get<Report>(`/user/campaigns/${id}/report`), refetchInterval: live ? 15_000 : false, enabled: !!campaign.data });
  const recipients = usePaged<Recipient>(["campaigns", id, "recipients", recipientStatus], (cursor) =>
    api.get<Page<Recipient>>(`/user/campaigns/${id}/recipients`, { status: recipientStatus, cursor }),
  );
  const act = useAction((action: "pause" | "resume" | "cancel") => api.post<Campaign>(`/user/campaigns/${id}/${action}`), {
    invalidate: [["campaigns"], ["dashboard"]],
    success: (c) => (c.status === "CANCELLED" ? "Sending stopped" : c.status === "PAUSED" ? "Paused" : "Resumed"),
  });

  if (campaign.isLoading) return <PageLoader />;
  if (campaign.error || !campaign.data) return <PageError error={campaign.error} />;
  const c = campaign.data;
  if (["DRAFT", "READY"].includes(c.status)) return <Navigate to={`/campaigns/${c.id}/edit`} replace />;
  const s = stats.data;
  const percent = s?.percent ?? c.progress?.percent ?? 0;

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
            {c.name} <StatusBadge status={s?.status ?? c.status} />
          </span>
        }
        description={`Started ${fmtDate(c.started_at)}${c.completed_at ? ` · Finished ${fmtDate(c.completed_at)}` : ""}`}
        actions={
          <>
            {LIVE.includes(c.status) && (
              <Button variant="secondary" onClick={() => act.mutate("pause")} loading={act.isPending && act.variables === "pause"}>
                <Pause /> Pause
              </Button>
            )}
            {c.status === "PAUSED" && (
              <Button onClick={() => act.mutate("resume")} loading={act.isPending && act.variables === "resume"}>
                <Play /> Resume
              </Button>
            )}
            {[...LIVE, "PAUSED"].includes(c.status) && (
              <Button
                variant="danger-ghost"
                onClick={async () => {
                  if (await confirm({ title: "Stop sending?", message: "Messages not yet sent will be cancelled. This cannot be undone.", confirmLabel: "Stop sending", danger: true })) act.mutate("cancel");
                }}
              >
                <Square /> Stop
              </Button>
            )}
            <Button variant="ghost" onClick={() => api.download(`/user/campaigns/${id}/report.csv`, `${c.name}.csv`).catch((e) => toast.error(errorMessage(e)))}>
              <Download /> Export CSV
            </Button>
          </>
        }
      />
      {c.last_error && <div className="mb-6"><ErrorBanner message={c.last_error} /></div>}

      <Card>
        <CardBody>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-sm text-fg-secondary">Campaign progress</div>
              <div className="mt-1 text-5xl font-semibold tracking-tight">{percent}%</div>
            </div>
            <div className="text-right">
              <div className="text-sm text-fg-secondary">Speed</div>
              <div className="mt-1 text-2xl font-semibold">{live ? fmtRate(s?.speed ?? 0) : "—"}</div>
            </div>
          </div>
          <Progress value={percent} className="mt-4 h-3" label="Campaign progress" />
          <dl className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-7">
            {(
              [
                ["Processed", (s?.sent ?? 0) + (s?.failed ?? 0) + (s?.bounced ?? 0)],
                ["Delivered", s?.delivered],
                ["Failed", (s?.failed ?? 0) + (s?.bounced ?? 0)],
                ["Remaining", s?.remaining],
                ["Suppressed", s?.skipped_suppressed],
                ["Unsubscribed", s?.unsubscribed],
                ["Complaints", s?.complained],
              ] as const
            ).map(([label, v]) => (
              <div key={label}>
                <dt className="text-xs text-fg-muted">{label}</dt>
                <dd className="mt-0.5 text-lg font-semibold tabular">{fmt(v)}</dd>
              </div>
            ))}
          </dl>
        </CardBody>
      </Card>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Sending activity" description="Messages per hour" />
          <CardBody>
            <ColumnChart
              title="Hourly messages sent and failed"
              data={(report.data?.hourly ?? []).map((h) => ({ hour: h.hour, sent: h.sent, failed: h.failed + h.bounced }))}
              xKey="hour"
              series={[
                { key: "sent", label: "Sent" },
                { key: "failed", label: "Failed / bounced" },
              ]}
              formatX={(v) => new Date(v).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit" })}
              height={200}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Most common errors" />
          <CardBody>
            {report.data?.top_errors.length ? (
              <ul className="space-y-2 text-sm">
                {report.data.top_errors.map((e) => (
                  <li key={e.code} className="flex justify-between gap-3">
                    <code className="truncate text-xs text-fg-secondary">{e.code}</code>
                    <span className="tabular">{fmt(e.count)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-fg-muted">No errors so far.</p>
            )}
          </CardBody>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader
          title="Recipients"
          actions={
            <Select value={recipientStatus} onChange={(e) => setRecipientStatus(e.target.value)} className="w-44" aria-label="Filter recipients">
              <option value="">All statuses</option>
              {["queued", "sent", "delivered", "deferred", "failed", "bounced", "suppressed", "unsubscribed", "complained", "cancelled"].map((st) => (
                <option key={st} value={st}>
                  {titleCase(st)}
                  {report.data?.by_status[st] != null ? ` (${fmt(report.data.by_status[st])})` : ""}
                </option>
              ))}
            </Select>
          }
        />
        <Table>
          <THead>
            <tr>
              <TH>Email</TH>
              <TH>Status</TH>
              <TH className="text-right">Attempts</TH>
              <TH>Sent</TH>
              <TH>Last error</TH>
            </tr>
          </THead>
          <tbody>
            {recipients.items.length === 0 && <EmptyRow colSpan={5}>No recipients with this status.</EmptyRow>}
            {recipients.items.map((r) => (
              <TR key={r.id}>
                <TD>{r.email}</TD>
                <TD>
                  <StatusBadge status={r.status} />
                </TD>
                <TD className="text-right tabular">{r.attempts}</TD>
                <TD className="text-fg-secondary">{fmtDate(r.sent_at)}</TD>
                <TD className="max-w-xs truncate text-xs text-fg-muted" title={r.last_error ?? ""}>
                  {r.last_error ?? "—"}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
        <LoadMore hasNext={!!recipients.hasNextPage} loading={recipients.isFetchingNextPage} onClick={() => recipients.fetchNextPage()} />
      </Card>
    </>
  );
}
