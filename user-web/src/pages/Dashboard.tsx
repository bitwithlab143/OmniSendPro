import {
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  ErrorBanner,
  Meter,
  PageHeader,
  Progress,
  StatusBadge,
  buttonVariants,
  errorMessage,
  fmt,
  fmtRate,
  useAuth,
  type Page,
  useLiveQuery,
} from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { Gauge, Plus, Send } from "lucide-react";
import { Link } from "react-router";

import { api } from "../api";
import type { Campaign, EventCounts } from "../types";

interface Dashboard {
  today: EventCounts;
  active_campaigns: number;
  assigned: number;
  processed: number;
  delivered: number;
  failed: number;
  remaining: number;
  current_speed: number;
  quota: { daily_limit: number | null; hourly_limit: number | null; used_today: number; used_this_hour: number };
}

export function DashboardPage() {
  const { user } = useAuth();
  const q = useLiveQuery({ api, queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/user/dashboard"), streamPath: "/user/dashboard/stream", pollMs: 3000 });
  const active = useQuery({
    queryKey: ["campaigns", "processing"],
    queryFn: () => api.get<Page<Campaign>>("/user/campaigns", { view: "processing", limit: 5 }),
    refetchInterval: 3000,
  });
  const ready = useQuery({ queryKey: ["campaigns", "pending"], queryFn: () => api.get<Page<Campaign>>("/user/campaigns", { view: "pending", limit: 5 }) });
  const d = q.data;
  const readyToStart = ready.data?.items.find((c) => c.status === "READY");

  return (
    <>
      <PageHeader
        title={`Hello, ${user?.full_name?.split(" ")[0] || user?.username}`}
        description="Today's sending at a glance."
        actions={
          <Link to="/campaigns/new" className={buttonVariants({ variant: "secondary" })}>
            <Plus /> New campaign
          </Link>
        }
      />
      {q.error && <ErrorBanner message={errorMessage(q.error)} onRetry={() => q.refetch()} />}

      <Card>
        <CardBody className="grid gap-6 lg:grid-cols-[1fr_auto] lg:items-center">
          <dl className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-5">
            {(
              [
                ["Assigned", d?.assigned],
                ["Processed", d?.processed],
                ["Delivered", d?.delivered],
                ["Failed", d?.failed],
                ["Remaining", d?.remaining],
              ] as const
            ).map(([label, value]) => (
              <div key={label}>
                <dt className="text-sm text-fg-secondary">{label}</dt>
                <dd className="mt-1 text-2xl font-semibold tracking-tight">{fmt(value)}</dd>
              </div>
            ))}
          </dl>
          <div className="flex items-center gap-6 border-t border-border pt-5 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
            <div>
              <div className="flex items-center gap-1.5 text-sm text-fg-secondary">
                <Gauge className="size-4" /> Current speed
              </div>
              <div className="mt-1 text-4xl font-semibold tracking-tight">{fmtRate(d?.current_speed ?? 0)}</div>
            </div>
            {readyToStart ? (
              <Link to={`/campaigns/${readyToStart.id}/edit?step=review`} className={buttonVariants({ size: "lg" })}>
                <Send /> Start sending
              </Link>
            ) : (
              <Link to="/campaigns/new" className={buttonVariants({ size: "lg" })}>
                <Send /> Start sending
              </Link>
            )}
          </div>
        </CardBody>
      </Card>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Sending now" actions={<Link to="/campaigns" className="text-sm text-primary hover:underline">All campaigns</Link>} />
          {active.data?.items.length ? (
            <ul className="divide-y divide-border">
              {active.data.items.map((c) => (
                <li key={c.id} className="px-5 py-4">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <Link to={`/campaigns/${c.id}`} className="truncate font-medium hover:underline">
                      {c.name}
                    </Link>
                    <StatusBadge status={c.status} />
                  </div>
                  <Progress value={c.progress?.percent ?? 0} label={`${c.name} progress`} />
                  <div className="mt-1.5 flex justify-between text-xs text-fg-muted tabular">
                    <span>
                      {fmt(c.progress?.done)} of {fmt(c.total_recipients)}
                    </span>
                    <span>{c.progress?.percent ?? 0}%</span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={<Send />} title="Nothing sending right now" description="Start a campaign to see live progress here." />
          )}
        </Card>
        <Card>
          <CardHeader title="Your quota" description="Limits set by your administrator" />
          <CardBody className="space-y-5">
            <Meter label="Today" used={d?.quota.used_today ?? 0} limit={d?.quota.daily_limit} />
            <Meter label="This hour" used={d?.quota.used_this_hour ?? 0} limit={d?.quota.hourly_limit} />
            <p className="text-xs text-fg-muted">When a limit is reached, sending pauses automatically and resumes in the next window.</p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
