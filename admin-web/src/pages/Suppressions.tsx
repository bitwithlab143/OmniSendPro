import {
  Button,
  Card,
  Dialog,
  EmptyRow,
  ErrorBanner,
  Field,
  Input,
  LoadMore,
  LoadingRows,
  PageHeader,
  Select,
  StatusBadge,
  Table,
  TD,
  TH,
  THead,
  TR,
  errorMessage,
  fmtDate,
  titleCase,
  useAction,
  useAuth,
  useConfirm,
  usePaged,
  type Page,
} from "@omnisend/web-shared";
import { Plus, Search, Trash2 } from "lucide-react";
import { useState } from "react";

import { api } from "../api";
import type { Suppression } from "../types";

const TYPES = ["unsubscribe", "hard_bounce", "complaint", "invalid", "admin_blocked"];

export function SuppressionsPage() {
  const { can } = useAuth();
  const confirm = useConfirm();
  const writable = can("suppressions.write");
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const list = usePaged<Suppression>(["suppressions", type, search], (cursor) => api.get<Page<Suppression>>("/admin/suppressions", { type, q: search, cursor }));
  const remove = useAction((id: number) => api.del(`/admin/suppressions/${id}`), { invalidate: [["suppressions"]], success: "Suppression removed" });

  return (
    <>
      <PageHeader
        title="Suppression list"
        description="Suppressed addresses are never sent to. Bounces, complaints and admin blocks apply to every user; unsubscribes apply to the sender they unsubscribed from."
        actions={
          writable && (
            <Button onClick={() => setAdding(true)}>
              <Plus /> Block address
            </Button>
          )
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select value={type} onChange={(e) => setType(e.target.value)} className="w-48" aria-label="Filter by type">
          <option value="">All types</option>
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {titleCase(t)}
            </option>
          ))}
        </Select>
        <form
          className="relative ml-auto w-full sm:w-72"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(q);
          }}
        >
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-muted" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search email" className="pl-9" aria-label="Search suppressions" />
        </form>
      </div>
      {list.error && <ErrorBanner message={errorMessage(list.error)} />}
      <Card>
        <Table>
          <THead>
            <tr>
              <TH>Email</TH>
              <TH>Type</TH>
              <TH>Scope</TH>
              <TH>Reason</TH>
              <TH>Added</TH>
              {writable && <TH className="text-right">Actions</TH>}
            </tr>
          </THead>
          <tbody>
            {list.isLoading && <LoadingRows colSpan={6} />}
            {!list.isLoading && list.items.length === 0 && <EmptyRow colSpan={6}>No suppressed addresses.</EmptyRow>}
            {list.items.map((s) => (
              <TR key={s.id}>
                <TD className="font-medium">{s.email_normalized}</TD>
                <TD>
                  <StatusBadge status={s.type === "complaint" ? "complained" : s.type === "hard_bounce" ? "bounced" : s.type === "unsubscribe" ? "unsubscribed" : "suppressed"} />
                  <span className="ml-2 text-xs text-fg-muted">{titleCase(s.type)}</span>
                </TD>
                <TD className="text-fg-secondary">{s.scope_user_id ? "Single user" : "Global"}</TD>
                <TD className="max-w-xs truncate text-sm text-fg-secondary" title={s.reason ?? ""}>
                  {s.reason ?? "—"}
                </TD>
                <TD className="text-fg-secondary">{fmtDate(s.created_at)}</TD>
                {writable && (
                  <TD className="text-right">
                    {s.type !== "complaint" && (
                      <Button
                        variant="danger-ghost"
                        size="icon-sm"
                        aria-label={`Remove ${s.email_normalized}`}
                        title="Remove"
                        onClick={async () => {
                          if (await confirm({ title: "Remove suppression?", message: `${s.email_normalized} will be eligible to receive mail again. Only do this with documented consent.`, confirmLabel: "Remove", danger: true }))
                            remove.mutate(s.id);
                        }}
                      >
                        <Trash2 />
                      </Button>
                    )}
                  </TD>
                )}
              </TR>
            ))}
          </tbody>
        </Table>
        <LoadMore hasNext={!!list.hasNextPage} loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()} />
      </Card>
      {adding && <AddDialog onClose={() => setAdding(false)} />}
    </>
  );
}

function AddDialog({ onClose }: { onClose: () => void }) {
  const add = useAction((body: unknown) => api.post("/admin/suppressions", body), { invalidate: [["suppressions"]], success: "Address suppressed", onSuccess: onClose });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Block an address"
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="add-supp" loading={add.isPending}>
            Block
          </Button>
        </>
      }
    >
      <form
        id="add-supp"
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          add.mutate({ email: f.get("email"), type: "admin_blocked", reason: f.get("reason") || null });
        }}
      >
        <Field label="Email">{(id) => <Input id={id} name="email" type="email" required />}</Field>
        <Field label="Reason" optional>{(id) => <Input id={id} name="reason" maxLength={512} />}</Field>
      </form>
    </Dialog>
  );
}
