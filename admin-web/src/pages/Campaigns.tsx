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
  Progress,
  Select,
  StatusBadge,
  Table,
  Tabs,
  TD,
  TH,
  THead,
  TR,
  errorMessage,
  fmt,
  fmtRelative,
  useAction,
  useAuth,
  usePaged,
  type Page,
} from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "../api";
import type { Campaign, User } from "../types";
import { CampaignFields, campaignBody } from "./campaignForm";

type View = "all" | "pending" | "processing" | "completed" | "failed";

export function CampaignsPage() {
  const { can } = useAuth();
  const [view, setView] = useState<View>("all");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const list = usePaged<Campaign>(["campaigns", view, search], (cursor) => api.get<Page<Campaign>>("/admin/campaigns", { view, q: search, cursor }), {
    refetchInterval: 10_000,
  });
  return (
    <>
      <PageHeader
        title="Campaigns"
        description="Every campaign across all users."
        actions={
          can("campaigns.write") && (
            <Button onClick={() => setCreating(true)}>
              <Plus /> New campaign
            </Button>
          )
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs
          value={view}
          onChange={setView}
          options={[
            { value: "all", label: "All" },
            { value: "pending", label: "Pending" },
            { value: "processing", label: "Processing" },
            { value: "completed", label: "Completed" },
            { value: "failed", label: "Failed" },
          ]}
        />
        <form
          className="relative ml-auto w-full sm:w-64"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(q);
          }}
        >
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-muted" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search campaigns" className="pl-9" aria-label="Search campaigns" />
        </form>
      </div>
      {list.error && <ErrorBanner message={errorMessage(list.error)} />}
      <Card>
        <Table>
          <THead>
            <tr>
              <TH>Campaign</TH>
              <TH>Owner</TH>
              <TH>Status</TH>
              <TH className="min-w-40">Progress</TH>
              <TH className="text-right">Recipients</TH>
              <TH>Created</TH>
            </tr>
          </THead>
          <tbody>
            {list.isLoading && <LoadingRows colSpan={6} />}
            {!list.isLoading && list.items.length === 0 && <EmptyRow colSpan={6}>No campaigns in this view.</EmptyRow>}
            {list.items.map((c) => (
              <TR key={c.id}>
                <TD>
                  <Link to={`/campaigns/${c.id}`} className="font-medium hover:underline">
                    {c.name}
                  </Link>
                  <div className="max-w-xs truncate text-xs text-fg-muted">{c.subject || "No subject"}</div>
                </TD>
                <TD className="text-fg-secondary">{c.username}</TD>
                <TD>
                  <StatusBadge status={c.status} />
                </TD>
                <TD>
                  <div className="flex items-center gap-2">
                    <Progress value={c.progress?.percent ?? 0} className="w-24" label={`${c.name} progress`} />
                    <span className="text-xs tabular text-fg-secondary">{c.progress?.percent ?? 0}%</span>
                  </div>
                </TD>
                <TD className="text-right tabular">{fmt(c.total_recipients)}</TD>
                <TD className="text-fg-secondary">{fmtRelative(c.created_at)}</TD>
              </TR>
            ))}
          </tbody>
        </Table>
        <LoadMore hasNext={!!list.hasNextPage} loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()} />
      </Card>
      {creating && <CreateCampaignDialog onClose={() => setCreating(false)} />}
    </>
  );
}

function CreateCampaignDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [userId, setUserId] = useState<string>("");
  const users = useQuery({ queryKey: ["users", "USER-role"], queryFn: () => api.get<Page<User>>("/admin/users", { role: "USER", status: "active", limit: 200 }) });
  const create = useAction((body: unknown) => api.post<Campaign>("/admin/campaigns", body), {
    invalidate: [["campaigns"]],
    success: "Campaign created",
    onSuccess: (c) => {
      onClose();
      navigate(`/campaigns/${c.id}`);
    },
  });
  return (
    <Dialog
      open
      onClose={onClose}
      title="New campaign"
      size="lg"
      description="Create a campaign on behalf of a user. The user can review, upload recipients and start it."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="new-campaign" loading={create.isPending} disabled={!userId}>
            Create campaign
          </Button>
        </>
      }
    >
      <form
        id="new-campaign"
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          const body = campaignBody(new FormData(e.currentTarget));
          create.mutate(Object.fromEntries(Object.entries({ ...body, user_id: userId }).filter(([, v]) => v !== null)));
        }}
      >
        <Field label="Assign to user">
          {(id) => (
            <Select id={id} value={userId} onChange={(e) => setUserId(e.target.value)} required>
              <option value="">Select a user</option>
              {users.data?.items.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username} — {u.email}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <CampaignFields key={userId} userId={userId || null} />
      </form>
    </Dialog>
  );
}
