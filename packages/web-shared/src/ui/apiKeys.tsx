import { KeyRound, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { fmtDate, fmtRelative } from "../lib/format";
import { Badge } from "./badge";
import { Button } from "./button";
import { Dialog } from "./dialog";
import { Checkbox, Field, Input, Select } from "./form";
import { CopyField } from "./misc";
import { EmptyRow, LoadingRows, Table, TD, TH, THead, TR } from "./table";

/** API keys for the User API (design DS-17). */
export interface ApiKey {
  id: string;
  user_id: string;
  username: string | null;
  name: string;
  prefix: string;
  scopes: string[];
  created_by: string | null;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
  active: boolean;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export const API_KEY_SCOPES: { scope: string; label: string }[] = [
  { scope: "campaigns.read", label: "Read campaigns, recipients and reports of campaigns" },
  { scope: "campaigns.write", label: "Create and edit campaigns, upload recipients" },
  { scope: "campaigns.start", label: "Start and resume campaigns" },
  { scope: "campaigns.stop", label: "Pause and cancel campaigns" },
  { scope: "providers.read", label: "List assigned providers" },
  { scope: "reports.read", label: "Dashboard and reports" },
];

export function apiKeyBody(f: FormData): Record<string, unknown> {
  const days = String(f.get("expires_in_days") ?? "");
  return {
    name: String(f.get("name") ?? "").trim(),
    scopes: f.getAll("scopes").map(String),
    expires_in_days: days ? Number(days) : null,
  };
}

function keyState(k: ApiKey): ReactNode {
  if (k.revoked_at) return <Badge tone="danger">Revoked</Badge>;
  if (!k.active) return <Badge tone="warning">Expired</Badge>;
  return <Badge tone="success">Active</Badge>;
}

export function ApiKeyTable({
  keys,
  loading,
  showOwner,
  onRevoke,
}: {
  keys: ApiKey[] | undefined;
  loading?: boolean;
  showOwner?: boolean;
  onRevoke?: (key: ApiKey) => void;
}) {
  const cols = 6 + (showOwner ? 1 : 0) + (onRevoke ? 1 : 0);
  return (
    <Table>
      <THead>
        <tr>
          <TH>Name</TH>
          {showOwner && <TH>User</TH>}
          <TH>Key</TH>
          <TH>Scopes</TH>
          <TH>Status</TH>
          <TH>Last used</TH>
          <TH>Expires</TH>
          {onRevoke && <TH className="text-right">Actions</TH>}
        </tr>
      </THead>
      <tbody>
        {loading && <LoadingRows colSpan={cols} />}
        {!loading && keys?.length === 0 && <EmptyRow colSpan={cols}>No API keys yet.</EmptyRow>}
        {keys?.map((k) => (
          <TR key={k.id}>
            <TD className="font-medium">{k.name}</TD>
            {showOwner && <TD>{k.username ?? "—"}</TD>}
            <TD>
              <code className="text-xs text-fg-secondary">osk_{k.prefix}_…</code>
            </TD>
            <TD className="max-w-xs text-xs text-fg-secondary">{k.scopes.join(", ")}</TD>
            <TD>{keyState(k)}</TD>
            <TD className="text-fg-secondary" title={k.last_used_ip ?? undefined}>
              {k.last_used_at ? fmtRelative(k.last_used_at) : "Never"}
            </TD>
            <TD className="text-fg-secondary">{k.expires_at ? fmtDate(k.expires_at) : "Never"}</TD>
            {onRevoke && (
              <TD className="text-right">
                {!k.revoked_at && (
                  <Button variant="danger-ghost" size="sm" onClick={() => onRevoke(k)} aria-label={`Revoke ${k.name}`}>
                    <Trash2 /> Revoke
                  </Button>
                )}
              </TD>
            )}
          </TR>
        ))}
      </tbody>
    </Table>
  );
}

/** Create form. `extra` renders additional fields (e.g. the owner picker in the admin panel). */
export function CreateApiKeyDialog({
  onClose,
  onSubmit,
  pending,
  extra,
}: {
  onClose: () => void;
  onSubmit: (form: FormData) => void;
  pending?: boolean;
  extra?: ReactNode;
}) {
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog
      open
      onClose={onClose}
      title="Create API key"
      description="Keys work only on the User API and never on account settings. Grant only the scopes the integration needs."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="create-api-key" loading={pending}>
            <KeyRound /> Create key
          </Button>
        </>
      }
    >
      <form
        id="create-api-key"
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          if (f.getAll("scopes").length === 0) {
            setError("Select at least one scope");
            return;
          }
          setError(null);
          onSubmit(f);
        }}
      >
        {extra}
        <Field label="Name" hint="Where the key is used, e.g. “CRM sync”.">
          {(id, d) => <Input id={id} name="name" maxLength={100} required aria-describedby={d} />}
        </Field>
        <fieldset className="space-y-2">
          <legend className="mb-1 text-sm font-medium">Scopes</legend>
          {API_KEY_SCOPES.map((s) => (
            <Checkbox key={s.scope} name="scopes" value={s.scope} defaultChecked={s.scope.endsWith(".read")} label={<><code className="text-xs">{s.scope}</code> — {s.label}</>} />
          ))}
          {error && <p className="text-xs text-danger">{error}</p>}
        </fieldset>
        <Field label="Expires">
          {(id) => (
            <Select id={id} name="expires_in_days" defaultValue="90">
              <option value="30">In 30 days</option>
              <option value="90">In 90 days</option>
              <option value="365">In 1 year</option>
              <option value="">Never</option>
            </Select>
          )}
        </Field>
      </form>
    </Dialog>
  );
}

export function NewApiKeyDialog({ created, onClose }: { created: ApiKeyCreated | null; onClose: () => void }) {
  return (
    <Dialog open={!!created} onClose={onClose} title="API key created" description="Copy it now — it is shown only once and cannot be recovered.">
      {created && (
        <div className="space-y-4 text-sm">
          <Field label={created.name}>{() => <CopyField value={created.key} />}</Field>
          <p className="text-fg-secondary">
            Send it as <code className="text-xs">Authorization: Bearer {"<key>"}</code> to <code className="text-xs">/api/v1/user/…</code>. Treat it like a password;
            revoke it if it leaks.
          </p>
        </div>
      )}
    </Dialog>
  );
}
