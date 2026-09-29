import {
  Card,
  CardBody,
  CardHeader,
  ColumnChart,
  EmptyRow,
  ErrorBanner,
  PageHeader,
  Stat,
  StatusBadge,
  Table,
  TD,
  TH,
  THead,
  TR,
  errorMessage,
  fmt,
  fmtCompact,
  fmtRate,
  fmtRelative,
  useLiveQuery,
} from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { Activity, Cpu, Layers, Server } from "lucide-react";
import { Link } from "react-router";

import { api } from "../api";
import type { EventCounts } from "../types";

interface Dashboard {
  active_workers: number;
  total_workers: number;
  online_providers: number;
  total_providers: number;
  queue_depth: number;
  queue_recipients: number;
  emails_per_second: number;
  campaigns: Record<string, number>;
  today: EventCounts;
  workers: { id: string; worker_id: string; name: string; status: string; cpu: number | null; memory: number | null; active_jobs: number; current_rate: number; capacity: number; disabled: boolean; last_heartbeat_at: string | null }[];
  providers: { id: string; provider_name: string; status: string; health_score: number }[];
}

interface Summary {
  daily: ({ day: string } & EventCounts)[];
}

export function DashboardPage() {
  const q = useLiveQuery({ api, queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/admin/dashboard"), streamPath: "/admin/dashboard/stream", pollMs: 5000 });
  const summary = useQuery({ queryKey: ["reports", "summary", 14], queryFn: () => api.get<Summary>("/admin/reports/summary", { days: 14 }), refetchInterval: 60_000 });
  const d = q.data;

  return (
    <>
      <PageHeader title="Dashboard" description="Live view of sending, workers and providers. Refreshes every 5 seconds." />
      {q.error && <ErrorBanner message={errorMessage(q.error)} onRetry={() => q.refetch()} />}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Emails / sec" value={d ? fmtRate(d.emails_per_second) : "—"} icon={<Activity />} hint="10-second average" />
        <Stat label="Active workers" value={d ? `${d.active_workers} / ${d.total_workers}` : "—"} icon={<Cpu />} />
        <Stat label="Online providers" value={d ? `${d.online_providers} / ${d.total_providers}` : "—"} icon={<Server />} />
        <Stat label="Queue depth" value={d ? fmt(d.queue_depth) : "—"} icon={<Layers />} hint={d ? `${fmtCompact(d.queue_recipients)} recipients waiting` : undefined} />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        {(["sent", "delivered", "failed", "bounced", "deferred", "complained"] as const).map((k) => (
          <div key={k} className="rounded-xl border border-border bg-surface px-4 py-3 shadow-card">
            <div className="text-xs capitalize text-fg-muted">{k} today</div>
            <div className="mt-1 text-lg font-semibold">{d ? fmtCompact(d.today[k]) : "—"}</div>
          </div>
        ))}
      </div>

      <Card className="mt-6">
        <CardHeader title="Delivery — last 14 days" description="Messages accepted by providers vs. failed or bounced" />
        <CardBody>
          <ColumnChart
            title="Daily messages sent and failed, last 14 days"
            data={(summary.data?.daily ?? []).map((r) => ({ day: r.day, sent: r.sent, failed: r.failed + r.bounced }))}
            xKey="day"
            series={[
              { key: "sent", label: "Sent" },
              { key: "failed", label: "Failed / bounced" },
            ]}
            formatX={(v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          />
        </CardBody>
      </Card>

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Workers" actions={<Link className="text-sm text-primary hover:underline" to="/workers">Manage</Link>} />
          <Table>
            <THead>
              <tr>
                <TH>Worker</TH>
                <TH>Status</TH>
                <TH className="text-right">CPU</TH>
                <TH className="text-right">Memory</TH>
                <TH className="text-right">Jobs</TH>
                <TH className="text-right">Rate</TH>
                <TH>Last heartbeat</TH>
              </tr>
            </THead>
            <tbody>
              {d?.workers.length === 0 && <EmptyRow colSpan={7}>No workers provisioned yet.</EmptyRow>}
              {d?.workers.map((w) => (
                <TR key={w.id}>
                  <TD>
                    <div className="font-medium">{w.name}</div>
                    <div className="text-xs text-fg-muted">{w.worker_id}</div>
                  </TD>
                  <TD>{w.disabled ? <StatusBadge status="disabled" /> : <StatusBadge status={w.status} />}</TD>
                  <TD className="text-right tabular">{w.cpu == null ? "—" : `${Math.round(w.cpu)}%`}</TD>
                  <TD className="text-right tabular">{w.memory == null ? "—" : `${Math.round(w.memory)}%`}</TD>
                  <TD className="text-right tabular">{w.active_jobs}</TD>
                  <TD className="text-right tabular">
                    {fmtRate(w.current_rate)} <span className="text-fg-muted">/ {w.capacity}</span>
                  </TD>
                  <TD className="text-fg-secondary">{fmtRelative(w.last_heartbeat_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
        <Card>
          <CardHeader title="Provider health" actions={<Link className="text-sm text-primary hover:underline" to="/providers">Manage</Link>} />
          <ul className="divide-y divide-border">
            {d?.providers.length === 0 && <li className="px-5 py-8 text-center text-sm text-fg-muted">No providers configured.</li>}
            {d?.providers.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-3 px-5 py-3">
                <Link to={`/providers/${p.id}`} className="min-w-0 truncate text-sm font-medium hover:underline">
                  {p.provider_name}
                </Link>
                <div className="flex items-center gap-3">
                  <span className="text-sm tabular text-fg-secondary">{Math.round(p.health_score)}%</span>
                  <StatusBadge status={p.status} />
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
