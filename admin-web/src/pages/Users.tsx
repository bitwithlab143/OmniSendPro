import {
  Badge,
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
  Tabs,
  TD,
  TH,
  THead,
  TR,
  errorMessage,
  fmtRelative,
  titleCase,
  useAction,
  useAuth,
  useConfirm,
  usePaged,
  type Page,
} from "@omnisend/web-shared";
import { KeyRound, Pencil, Plus, Search, ShieldOff, SlidersHorizontal, UserCheck, UserX } from "lucide-react";
import { useState, type FormEvent } from "react";

import { api } from "../api";
import type { Limits, User } from "../types";

const ROLES = ["USER", "OPERATOR", "VIEWER", "ADMIN", "SUPER_ADMIN"];
const LIMIT_FIELDS: [keyof Limits, string, string][] = [
  ["daily_limit", "Daily limit", "Messages per day"],
  ["hourly_limit", "Hourly limit", "Messages per hour"],
  ["per_second_limit", "Per-second limit", "Messages per second"],
  ["max_batch_size", "Max batch size", "Upper bound for batch size"],
  ["max_recipients_per_campaign", "Max recipients / campaign", "Per campaign"],
];

function numOrNull(v: FormDataEntryValue | null): number | null {
  const s = String(v ?? "").trim();
  return s === "" ? null : Number(s);
}

export function UsersPage() {
  const { can, user: me } = useAuth();
  const confirm = useConfirm();
  const [status, setStatus] = useState<"" | "active" | "suspended" | "disabled">("");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [limitsFor, setLimitsFor] = useState<User | null>(null);
  const [passwordFor, setPasswordFor] = useState<User | null>(null);
  const writable = can("users.write");

  const list = usePaged<User>(["users", status, search], (cursor) =>
    api.get<Page<User>>("/admin/users", { status, q: search, cursor, limit: 50 }),
  );
  const inv = { invalidate: [["users"]] };
  const setUserStatus = useAction(
    ({ id, action }: { id: string; action: "suspend" | "activate" }) => api.post<User>(`/admin/users/${id}/${action}`),
    { ...inv, success: (u) => `${u.username} is now ${u.status}` },
  );
  const reset2fa = useAction((id: string) => api.post<User>(`/admin/users/${id}/reset-2fa`), { ...inv, success: "Two-factor authentication reset" });

  return (
    <>
      <PageHeader
        title="Users"
        description="Accounts, roles and sending limits."
        actions={
          writable && (
            <Button onClick={() => setCreating(true)}>
              <Plus /> New user
            </Button>
          )
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs
          value={status}
          onChange={setStatus}
          options={[
            { value: "", label: "All" },
            { value: "active", label: "Active" },
            { value: "suspended", label: "Suspended" },
            { value: "disabled", label: "Disabled" },
          ]}
        />
        <form
          className="relative ml-auto w-full sm:w-72"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(q);
          }}
        >
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-muted" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, email, username" className="pl-9" aria-label="Search users" />
        </form>
      </div>
      {list.error && <ErrorBanner message={errorMessage(list.error)} onRetry={() => list.refetch()} />}
      <Card>
        <Table>
          <THead>
            <tr>
              <TH>User</TH>
              <TH>Role</TH>
              <TH>Status</TH>
              <TH>Limits</TH>
              <TH>Last login</TH>
              {writable && <TH className="text-right">Actions</TH>}
            </tr>
          </THead>
          <tbody>
            {list.isLoading && <LoadingRows colSpan={6} />}
            {!list.isLoading && list.items.length === 0 && <EmptyRow colSpan={6}>No users match.</EmptyRow>}
            {list.items.map((u) => (
              <TR key={u.id}>
                <TD>
                  <div className="font-medium">{u.full_name || u.username}</div>
                  <div className="text-xs text-fg-muted">
                    {u.username} · {u.email}
                  </div>
                </TD>
                <TD>
                  <div className="flex items-center gap-1.5">
                    <Badge>{titleCase(u.role)}</Badge>
                    {u.totp_enabled && <Badge tone="info">2FA</Badge>}
                  </div>
                </TD>
                <TD>
                  <StatusBadge status={u.status} />
                  {u.locked_until && new Date(u.locked_until) > new Date() && (
                    <Badge tone="warning" className="ml-1">
                      Locked
                    </Badge>
                  )}
                </TD>
                <TD className="text-xs text-fg-secondary tabular">
                  {u.limits?.daily_limit ? `${u.limits.daily_limit.toLocaleString()}/day` : "—"}
                  {u.limits?.per_second_limit ? ` · ${u.limits.per_second_limit}/s` : ""}
                </TD>
                <TD className="text-fg-secondary">{fmtRelative(u.last_login_at)}</TD>
                {writable && (
                  <TD>
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="icon-sm" title="Edit" aria-label={`Edit ${u.username}`} onClick={() => setEditing(u)}>
                        <Pencil />
                      </Button>
                      <Button variant="ghost" size="icon-sm" title="Limits" aria-label={`Limits for ${u.username}`} onClick={() => setLimitsFor(u)}>
                        <SlidersHorizontal />
                      </Button>
                      <Button variant="ghost" size="icon-sm" title="Reset password" aria-label={`Reset password for ${u.username}`} onClick={() => setPasswordFor(u)}>
                        <KeyRound />
                      </Button>
                      {u.totp_enabled && (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          title="Reset 2FA"
                          aria-label={`Reset 2FA for ${u.username}`}
                          onClick={async () => {
                            if (await confirm({ title: "Reset two-factor authentication?", message: `${u.username} will have to enrol again at next sign-in. Active sessions are signed out.`, confirmLabel: "Reset 2FA", danger: true }))
                              reset2fa.mutate(u.id);
                          }}
                        >
                          <ShieldOff />
                        </Button>
                      )}
                      {u.id !== me?.id &&
                        (u.status === "active" ? (
                          <Button
                            variant="danger-ghost"
                            size="icon-sm"
                            title="Suspend"
                            aria-label={`Suspend ${u.username}`}
                            onClick={async () => {
                              if (await confirm({ title: `Suspend ${u.username}?`, message: "The user is signed out immediately and cannot send until reactivated.", confirmLabel: "Suspend", danger: true }))
                                setUserStatus.mutate({ id: u.id, action: "suspend" });
                            }}
                          >
                            <UserX />
                          </Button>
                        ) : (
                          <Button variant="ghost" size="icon-sm" title="Activate" aria-label={`Activate ${u.username}`} onClick={() => setUserStatus.mutate({ id: u.id, action: "activate" })}>
                            <UserCheck />
                          </Button>
                        ))}
                    </div>
                  </TD>
                )}
              </TR>
            ))}
          </tbody>
        </Table>
        <LoadMore hasNext={!!list.hasNextPage} loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()} />
      </Card>

      <CreateUserDialog open={creating} onClose={() => setCreating(false)} />
      {editing && <EditUserDialog user={editing} onClose={() => setEditing(null)} />}
      {limitsFor && <LimitsDialog user={limitsFor} onClose={() => setLimitsFor(null)} />}
      {passwordFor && <PasswordDialog user={passwordFor} onClose={() => setPasswordFor(null)} />}
    </>
  );
}

function CreateUserDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const create = useAction((body: unknown) => api.post<User>("/admin/users", body), {
    invalidate: [["users"]],
    success: (u) => `Created ${u.username}`,
    onSuccess: onClose,
  });
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setError(null);
    create.mutate(
      {
        email: f.get("email"),
        username: f.get("username"),
        full_name: f.get("full_name") || null,
        password: f.get("password"),
        role: f.get("role"),
        limits: { daily_limit: numOrNull(f.get("daily_limit")), hourly_limit: null, per_second_limit: numOrNull(f.get("per_second_limit")), max_batch_size: null, max_recipients_per_campaign: null },
      },
      { onError: (err) => setError(errorMessage(err)) },
    );
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New user"
      description="The user signs in to the User Panel with these credentials."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="create-user" loading={create.isPending}>
            Create user
          </Button>
        </>
      }
    >
      <form id="create-user" onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        {error && <div className="sm:col-span-2"><ErrorBanner message={error} /></div>}
        <Field label="Email">{(id) => <Input id={id} name="email" type="email" required />}</Field>
        <Field label="Username" hint="3–64 characters: letters, digits, . _ -">{(id, d) => <Input id={id} name="username" required aria-describedby={d} pattern="[A-Za-z0-9_.\-]{3,64}" />}</Field>
        <Field label="Full name" optional>{(id) => <Input id={id} name="full_name" />}</Field>
        <Field label="Role">
          {(id) => (
            <Select id={id} name="role" defaultValue="USER">
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {titleCase(r)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Initial password" hint="At least 12 characters, 3 of: lower, upper, digit, symbol" className="sm:col-span-2">
          {(id, d) => <Input id={id} name="password" type="password" autoComplete="new-password" minLength={12} required aria-describedby={d} />}
        </Field>
        <Field label="Daily limit" optional>{(id) => <Input id={id} name="daily_limit" type="number" min={0} />}</Field>
        <Field label="Per-second limit" optional>{(id) => <Input id={id} name="per_second_limit" type="number" min={1} />}</Field>
      </form>
    </Dialog>
  );
}

function EditUserDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const save = useAction((body: unknown) => api.patch<User>(`/admin/users/${user.id}`, body), { invalidate: [["users"]], success: "User updated", onSuccess: onClose });
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Edit ${user.username}`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="edit-user" loading={save.isPending}>
            Save
          </Button>
        </>
      }
    >
      <form
        id="edit-user"
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          save.mutate({ email: f.get("email"), username: f.get("username"), full_name: f.get("full_name") || null, role: f.get("role") });
        }}
      >
        <Field label="Email">{(id) => <Input id={id} name="email" type="email" defaultValue={user.email} required />}</Field>
        <Field label="Username">{(id) => <Input id={id} name="username" defaultValue={user.username} required />}</Field>
        <Field label="Full name" optional>{(id) => <Input id={id} name="full_name" defaultValue={user.full_name ?? ""} />}</Field>
        <Field label="Role" hint="Changing the role signs the user out of all sessions.">
          {(id, d) => (
            <Select id={id} name="role" defaultValue={user.role} aria-describedby={d}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {titleCase(r)}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </form>
    </Dialog>
  );
}

function LimitsDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const save = useAction((body: unknown) => api.put<Limits>(`/admin/users/${user.id}/limits`, body), { invalidate: [["users"]], success: "Limits saved", onSuccess: onClose });
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Sending limits — ${user.username}`}
      description="Leave a field empty for no limit. Provider limits still apply."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="limits" loading={save.isPending}>
            Save limits
          </Button>
        </>
      }
    >
      <form
        id="limits"
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          save.mutate(Object.fromEntries(LIMIT_FIELDS.map(([k]) => [k, numOrNull(f.get(k))])));
        }}
      >
        {LIMIT_FIELDS.map(([k, label, hint]) => (
          <Field key={k} label={label} hint={hint}>
            {(id, d) => <Input id={id} name={k} type="number" min={k === "per_second_limit" || k.startsWith("max") ? 1 : 0} defaultValue={user.limits?.[k] ?? ""} aria-describedby={d} />}
          </Field>
        ))}
      </form>
    </Dialog>
  );
}

function PasswordDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const save = useAction((password: string) => api.post(`/admin/users/${user.id}/password`, { password }), { success: "Password reset — the user was signed out", onSuccess: onClose });
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Reset password — ${user.username}`}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="pw" loading={save.isPending}>
            Reset password
          </Button>
        </>
      }
    >
      <form
        id="pw"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(String(new FormData(e.currentTarget).get("password")));
        }}
      >
        <Field label="New password" hint="At least 12 characters, 3 of: lower, upper, digit, symbol">
          {(id, d) => <Input id={id} name="password" type="password" autoComplete="new-password" minLength={12} required aria-describedby={d} />}
        </Field>
      </form>
    </Dialog>
  );
}
