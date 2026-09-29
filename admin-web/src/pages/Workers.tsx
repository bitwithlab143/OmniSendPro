import {
  Checkbox,
  Badge,
  Button,
  Card,
  CopyField,
  Dialog,
  EmptyRow,
  ErrorBanner,
  Field,
  Input,
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
  fmtRate,
  fmtRelative,
  useAction,
  useAuth,
  useConfirm,
  usePaged,
  type Page,
} from "@omnisend/web-shared";
import { KeyRound, Plus, Power, PowerOff } from "lucide-react";
import { useState } from "react";

import { api } from "../api";
import type { Worker } from "../types";

interface Credential {
  worker: Worker;
  credential: string;
  note: string;
}

export function WorkersPage() {
  const { can } = useAuth();
  const confirm = useConfirm();
  const writable = can("workers.write");
  const [creating, setCreating] = useState(false);
  const [credential, setCredential] = useState<Credential | null>(null);
  const list = usePaged<Worker>(["workers"], (cursor) => api.get<Page<Worker>>("/admin/workers", { cursor }), { refetchInterval: 5000 });
  const inv = { invalidate: [["workers"], ["dashboard"]] };
  const toggle = useAction(({ id, action }: { id: string; action: "enable" | "disable" }) => api.post<Worker>(`/admin/workers/${id}/${action}`), {
    ...inv,
    success: (w) => `${w.name} ${w.disabled ? "disabled" : "enabled"}`,
  });
  const rotate = useAction((id: string) => api.post<Credential>(`/admin/workers/${id}/rotate-credential`), { ...inv, onSuccess: setCredential });

  return (
    <>
      <PageHeader
        title="Workers"
        description="Delivery nodes. Workers pull jobs from the queue; add capacity by provisioning more."
        actions={
          writable && (
            <Button onClick={() => setCreating(true)}>
              <Plus /> Provision worker
            </Button>
          )
        }
      />
      {list.error && <ErrorBanner message={errorMessage(list.error)} />}
      <Card>
        <Table>
          <THead>
            <tr>
              <TH>Worker</TH>
              <TH>Status</TH>
              <TH className="text-right">CPU / Mem</TH>
              <TH className="text-right">Jobs</TH>
              <TH className="text-right">Rate / capacity</TH>
              <TH>Version</TH>
              <TH>Heartbeat</TH>
              {writable && <TH className="text-right">Actions</TH>}
            </tr>
          </THead>
          <tbody>
            {list.isLoading && <LoadingRows colSpan={8} />}
            {!list.isLoading && list.items.length === 0 && <EmptyRow colSpan={8}>No workers yet. Provision one and start it with its credential.</EmptyRow>}
            {list.items.map((w) => (
              <TR key={w.id}>
                <TD>
                  <div className="flex items-center gap-2 font-medium">
                    {w.name}
                    {w.is_pool && <Badge tone="info">Pool</Badge>}
                    {w.pool_id && <Badge>Autoscaled</Badge>}
                  </div>
                  <div className="text-xs text-fg-muted">
                    {w.worker_id}
                    {w.hostname ? ` · ${w.hostname}` : ""}
                  </div>
                </TD>
                <TD>{w.disabled ? <StatusBadge status="disabled" /> : <StatusBadge status={w.status} />}</TD>
                <TD className="text-right tabular text-fg-secondary">
                  {w.cpu == null ? "—" : `${Math.round(w.cpu)}%`} / {w.memory == null ? "—" : `${Math.round(w.memory)}%`}
                </TD>
                <TD className="text-right tabular">
                  {w.active_jobs} / {w.max_concurrent_jobs}
                </TD>
                <TD className="text-right tabular">
                  {fmtRate(w.current_rate)} / {w.capacity}
                </TD>
                <TD className="text-fg-secondary">{w.version ?? "—"}</TD>
                <TD className="text-fg-secondary">{fmtRelative(w.last_heartbeat_at)}</TD>
                {writable && (
                  <TD>
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        title="Rotate credential"
                        aria-label={`Rotate credential for ${w.name}`}
                        onClick={async () => {
                          if (await confirm({ title: "Rotate worker credential?", message: "The current credential stops working immediately. Update the worker's secret before its token expires.", confirmLabel: "Rotate", danger: true }))
                            rotate.mutate(w.id);
                        }}
                      >
                        <KeyRound />
                      </Button>
                      {w.disabled ? (
                        <Button variant="ghost" size="icon-sm" title="Enable" aria-label={`Enable ${w.name}`} onClick={() => toggle.mutate({ id: w.id, action: "enable" })}>
                          <Power />
                        </Button>
                      ) : (
                        <Button
                          variant="danger-ghost"
                          size="icon-sm"
                          title="Disable"
                          aria-label={`Disable ${w.name}`}
                          onClick={async () => {
                            if (await confirm({ title: `Disable ${w.name}?`, message: "It can no longer authenticate or claim jobs. In-flight jobs are recovered after their lease expires.", confirmLabel: "Disable", danger: true }))
                              toggle.mutate({ id: w.id, action: "disable" });
                          }}
                        >
                          <PowerOff />
                        </Button>
                      )}
                    </div>
                  </TD>
                )}
              </TR>
            ))}
          </tbody>
        </Table>
        <LoadMore hasNext={!!list.hasNextPage} loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()} />
      </Card>
      {creating && (
        <ProvisionDialog
          onClose={() => setCreating(false)}
          onCreated={(c) => {
            setCreating(false);
            setCredential(c);
          }}
        />
      )}
      <Dialog open={!!credential} onClose={() => setCredential(null)} title="Worker credential" description="Copy it now — it is shown only once and cannot be recovered.">
        {credential && (
          <div className="space-y-4 text-sm">
            <Field label="WORKER_ID">{() => <CopyField value={credential.worker.worker_id} />}</Field>
            <Field label="WORKER_CREDENTIAL">{() => <CopyField value={credential.credential} />}</Field>
            <p className="text-fg-secondary">
              {credential.note} Set WORKER_API_URL to this control plane's public URL.
              {credential.worker.is_pool && " This is a pool: start as many instances as you need with the same values; scale on the omnisend_workers_desired metric."}
            </p>
          </div>
        )}
      </Dialog>
    </>
  );
}

function ProvisionDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (c: Credential) => void }) {
  const create = useAction((body: unknown) => api.post<Credential>("/admin/workers", body), { invalidate: [["workers"]], onSuccess: onCreated });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Provision worker"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="provision" loading={create.isPending}>
            Provision
          </Button>
        </>
      }
    >
      <form
        id="provision"
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          create.mutate({
            worker_id: String(f.get("worker_id")).trim().toLowerCase(),
            name: f.get("name"),
            capacity: Number(f.get("capacity")),
            max_concurrent_jobs: Number(f.get("max_concurrent_jobs")),
            pool: f.get("pool") === "on",
          });
        }}
      >
        <Field label="Worker ID" hint="Lowercase letters, digits and dashes, e.g. worker-dhaka-01">
          {(id, d) => <Input id={id} name="worker_id" required pattern="[a-z0-9][a-z0-9\-]{1,62}" aria-describedby={d} />}
        </Field>
        <Field label="Display name">{(id) => <Input id={id} name="name" required />}</Field>
        <Field label="Capacity (msgs/sec)">{(id) => <Input id={id} name="capacity" type="number" min={1} defaultValue={80} required />}</Field>
        <Field label="Max concurrent jobs">{(id) => <Input id={id} name="max_concurrent_jobs" type="number" min={1} max={256} defaultValue={4} required />}</Field>
        <Checkbox
          className="sm:col-span-2"
          name="pool"
          label="Pool for autoscaling: any number of worker instances share this credential; each registers under its own WORKER_INSTANCE (default: hostname)."
        />
      </form>
    </Dialog>
  );
}
