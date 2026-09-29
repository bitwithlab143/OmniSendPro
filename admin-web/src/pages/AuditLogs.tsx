import {
  Card,
  EmptyRow,
  ErrorBanner,
  Input,
  LoadMore,
  LoadingRows,
  PageHeader,
  Table,
  TD,
  TH,
  THead,
  TR,
  errorMessage,
  fmtDate,
  usePaged,
  type Page,
} from "@omnisend/web-shared";
import { useState } from "react";

import { api } from "../api";
import type { AuditEntry } from "../types";

function Changes({ entry }: { entry: AuditEntry }) {
  const keys = Array.from(new Set([...Object.keys(entry.old_value ?? {}), ...Object.keys(entry.new_value ?? {})]));
  if (!keys.length) return <span className="text-fg-muted">—</span>;
  return (
    <ul className="space-y-0.5 text-xs">
      {keys.slice(0, 5).map((k) => (
        <li key={k}>
          <span className="text-fg-muted">{k}: </span>
          {entry.old_value && k in entry.old_value && <span className="text-fg-secondary line-through">{String(entry.old_value[k])}</span>}
          {entry.old_value && k in entry.old_value && entry.new_value && k in entry.new_value && " → "}
          {entry.new_value && k in entry.new_value && <span>{String(entry.new_value[k])}</span>}
        </li>
      ))}
    </ul>
  );
}

export function AuditLogsPage() {
  const [action, setAction] = useState("");
  const [filter, setFilter] = useState("");
  const list = usePaged<AuditEntry>(["audit", filter], (cursor) => api.get<Page<AuditEntry>>("/admin/audit-logs", { action: filter, cursor }));
  return (
    <>
      <PageHeader title="Audit logs" description="Append-only record of every sensitive action." />
      <form
        className="mb-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setFilter(action.trim().toUpperCase());
        }}
      >
        <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="Filter by action, e.g. USER_SUSPENDED" className="max-w-sm" aria-label="Filter by action" />
      </form>
      {list.error && <ErrorBanner message={errorMessage(list.error)} />}
      <Card>
        <Table>
          <THead>
            <tr>
              <TH>Time</TH>
              <TH>Actor</TH>
              <TH>Action</TH>
              <TH>Resource</TH>
              <TH>Changes</TH>
              <TH>IP</TH>
            </tr>
          </THead>
          <tbody>
            {list.isLoading && <LoadingRows colSpan={6} />}
            {!list.isLoading && list.items.length === 0 && <EmptyRow colSpan={6}>No entries.</EmptyRow>}
            {list.items.map((e) => (
              <TR key={e.id}>
                <TD className="whitespace-nowrap text-fg-secondary">{fmtDate(e.timestamp)}</TD>
                <TD>{e.actor ?? e.actor_type}</TD>
                <TD>
                  <code className="rounded bg-surface-2 px-1.5 py-0.5 text-xs">{e.action}</code>
                </TD>
                <TD className="text-xs text-fg-secondary">
                  {e.resource}
                  {e.resource_id && <span className="font-mono"> · {e.resource_id.slice(0, 8)}</span>}
                </TD>
                <TD>
                  <Changes entry={e} />
                </TD>
                <TD className="text-xs text-fg-muted">{e.ip ?? "—"}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
        <LoadMore hasNext={!!list.hasNextPage} loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()} />
      </Card>
    </>
  );
}
