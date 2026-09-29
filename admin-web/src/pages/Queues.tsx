import {
  Button,
  Card,
  Dialog,
  EmptyRow,
  ErrorBanner,
  LoadMore,
  LoadingRows,
  PageHeader,
  StatusBadge,
  Table,
  Tabs,
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
  usePaged,
  type Page,
} from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "../api";
import type { Job } from "../types";

type Status = "pending" | "claimed" | "processing" | "retry" | "failed" | "dead_letter" | "completed";

interface Summary {
  by_status: Record<string, { jobs: number; recipients: number }>;
  oldest_ready_at: string | null;
}

interface JobDetail {
  job: Job;
  attempts: { id: string; attempt_no: number; worker_id: string | null; started_at: string; finished_at: string | null; outcome: string; error_code: string | null; error_message: string | null }[];
}

export function QueuesPage() {
  const { can } = useAuth();
  const [status, setStatus] = useState<Status>("pending");
  const [selected, setSelected] = useState<string | null>(null);
  const summary = useQuery({ queryKey: ["queues"], queryFn: () => api.get<Summary>("/admin/queues"), refetchInterval: 5000 });
  const jobs = usePaged<Job>(["queues", "jobs", status], (cursor) => api.get<Page<Job>>("/admin/queues/jobs", { status, cursor }), { refetchInterval: 5000 });
  const requeue = useAction((id: string) => api.post<Job>(`/admin/queues/jobs/${id}/requeue`), { invalidate: [["queues"]], success: "Job requeued" });
  const count = (s: string) => summary.data?.by_status[s]?.jobs;

  return (
    <>
      <PageHeader
        title="Queues"
        description={summary.data?.oldest_ready_at ? `Oldest ready job waiting since ${fmtRelative(summary.data.oldest_ready_at)}` : "Jobs flow pending → claimed → processing → completed; failures retry with backoff, then dead-letter."}
      />
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        {(["pending", "claimed", "processing", "retry", "dead_letter", "completed", "cancelled"] as const).map((s) => (
          <div key={s} className="rounded-xl border border-border bg-surface px-4 py-3 shadow-card">
            <div className="text-xs capitalize text-fg-muted">{s.replace("_", " ")}</div>
            <div className="mt-1 text-lg font-semibold tabular">{fmt(count(s) ?? 0)}</div>
            <div className="text-xs text-fg-muted tabular">{fmt(summary.data?.by_status[s]?.recipients ?? 0)} recipients</div>
          </div>
        ))}
      </div>
      <Tabs
        className="mb-4"
        value={status}
        onChange={setStatus}
        options={(["pending", "processing", "retry", "dead_letter", "completed"] as const).map((s) => ({ value: s, label: s.replace("_", " ").replace(/^\w/, (x) => x.toUpperCase()), count: count(s) }))}
      />
      {jobs.error && <ErrorBanner message={errorMessage(jobs.error)} />}
      <Card>
        <Table>
          <THead>
            <tr>
              <TH>Job</TH>
              <TH>Campaign</TH>
              <TH>Status</TH>
              <TH className="text-right">Batch</TH>
              <TH className="text-right">Attempts</TH>
              <TH>Available</TH>
              <TH>Last error</TH>
              <TH className="text-right">Actions</TH>
            </tr>
          </THead>
          <tbody>
            {jobs.isLoading && <LoadingRows colSpan={8} />}
            {!jobs.isLoading && jobs.items.length === 0 && <EmptyRow colSpan={8}>Nothing here.</EmptyRow>}
            {jobs.items.map((j) => (
              <TR key={j.id}>
                <TD>
                  <button className="font-mono text-xs text-primary hover:underline" onClick={() => setSelected(j.id)}>
                    {j.id.slice(0, 8)}
                  </button>
                </TD>
                <TD>
                  <Link to={`/campaigns/${j.campaign_id}`} className="font-mono text-xs hover:underline">
                    {j.campaign_id.slice(0, 8)}
                  </Link>
                </TD>
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
                <TD className="text-right">
                  {j.status === "dead_letter" && can("queues.write") && (
                    <Button variant="secondary" size="sm" loading={requeue.isPending && requeue.variables === j.id} onClick={() => requeue.mutate(j.id)}>
                      <RotateCcw /> Requeue
                    </Button>
                  )}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
        <LoadMore hasNext={!!jobs.hasNextPage} loading={jobs.isFetchingNextPage} onClick={() => jobs.fetchNextPage()} />
      </Card>
      {selected && <JobDialog id={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function JobDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["queues", "job", id], queryFn: () => api.get<JobDetail>(`/admin/queues/jobs/${id}`) });
  return (
    <Dialog open onClose={onClose} title={`Job ${id.slice(0, 8)}`} size="lg" description="Every claim creates an attempt; only the current attempt may report results.">
      {q.error && <ErrorBanner message={errorMessage(q.error)} />}
      <Table>
        <THead>
          <tr>
            <TH>#</TH>
            <TH>Outcome</TH>
            <TH>Started</TH>
            <TH>Finished</TH>
            <TH>Error</TH>
          </tr>
        </THead>
        <tbody>
          {q.data?.attempts.length === 0 && <EmptyRow colSpan={5}>Not claimed yet.</EmptyRow>}
          {q.data?.attempts.map((a) => (
            <TR key={a.id}>
              <TD className="tabular">{a.attempt_no}</TD>
              <TD>
                <StatusBadge status={a.outcome === "lease_expired" ? "retry" : a.outcome === "running" ? "processing" : a.outcome} />
              </TD>
              <TD className="text-fg-secondary">{fmtDate(a.started_at)}</TD>
              <TD className="text-fg-secondary">{fmtDate(a.finished_at)}</TD>
              <TD className="text-xs text-fg-muted">{[a.error_code, a.error_message].filter(Boolean).join(" — ") || "—"}</TD>
            </TR>
          ))}
        </tbody>
      </Table>
    </Dialog>
  );
}
