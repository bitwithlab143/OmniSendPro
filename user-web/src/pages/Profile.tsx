import {
  ApiKeyTable,
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CopyField,
  CreateApiKeyDialog,
  DefinitionList,
  ErrorBanner,
  Field,
  Input,
  NewApiKeyDialog,
  PageHeader,
  apiKeyBody,
  errorMessage,
  fmtDate,
  useAction,
  useAuth,
  useConfirm,
  type ApiKey,
  type ApiKeyCreated,
  type Me,
} from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useState, type FormEvent } from "react";

import { api } from "../api";

export function ProfilePage() {
  const { user, setUser, logout } = useAuth();
  const [setup, setSetup] = useState<{ otpauth_uri: string; totp_secret: string } | null>(null);
  const [code, setCode] = useState("");
  const [pwError, setPwError] = useState<string | null>(null);
  const saveName = useAction((full_name: string) => api.patch<Me>("/user/profile", { full_name: full_name || null }), { success: "Profile saved", onSuccess: setUser });
  const changePw = useAction((body: unknown) => api.post("/user/profile/password", body), {
    success: "Password changed — please sign in again",
    onSuccess: () => void logout(),
  });
  const begin2fa = useAction(() => api.post<{ otpauth_uri: string; totp_secret: string }>("/user/profile/2fa/setup"), { onSuccess: setSetup });
  const enable2fa = useAction((c: string) => api.post<Me>("/user/profile/2fa/enable", { code: c }), {
    success: "Two-factor authentication enabled",
    onSuccess: (u) => {
      setUser(u);
      setSetup(null);
      setCode("");
    },
  });
  const disable2fa = useAction((c: string) => api.post<Me>("/user/profile/2fa/disable", { code: c }), {
    success: "Two-factor authentication disabled",
    onSuccess: (u) => {
      setUser(u);
      setCode("");
    },
  });
  if (!user) return null;

  function submitPassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (f.get("new_password") !== f.get("confirm")) {
      setPwError("The new passwords do not match");
      return;
    }
    setPwError(null);
    changePw.mutate({ current_password: f.get("current_password"), new_password: f.get("new_password") });
  }

  return (
    <>
      <PageHeader title="Profile" description="Your account and security settings." />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Account" />
          <CardBody className="space-y-5">
            <DefinitionList items={[["Email", user.email], ["Username", user.username], ["Last sign-in", fmtDate(user.last_login_at)]]} />
            <form
              className="flex items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                saveName.mutate(String(new FormData(e.currentTarget).get("full_name") ?? ""));
              }}
            >
              <Field label="Display name" className="flex-1">{(id) => <Input id={id} name="full_name" defaultValue={user.full_name ?? ""} maxLength={128} />}</Field>
              <Button type="submit" variant="secondary" loading={saveName.isPending}>
                Save
              </Button>
            </form>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Password" description="Changing it signs you out everywhere." />
          <CardBody>
            <form className="space-y-4" onSubmit={submitPassword}>
              {pwError && <ErrorBanner message={pwError} />}
              {changePw.error && <ErrorBanner message={errorMessage(changePw.error)} />}
              <Field label="Current password">{(id) => <Input id={id} name="current_password" type="password" autoComplete="current-password" required />}</Field>
              <Field label="New password" hint="At least 12 characters with 3 of: lowercase, uppercase, digit, symbol">
                {(id, d) => <Input id={id} name="new_password" type="password" autoComplete="new-password" minLength={12} required aria-describedby={d} />}
              </Field>
              <Field label="Confirm new password">{(id) => <Input id={id} name="confirm" type="password" autoComplete="new-password" minLength={12} required />}</Field>
              <Button type="submit" loading={changePw.isPending}>
                Change password
              </Button>
            </form>
          </CardBody>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader
            title="Two-factor authentication"
            description="Protect your account with a code from an authenticator app."
            actions={user.totp_enabled ? <Badge tone="success">Enabled</Badge> : <Badge>Off</Badge>}
          />
          <CardBody>
            {!user.totp_enabled && !setup && (
              <Button onClick={() => begin2fa.mutate(undefined)} loading={begin2fa.isPending}>
                Set up two-factor authentication
              </Button>
            )}
            {(setup || user.totp_enabled) && (
              <div className="flex flex-col gap-6 sm:flex-row sm:items-start">
                {setup && (
                  <div className="space-y-3">
                    <div className="w-fit rounded-lg bg-white p-3">
                      <QRCodeSVG value={setup.otpauth_uri} size={152} />
                    </div>
                    <CopyField value={setup.totp_secret} label="Setup key" />
                  </div>
                )}
                <form
                  className="flex max-w-xs flex-1 flex-col gap-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    (user.totp_enabled ? disable2fa : enable2fa).mutate(code);
                  }}
                >
                  <Field label={user.totp_enabled ? "Enter a code to turn it off" : "Enter the 6-digit code"}>
                    {(id) => (
                      <Input
                        id={id}
                        value={code}
                        onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        className="tabular tracking-[0.3em]"
                        required
                      />
                    )}
                  </Field>
                  <Button type="submit" variant={user.totp_enabled ? "danger" : "primary"} disabled={code.length !== 6} loading={enable2fa.isPending || disable2fa.isPending}>
                    {user.totp_enabled ? "Turn off" : "Verify and enable"}
                  </Button>
                </form>
              </div>
            )}
          </CardBody>
        </Card>
      </div>
      <ApiKeysCard />
    </>
  );
}

function ApiKeysCard() {
  const confirm = useConfirm();
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api.get<ApiKey[]>("/user/api-keys") });
  const create = useAction((body: unknown) => api.post<ApiKeyCreated>("/user/api-keys", body), {
    invalidate: [["api-keys"]],
    onSuccess: (k) => {
      setCreating(false);
      setCreated(k);
    },
  });
  const revoke = useAction((id: string) => api.del(`/user/api-keys/${id}`), { invalidate: [["api-keys"]], success: "API key revoked" });
  return (
    <Card className="mt-6">
      <CardHeader
        title="API keys"
        description="Let your own tools (CRM, scripts) manage campaigns through the API. Keys never have access to your account settings."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            <KeyRound /> Create key
          </Button>
        }
      />
      {keys.error && <ErrorBanner message={errorMessage(keys.error)} />}
      <ApiKeyTable
        keys={keys.data}
        loading={keys.isLoading}
        onRevoke={async (k) => {
          if (await confirm({ title: `Revoke “${k.name}”?`, message: "Integrations using this key stop working immediately.", confirmLabel: "Revoke", danger: true }))
            revoke.mutate(k.id);
        }}
      />
      {creating && <CreateApiKeyDialog onClose={() => setCreating(false)} pending={create.isPending} onSubmit={(f) => create.mutate(apiKeyBody(f))} />}
      <NewApiKeyDialog created={created} onClose={() => setCreated(null)} />
    </Card>
  );
}
