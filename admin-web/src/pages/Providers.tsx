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
  TD,
  TH,
  THead,
  TR,
  errorMessage,
  fmtRelative,
  useAction,
  useAuth,
  usePaged,
  type Page,
} from "@omnisend/web-shared";
import { Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { api } from "../api";
import type { Provider } from "../types";
import { ProviderFields, providerBody } from "./providerForm";

export function ProvidersPage() {
  const { can } = useAuth();
  const [creating, setCreating] = useState(false);
  const list = usePaged<Provider>(["providers"], (cursor) => api.get<Page<Provider>>("/admin/providers", { cursor }));
  return (
    <>
      <PageHeader
        title="Providers"
        description="SMTP / email providers, their limits and health."
        actions={
          can("providers.write") && (
            <Button onClick={() => setCreating(true)}>
              <Plus /> Add provider
            </Button>
          )
        }
      />
      {list.error && <ErrorBanner message={errorMessage(list.error)} />}
      <Card>
        <Table>
          <THead>
            <tr>
              <TH>Provider</TH>
              <TH>Status</TH>
              <TH className="text-right">Health</TH>
              <TH>Limits</TH>
              <TH>Last test</TH>
            </tr>
          </THead>
          <tbody>
            {list.isLoading && <LoadingRows colSpan={5} />}
            {!list.isLoading && list.items.length === 0 && <EmptyRow colSpan={5}>No providers yet. Add your first SMTP provider.</EmptyRow>}
            {list.items.map((p) => (
              <TR key={p.id}>
                <TD>
                  <Link to={`/providers/${p.id}`} className="font-medium hover:underline">
                    {p.provider_name}
                  </Link>
                  <div className="text-xs text-fg-muted">
                    {p.host}:{p.port} · {p.from_email}
                  </div>
                </TD>
                <TD>
                  <StatusBadge status={p.status} />
                </TD>
                <TD className="text-right tabular">{Math.round(p.health_score)}%</TD>
                <TD className="text-xs text-fg-secondary tabular">
                  {[p.per_second_limit && `${p.per_second_limit}/s`, p.hourly_limit && `${p.hourly_limit.toLocaleString()}/h`, p.daily_limit && `${p.daily_limit.toLocaleString()}/day`].filter(Boolean).join(" · ") || "—"}
                </TD>
                <TD className="text-sm">
                  {p.last_tested_at ? (
                    <span className={p.last_test_ok ? "text-success" : "text-danger"}>
                      {p.last_test_ok ? "Passed" : "Failed"} <span className="text-fg-muted">· {fmtRelative(p.last_tested_at)}</span>
                    </span>
                  ) : (
                    <span className="text-fg-muted">Never tested</span>
                  )}
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
        <LoadMore hasNext={!!list.hasNextPage} loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()} />
      </Card>
      <CreateProviderDialog open={creating} onClose={() => setCreating(false)} />
    </>
  );
}

function CreateProviderDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const create = useAction((body: unknown) => api.post<Provider>("/admin/providers", body), {
    invalidate: [["providers"]],
    success: "Provider added",
    onSuccess: (p) => {
      onClose();
      navigate(`/providers/${p.id}`);
    },
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add provider"
      size="lg"
      description="Only add providers you are authorised to send through. Limits must match the provider's policy."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="new-provider" loading={create.isPending}>
            Add provider
          </Button>
        </>
      }
    >
      <form
        id="new-provider"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(providerBody(new FormData(e.currentTarget), true));
        }}
      >
        <ProviderFields withPassword />
      </form>
    </Dialog>
  );
}
