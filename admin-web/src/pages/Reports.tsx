import {
  Card,
  CardBody,
  CardHeader,
  ColumnChart,
  EmptyRow,
  ErrorBanner,
  PageHeader,
  Stat,
  Table,
  Tabs,
  TD,
  TH,
  THead,
  TR,
  errorMessage,
  fmt,
  fmtCompact,
  fmtPercent,
} from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import type { EventCounts } from "../types";

interface Summary {
  totals: EventCounts;
  rates: { delivery_rate: number; bounce_rate: number; complaint_rate: number };
  daily: ({ day: string } & EventCounts)[];
}

interface Breakdown {
  rows: ({ id: string | null; name: string } & EventCounts)[];
}

export function ReportsPage() {
  const [days, setDays] = useState<"7" | "30" | "90">("7");
  const [group, setGroup] = useState<"user" | "provider">("user");
  const summary = useQuery({ queryKey: ["reports", "summary", days], queryFn: () => api.get<Summary>("/admin/reports/summary", { days }) });
  const breakdown = useQuery({ queryKey: ["reports", "breakdown", group, days], queryFn: () => api.get<Breakdown>("/admin/reports/breakdown", { group, days }) });
  const t = summary.data?.totals;

  return (
    <>
      <PageHeader
        title="Reports"
        description="Delivery, bounce and complaint reporting from delivery events. Counters are eventually consistent."
        actions={
          <Tabs
            value={days}
            onChange={setDays}
            options={[
              { value: "7", label: "7 days" },
              { value: "30", label: "30 days" },
              { value: "90", label: "90 days" },
            ]}
          />
        }
      />
      {summary.error && <ErrorBanner message={errorMessage(summary.error)} />}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Sent" value={fmtCompact(t?.sent)} />
        <Stat label="Delivery rate" value={fmtPercent(summary.data?.rates.delivery_rate)} hint={`${fmt(t?.delivered)} delivered`} />
        <Stat label="Bounce rate" value={fmtPercent(summary.data?.rates.bounce_rate, 2)} hint={`${fmt(t?.bounced)} bounced`} />
        <Stat label="Complaint rate" value={fmtPercent(summary.data?.rates.complaint_rate, 3)} hint={`${fmt(t?.complained)} complaints`} />
      </div>
      <Card className="mt-6">
        <CardHeader title="Daily volume" />
        <CardBody>
          <ColumnChart
            title={`Daily sent, failed and deferred messages, last ${days} days`}
            data={(summary.data?.daily ?? []).map((d) => ({ day: d.day, sent: d.sent, failed: d.failed + d.bounced, deferred: d.deferred }))}
            xKey="day"
            series={[
              { key: "sent", label: "Sent" },
              { key: "failed", label: "Failed / bounced" },
              { key: "deferred", label: "Deferred (retried)" },
            ]}
            formatX={(v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          />
        </CardBody>
      </Card>
      <Card className="mt-6">
        <CardHeader
          title="Breakdown"
          actions={
            <Tabs
              value={group}
              onChange={setGroup}
              options={[
                { value: "user", label: "By user" },
                { value: "provider", label: "By provider" },
              ]}
            />
          }
        />
        <Table>
          <THead>
            <tr>
              <TH>{group === "user" ? "User" : "Provider"}</TH>
              <TH className="text-right">Sent</TH>
              <TH className="text-right">Delivered</TH>
              <TH className="text-right">Failed</TH>
              <TH className="text-right">Bounced</TH>
              <TH className="text-right">Complaints</TH>
              <TH className="text-right">Unsubscribed</TH>
              <TH className="text-right">Bounce rate</TH>
            </tr>
          </THead>
          <tbody>
            {breakdown.data?.rows.length === 0 && <EmptyRow colSpan={8}>No activity in this period.</EmptyRow>}
            {breakdown.data?.rows.map((r) => (
              <TR key={r.id ?? "none"}>
                <TD className="font-medium">{r.name}</TD>
                <TD className="text-right tabular">{fmt(r.sent)}</TD>
                <TD className="text-right tabular">{fmt(r.delivered)}</TD>
                <TD className="text-right tabular">{fmt(r.failed)}</TD>
                <TD className="text-right tabular">{fmt(r.bounced)}</TD>
                <TD className="text-right tabular">{fmt(r.complained)}</TD>
                <TD className="text-right tabular">{fmt(r.unsubscribed)}</TD>
                <TD className="text-right tabular">{fmtPercent(r.sent ? r.bounced / r.sent : null, 2)}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
      </Card>
    </>
  );
}
