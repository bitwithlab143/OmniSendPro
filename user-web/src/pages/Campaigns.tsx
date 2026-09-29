import {
  Card,
  EmptyState,
  ErrorBanner,
  LoadMore,
  LoadingRows,
  PageHeader,
  Progress,
  StatusBadge,
  Table,
  Tabs,
  TD,
  TH,
  THead,
  TR,
  buttonVariants,
  errorMessage,
  fmt,
  fmtRelative,
  usePaged,
  type Page,
} from "@omnisend/web-shared";
import { Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "../api";
import type { Campaign } from "../types";

type View = "all" | "pending" | "processing" | "completed" | "failed";

export function CampaignsPage() {
  const [view, setView] = useState<View>("all");
  const list = usePaged<Campaign>(["campaigns", "list", view], (cursor) => api.get<Page<Campaign>>("/user/campaigns", { view, cursor }), { refetchInterval: 5000 });
  const href = (c: Campaign) => (["DRAFT", "READY"].includes(c.status) ? `/campaigns/${c.id}/edit` : `/campaigns/${c.id}`);
  return (
    <>
      <PageHeader
        title="Campaigns"
        actions={
          <Link to="/campaigns/new" className={buttonVariants()}>
            <Plus /> New campaign
          </Link>
        }
      />
      <Tabs
        className="mb-4"
        value={view}
        onChange={setView}
        options={[
          { value: "all", label: "All" },
          { value: "pending", label: "Drafts & queued" },
          { value: "processing", label: "Sending" },
          { value: "completed", label: "Completed" },
          { value: "failed", label: "Failed" },
        ]}
      />
      {list.error && <ErrorBanner message={errorMessage(list.error)} />}
      <Card>
        {!list.isLoading && list.items.length === 0 ? (
          <EmptyState
            title="No campaigns here"
            description="Create a campaign, upload your opted-in recipients and start sending."
            action={
              <Link to="/campaigns/new" className={buttonVariants()}>
                <Plus /> New campaign
              </Link>
            }
          />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Campaign</TH>
                <TH>Status</TH>
                <TH className="hidden min-w-40 md:table-cell">Progress</TH>
                <TH className="hidden text-right sm:table-cell">Recipients</TH>
                <TH className="hidden text-right lg:table-cell">Delivered</TH>
                <TH className="hidden lg:table-cell">Created</TH>
              </tr>
            </THead>
            <tbody>
              {list.isLoading && <LoadingRows colSpan={6} />}
              {list.items.map((c) => (
                <TR key={c.id}>
                  <TD>
                    <Link to={href(c)} className="font-medium hover:underline">
                      {c.name}
                    </Link>
                    <div className="max-w-xs truncate text-xs text-fg-muted">{c.subject || "No subject yet"}</div>
                  </TD>
                  <TD>
                    <StatusBadge status={c.status} />
                  </TD>
                  <TD className="hidden md:table-cell">
                    <div className="flex items-center gap-2">
                      <Progress value={c.progress?.percent ?? 0} className="w-24" label={`${c.name} progress`} />
                      <span className="text-xs tabular text-fg-secondary">{c.progress?.percent ?? 0}%</span>
                    </div>
                  </TD>
                  <TD className="hidden text-right tabular sm:table-cell">{fmt(c.total_recipients)}</TD>
                  <TD className="hidden text-right tabular lg:table-cell">{fmt(c.delivered)}</TD>
                  <TD className="hidden text-fg-secondary lg:table-cell">{fmtRelative(c.created_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
        <LoadMore hasNext={!!list.hasNextPage} loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()} />
      </Card>
    </>
  );
}
