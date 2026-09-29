import { Card, CardBody, CardHeader, ColumnChart, ErrorBanner, PageHeader, Stat, Tabs, errorMessage, fmtCompact, fmtPercent } from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import type { EventCounts } from "../types";

interface Summary {
  totals: EventCounts;
  daily: ({ day: string } & EventCounts)[];
}

export function ReportsPage() {
  const [days, setDays] = useState<"7" | "30" | "90">("7");
  const q = useQuery({ queryKey: ["reports", days], queryFn: () => api.get<Summary>("/user/reports", { days }) });
  const t = q.data?.totals;
  const sent = t?.sent || 0;
  return (
    <>
      <PageHeader
        title="Reports"
        description="Results across all your campaigns."
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
      {q.error && <ErrorBanner message={errorMessage(q.error)} />}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Sent" value={fmtCompact(t?.sent)} />
        <Stat label="Delivered" value={fmtCompact(t?.delivered)} hint={sent ? `${fmtPercent((t?.delivered ?? 0) / sent)} of sent` : undefined} />
        <Stat label="Bounced" value={fmtCompact(t?.bounced)} hint={sent ? `${fmtPercent((t?.bounced ?? 0) / sent, 2)} bounce rate` : undefined} />
        <Stat label="Unsubscribed" value={fmtCompact(t?.unsubscribed)} hint={`${fmtCompact(t?.complained)} complaints`} />
      </div>
      <Card className="mt-6">
        <CardHeader title="Daily volume" />
        <CardBody>
          <ColumnChart
            title={`Daily sent and failed messages, last ${days} days`}
            data={(q.data?.daily ?? []).map((d) => ({ day: d.day, sent: d.sent, failed: d.failed + d.bounced }))}
            xKey="day"
            series={[
              { key: "sent", label: "Sent" },
              { key: "failed", label: "Failed / bounced" },
            ]}
            formatX={(v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          />
        </CardBody>
      </Card>
    </>
  );
}
